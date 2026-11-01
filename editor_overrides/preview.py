from pathlib import Path
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import math
import copy
import json
import sys
from PySide6.QtCore import QElapsedTimer, QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal, QSize
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPen, QBrush, QPolygonF
from PySide6.QtMultimedia import QAudioBufferOutput, QAudioFormat, QAudioOutput, QMediaDevices, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QVBoxLayout, QWidget
from .model import Clip, Library
from .scene import leaves_at, channel_key
from .audio import AudioStream, Compressor, apply_gain, decode_pcm
from .async_images import AsyncImages


class Canvas(QWidget):
    clipClicked = Signal(str)
    transformEdited = Signal(str, float, float, float, float, float, float)

    def __init__(self, preview):
        super().__init__(preview)
        self.preview = preview
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('プレビューの素材をクリックして選択。中央をドラッグして移動、四隅・上下左右でサイズ変更、上のバーで回転します。Shiftを押しながらサイズ変更すると縦横比を個別に調整できます。')
        self._gesture = None

    def _mapping(self):
        sequence = self.preview.sequence
        ratio = min(self.width() / sequence.width, self.height() / sequence.height)
        return ratio, (self.width() - sequence.width * ratio) / 2, (self.height() - sequence.height * ratio) / 2

    def _geometry(self, clip):
        sequence = self.preview.sequence
        asset = self.preview.library.assets[clip.asset_id]
        frame = self.preview.frame
        source = clip.source_in + frame - clip.start
        tx, ty, scale, opacity = self.preview.transforms.get(clip.id, clip.transform_at(source))
        sx, sy, rotation = self.preview.extra_transforms.get(
            clip.id, (clip.value_at('scale_x', source), clip.value_at('scale_y', source), clip.value_at('rotation', source)))
        iw, ih = max(1, asset.width or sequence.width), max(1, asset.height or sequence.height)
        factor = min(sequence.width / iw, sequence.height / ih) * scale
        center = (sequence.width / 2 + tx, sequence.height / 2 + ty)
        width, height = iw * factor * sx, ih * factor * sy
        return center, width, height, rotation, (tx, ty, scale, opacity, sx, sy, rotation)

    @staticmethod
    def _rotate(x, y, angle):
        radians = math.radians(angle)
        return x * math.cos(radians) - y * math.sin(radians), x * math.sin(radians) + y * math.cos(radians)

    def _widget_point(self, point):
        ratio, ox, oy = self._mapping()
        return QPointF(ox + point[0] * ratio, oy + point[1] * ratio)

    def _handles(self, center, width, height, rotation):
        points = {'nw':(-width/2,-height/2), 'n':(0,-height/2), 'ne':(width/2,-height/2),
                  'e':(width/2,0), 'se':(width/2,height/2), 's':(0,height/2),
                  'sw':(-width/2,height/2), 'w':(-width/2,0), 'move':(0,0)}
        result = {name:self._widget_point((center[0]+self._rotate(x,y,rotation)[0], center[1]+self._rotate(x,y,rotation)[1]))
                  for name,(x,y) in points.items()}
        ratio, _, _ = self._mapping()
        rx, ry = self._rotate(0, -30/max(ratio, .0001), rotation)
        top = self._rotate(0, -height/2, rotation)
        result['rotate'] = self._widget_point((center[0]+top[0]+rx, center[1]+top[1]+ry))
        return result

    def videoSink(self):
        return self.preview.main_channel.video

    def composite(self, width, height):
        return self.preview.compose(width, height)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            selected = next((c for c in self.preview.sequence.active_clips(self.preview.frame)
                             if c.id == getattr(self.preview, 'editing_clip_id', '')), None)
            selected_handle = None
            if selected and self.preview.library.assets[selected.asset_id].has_video:
                handles=self._handles(*self._geometry(selected)[:4])
                distances=[(math.hypot(event.position().x()-pt.x(),event.position().y()-pt.y()),name)
                           for name,pt in handles.items() if name!='move']
                distance, selected_handle = min(distances, default=(float('inf'), None))
                if distance > 22:selected_handle=None
            # Handles on the selected item take precedence over another visible
            # layer underneath them. This keeps the small corner targets grabbable.
            clip = selected if selected_handle else self.preview.clip_at(event.position())
            if clip is not None:
                if clip.id != getattr(self.preview, 'editing_clip_id', ''):
                    self.preview.editing_clip_id = clip.id
                    self.clipClicked.emit(clip.id)
                center, width, height, rotation, values = self._geometry(clip)
                handles = self._handles(center, width, height, rotation)
                ratio, ox, oy = self._mapping()
                pointer_doc=((event.position().x()-ox)/ratio,(event.position().y()-oy)/ratio)
                local = self._to_local(pointer_doc, center, rotation)
                mode = selected_handle or next((name for name, point in handles.items()
                             if math.hypot(event.position().x()-point.x(), event.position().y()-point.y()) <= 22), None)
                if mode is None and abs(local[0]) <= width/2 and abs(local[1]) <= height/2:
                    mode = 'move'
                if mode:
                    self._gesture = {'clip':clip.id,'mode':mode,'values':values,'center':center,
                        'start':((event.position().x()-ox)/ratio,(event.position().y()-oy)/ratio),
                        'press_local':local,'size':(width,height)}
                    self.grabMouse()
                    self.setCursor(Qt.CursorShape.ClosedHandCursor if mode == 'move' else Qt.CursorShape.CrossCursor)
                event.accept(); self.update(); return
            self.preview.editing_clip_id = ''
            self.update()
        super().mousePressEvent(event)

    @staticmethod
    def _to_local(point, center, rotation):
        # Canvas coordinates have already been converted to sequence pixels by the caller.
        return Canvas._rotate(point[0]-center[0], point[1]-center[1], -rotation)

    def mouseMoveEvent(self, event):
        if not self._gesture:
            selected=next((c for c in self.preview.sequence.active_clips(self.preview.frame)
                           if c.id==getattr(self.preview,'editing_clip_id','')),None)
            if selected and self.preview.library.assets[selected.asset_id].has_video:
                handles=self._handles(*self._geometry(selected)[:4])
                distances=[(math.hypot(event.position().x()-pt.x(),event.position().y()-pt.y()),name)
                           for name,pt in handles.items()]
                distance,mode=min(distances,default=(float('inf'),None))
                if distance<=22:
                    cursor={'n':Qt.CursorShape.SizeVerCursor,'s':Qt.CursorShape.SizeVerCursor,
                            'e':Qt.CursorShape.SizeHorCursor,'w':Qt.CursorShape.SizeHorCursor,
                            'ne':Qt.CursorShape.SizeBDiagCursor,'sw':Qt.CursorShape.SizeBDiagCursor,
                            'nw':Qt.CursorShape.SizeFDiagCursor,'se':Qt.CursorShape.SizeFDiagCursor,
                            'rotate':Qt.CursorShape.CrossCursor,'move':Qt.CursorShape.OpenHandCursor}.get(mode,Qt.CursorShape.ArrowCursor)
                    self.setCursor(cursor);return
            if self.preview.clip_at(event.position()):
                self.setCursor(Qt.CursorShape.OpenHandCursor);return
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return super().mouseMoveEvent(event)
        gesture = self._gesture
        clip = next((c for c in self.preview.sequence.clips if c.id == gesture['clip']), None)
        if clip is None:
            return
        ratio, ox, oy = self._mapping()
        pointer = ((event.position().x()-ox)/ratio, (event.position().y()-oy)/ratio)
        center, width, height = gesture['center'], *gesture['size']
        tx, ty, scale, opacity, sx, sy, rotation = gesture['values']
        mode = gesture['mode']
        local = self._to_local(pointer, center, rotation)
        if mode == 'move':
            dx, dy = pointer[0]-gesture['start'][0], pointer[1]-gesture['start'][1]
            tx += dx; ty += dy
        elif mode == 'rotate':
            a0 = math.degrees(math.atan2(gesture['press_local'][1], gesture['press_local'][0]))
            a1 = math.degrees(math.atan2(local[1], local[0]))
            rotation += a1-a0
        elif mode in {'nw','n','ne','e','se','s','sw','w'}:
            px, py = gesture['press_local']; x, y = local
            sx_ratio = max(.05, abs(x)/(abs(px) or width/2))
            sy_ratio = max(.05, abs(y)/(abs(py) or height/2))
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if shift:
                if mode in ('e','w'): sx *= sx_ratio
                elif mode in ('n','s'): sy *= sy_ratio
                else: sx *= sx_ratio; sy *= sy_ratio
            else:
                factor = sx_ratio if mode in ('e','w') else sy_ratio if mode in ('n','s') else max(sx_ratio,sy_ratio)
                sx *= factor; sy *= factor
            sx, sy = min(10,max(.01,sx)), min(10,max(.01,sy))
        self.preview.transforms[clip.id] = (tx,ty,scale,opacity)
        self.preview.extra_transforms[clip.id] = (sx,sy,rotation)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._gesture:
            gesture=self._gesture; self._gesture=None
            clip=next((c for c in self.preview.sequence.clips if c.id==gesture['clip']),None)
            if clip:
                values=self._geometry(clip)[4]
                self.preview.transforms.pop(clip.id,None);self.preview.extra_transforms.pop(clip.id,None)
                self.transformEdited.emit(clip.id,*values[:3],values[4],values[5],values[6])
            self.releaseMouse()
            self.unsetCursor();self.update();event.accept();return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#252525'))
        seq = self.preview.sequence
        ratio = min(self.width() / seq.width, self.height() / seq.height)
        width, height = max(1, round(seq.width * ratio)), max(1, round(seq.height * ratio))
        p.drawImage((self.width() - width) // 2, (self.height() - height) // 2, self.composite(width, height))
        clip = next((c for c in self.preview.sequence.active_clips(self.preview.frame)
                     if c.id == getattr(self.preview, 'editing_clip_id', '')), None)
        if clip and self.preview.library.assets[clip.asset_id].has_video:
            center, box_w, box_h, rotation, _ = self._geometry(clip)
            handles = self._handles(center, box_w, box_h, rotation)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor('#ffffff'),2));p.setBrush(Qt.BrushStyle.NoBrush)
            corners=[handles[k] for k in ('nw','ne','se','sw')]
            p.drawPolygon(QPolygonF(corners))
            p.setPen(QPen(QColor('#118ca5'),2));p.setBrush(QBrush(QColor('#ffffff')))
            for name,point in handles.items():
                radius=6 if name=='rotate' else 5
                p.drawEllipse(point,radius,radius)
            top=handles['n'];rot=handles['rotate'];p.setPen(QPen(QColor('#118ca5'),2));p.drawLine(top,rot)
            p.setPen(QPen(QColor('#118ca5'),3));p.drawLine(rot+QPointF(-8,0),rot+QPointF(8,0))
        if self.preview.notice:
            p.setPen(QColor('#cccccc'))
            p.drawText(self.rect().adjusted(15, 15, -15, -15), Qt.AlignmentFlag.AlignCenter, self.preview.notice)


class Channel(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.clip = owner, None
        self.image = QImage()
        self.ready, self.pending = False, None
        self.still_target = None
        self.frame_generation = 0
        self.frame_conversion_active = False
        self.pending_video_frame = None
        self.generated_key = None
        self.parent_processors = {}
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoSink(self)
        self.player.setVideoOutput(self.video)
        self.video.videoFrameChanged.connect(self.video_frame)
        format = QAudioFormat()
        format.setSampleRate(48000)
        format.setChannelCount(2)
        format.setSampleFormat(QAudioFormat.SampleFormat.Float)
        output = QMediaDevices.defaultAudioOutput()
        if not output.isNull() and not output.isFormatSupported(format):
            format = output.preferredFormat()
        self.buffers = QAudioBufferOutput(format, self)
        self.player.setAudioBufferOutput(self.buffers)
        self.stream = AudioStream(self)
        self.stream.failed.connect(owner.fail)
        self.stream.meter.connect(lambda peak, reduction: owner.meter.emit(self.clip.id, peak, reduction) if self.clip else None)
        self.buffers.audioBufferReceived.connect(self.audio_buffer)
        self.player.mediaStatusChanged.connect(self.status)
        self.player.errorOccurred.connect(lambda _, text: owner.fail(text) if text else None)

    def video_frame(self, frame):
        if (frame.isValid() and self in self.owner.visible_channels() and self.clip
            and self.owner.library.assets[self.clip.asset_id].kind == 'media'):
            if not self.owner.playing:
                if self.still_target is None:
                    return
                tolerance = 1_000_000 / float(self.owner.rate)
                if frame.startTime() < self.still_target * 1000 - tolerance:
                    return
                self.still_target = None
                self.player.pause()
            if self.owner.frame_conversion_pool is None:
                self.image = frame.toImage()
                self.owner.video.update()
            else:
                # QVideoFrame.toImage() can map and copy a full 1080p frame.
                # Keep that work off the Windows UI thread, and retain only the
                # newest waiting frame so a slow machine never builds a backlog.
                self.pending_video_frame = QVideoFrame(frame)
                self.start_frame_conversion()

    def start_frame_conversion(self):
        if self.frame_conversion_active or self.pending_video_frame is None:
            return
        frame = self.pending_video_frame
        self.pending_video_frame = None
        generation, clip_id = self.frame_generation, self.clip.id if self.clip else ''
        self.frame_conversion_active = True
        future = self.owner.frame_conversion_pool.submit(frame.toImage)

        def completed(result):
            try:
                image = result.result()
            except Exception:
                image = QImage()
            self.owner.frameConverted.emit(self, image, generation, clip_id, frame)

        future.add_done_callback(completed)

    def accept_frame_conversion(self, image, generation, clip_id, source_frame):
        self.frame_conversion_active = False
        if generation == self.frame_generation and self.clip and self.clip.id == clip_id:
            if image is None or image.isNull():
                # Keep playback working on drivers that cannot map a video
                # surface from a worker thread. This is a rare compatibility
                # fallback; normal Windows playback converts off-thread.
                try:
                    image = source_frame.toImage()
                except Exception:
                    image = QImage()
            if image is not None and not image.isNull():
                self.image = image
                self.owner.video.update()
        self.start_frame_conversion()

    def status(self, status):
        if not self.clip or self.owner.library.assets[self.clip.asset_id].kind != 'media':
            return
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            self.ready = True
            if self.pending is not None:
                position = self.pending
                self.pending = None
                self.player.setPosition(position)
                if not self.owner.playing:
                    self.request_still(position)
            if self.owner.playing and not self.owner.waiting and self in self.owner.visible_channels():
                self.player.play()

    def load(self, clip, frame, force=False):
        changed = not self.clip or self.clip.id != clip.id
        if changed or force:
            self.frame_generation += 1
            self.pending_video_frame = None
        self.clip = clip
        asset = self.owner.library.assets[clip.asset_id]
        source_frame = clip.source_in + frame - clip.start
        self.audio.setVolume(clip.gain_at(source_frame) if not clip.muted else 0)
        if asset.kind in ('title', 'image'):
            self.still_target = None
            self.player.pause()
            self.stream.stop()
            self.ready, self.pending = True, None
            if asset.kind == 'title':
                from .titles import render_title
                spec = clip.title or asset.payload
                key = (clip.id, round(frame-clip.start), clip.duration, json.dumps(spec, sort_keys=True))
                frozen = copy.deepcopy(spec)
                title_frame = frame-clip.start+getattr(clip, '_title_offset', 0)
                duration = getattr(clip, '_title_duration', clip.duration)
                key = (*key, title_frame, duration)
                def generate():
                    canvas = frozen.get('canvas', [1920, 1080])
                    factor = min(960/canvas[0], 540/canvas[1])
                    return render_title(frozen, title_frame, duration,
                        max(1, round(canvas[0]*factor)), max(1, round(canvas[1]*factor)))
            else:
                key = ('image', asset.path)
                path = asset.path
                def generate():
                    reader = QImageReader(path)
                    size = reader.size()
                    if size.isValid():
                        reader.setScaledSize(size.scaled(QSize(1920, 1080), Qt.AspectRatioMode.KeepAspectRatio))
                    return reader.read()
            image = self.owner.generated_images.request((clip.id, asset.kind), key, generate)
            if image is not None:
                self.image = image
            self.owner.video.update()
            return
        self.generated_key = None
        if not Path(asset.path).is_file():
            self.owner.fail('素材が見つかりません：' + asset.name)
            return
        source = QUrl.fromLocalFile(asset.path)
        position = max(0, round(source_frame * 1000 / self.owner.rate))
        if source != self.player.source():
            self.ready, self.pending = False, position
            self.image = QImage()
            self.stream.stop()
            self.player.setSource(source)
        elif force or changed:
            self.stream.stop()
            if self.ready:
                self.player.setPosition(position)
                if not self.owner.playing:
                    self.request_still(position)
            else:
                self.pending = position
        if self.owner.playing and self.ready and not self.owner.waiting:
            self.still_target = None
            self.player.play()

    def request_still(self, position):
        if self.clip and self.owner.library.assets[self.clip.asset_id].has_video:
            self.still_target = position
            self.player.play()

    def audio_buffer(self, buffer):
        if (not self.owner.playing or not self.clip or not buffer.isValid() or self.pending is not None
            or self not in self.owner.visible_channels() or self.clip.muted
            or not self.owner.library.assets[self.clip.asset_id].has_audio):
            return
        format = buffer.format()
        start = buffer.startTime() / 1_000_000
        if start < 0:
            return
        clip, rate = self.clip, float(self.owner.rate)
        samples = decode_pcm(buffer.constData(), format.sampleFormat(), format.channelCount())
        left = max(0, math.ceil((clip.source_in / rate - start) * format.sampleRate()))
        right = min(len(samples), math.ceil(((clip.source_in + clip.duration) / rate - start) * format.sampleRate()))
        if right <= left:
            return
        samples = apply_gain(samples[left:right], start + left / format.sampleRate(), format.sampleRate(), self.owner.rate, clip)
        if not self.stream.prepare(format):
            return
        samples = self.stream.processor.process(samples, format.sampleRate(), clip)
        for parent, offset in getattr(clip, '_gain_parents', []):
            if parent.compressor:
                processor = self.parent_processors.setdefault(parent.id, Compressor())
                samples = processor.process(samples, format.sampleRate(), parent)
        self.stream.push(samples, format)

    def pause(self):
        self.still_target = None
        self.player.pause()
        self.stream.stop()
        self.parent_processors.clear()

    def clear(self):
        if self.clip is None and self.player.source().isEmpty():
            return
        self.frame_generation += 1
        self.pending_video_frame = None
        self.pause()
        self.clip, self.ready, self.pending = None, False, None
        self.generated_key = None
        self.image = QImage()
        self.player.setSource(QUrl())


class Preview(QWidget):
    frameConverted = Signal(object, object, int, str, object)
    frameChanged = Signal(int)
    playingChanged = Signal(bool)
    error = Signal(str)
    modeChanged = Signal(str)
    meter = Signal(str, float, float)

    def __init__(self):
        super().__init__()
        self.library = Library()
        self.frameConverted.connect(self.accept_frame_conversion, Qt.ConnectionType.QueuedConnection)
        self.frame, self.mode, self.source_id = 0, 'timeline', ''
        self.playing, self.notice, self.channels = False, '', {}
        self.transforms = {}
        self.extra_transforms = {}
        self.editing_clip_id = ''
        self.generated_images = AsyncImages(self)
        self.generated_images.changed.connect(self.generated_ready)
        self.generated_images.failed.connect(self.fail)
        self.effect_images = AsyncImages(self)
        self.effect_images.changed.connect(lambda: self.video.update())
        self.effect_images.failed.connect(self.fail)
        self.clock = QElapsedTimer()
        self.base, self.waiting = 0, False
        self.frame_conversion_pool = (ThreadPoolExecutor(max_workers=2, thread_name_prefix='Studio-video-frame')
                                      if sys.platform == 'win32' else None)
        self.source_channel = Channel(self)
        self.video = Canvas(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video)
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def accept_frame_conversion(self, channel, image, generation, clip_id, source_frame):
        # A project reset may retire a channel while a conversion is in flight.
        # Drop that result before touching a deleted Qt object.
        if channel is not self.source_channel and channel not in self.channels.values():
            return
        channel.accept_frame_conversion(image, generation, clip_id, source_frame)

    def generated_ready(self):
        self.sync()
        self.video.update()

    @property
    def sequence(self):
        return self.library.active

    @property
    def rate(self):
        return self.sequence.rate

    @property
    def main_channel(self):
        return self.source_channel if self.mode == 'source' else self.channels.get(0, self.source_channel)

    @property
    def player(self):
        return self.main_channel.player

    @property
    def audio(self):
        return self.main_channel.audio

    @property
    def current_clip(self):
        return self.main_channel.clip.id if self.main_channel.clip else ''

    @property
    def pending_position(self):
        return self.main_channel.pending

    @property
    def end(self):
        if self.mode == 'source' and self.source_id in self.library.assets:
            asset = self.library.assets[self.source_id]
            return max(1, round(asset.duration_ms*self.rate/1000)) if asset.kind in ('title', 'image') else asset.frames(self.rate)
        return self.sequence.end

    def clip_at(self, point):
        """Return the topmost visible timeline clip under a canvas click."""
        if self.mode != 'timeline' or self.frame >= self.sequence.end:
            return None
        sequence = self.sequence
        ratio = min(self.video.width() / sequence.width,
                    self.video.height() / sequence.height)
        if ratio <= 0:
            return None
        target_width = max(1, round(sequence.width * ratio))
        target_height = max(1, round(sequence.height * ratio))
        local_x = point.x() - (self.video.width() - target_width) // 2
        local_y = point.y() - (self.video.height() - target_height) // 2
        if not (0 <= local_x < target_width and 0 <= local_y < target_height):
            return None
        x, y = local_x / ratio, local_y / ratio
        for clip in reversed(sequence.active_clips(self.frame)):
            asset = self.library.assets[clip.asset_id]
            if not asset.has_video:
                continue
            image_width = max(1, asset.width or sequence.width)
            image_height = max(1, asset.height or sequence.height)
            tx, ty, scale, _opacity = self.transforms.get(
                clip.id, clip.transform_at(clip.source_in + self.frame - clip.start))
            source = clip.source_in + self.frame - clip.start
            sx, sy, angle = self.extra_transforms.get(
                clip.id, (clip.value_at('scale_x', source), clip.value_at('scale_y', source), clip.value_at('rotation', source)))
            factor = min(sequence.width / image_width, sequence.height / image_height) * scale
            draw_width, draw_height = image_width * factor * sx, image_height * factor * sy
            center = (sequence.width/2+tx, sequence.height/2+ty)
            lx, ly = Canvas._to_local((x,y),center,angle)
            if not (-draw_width/2 <= lx <= draw_width/2 and -draw_height/2 <= ly <= draw_height/2):
                continue
            channel = self.channels.get(channel_key((), clip.track))
            image = channel.image if channel and channel.clip and channel.clip.id == clip.id else None
            if image is not None and not image.isNull() and asset.kind != 'compound':
                px = min(image.width() - 1, max(0, int((lx + draw_width/2) / max(1, draw_width) * image.width())))
                py = min(image.height() - 1, max(0, int((ly + draw_height/2) / max(1, draw_height) * image.height())))
                if image.pixelColor(px, py).alpha() <= 8:
                    continue
            return clip
        return None

    def visible_channels(self):
        if self.mode == 'source' and self.library.assets.get(self.source_id) and self.library.assets[self.source_id].kind != 'compound':
            return [self.source_channel] if self.source_channel.clip else []
        return [c for c in self.channels.values() if c.clip]

    def reset(self, library):
        self.pause()
        self.source_channel.clear()
        for channel in self.channels.values():
            channel.clear()
            channel.deleteLater()
        self.channels.clear()
        self.transforms.clear()
        self.extra_transforms.clear()
        self.editing_clip_id = ''
        self.effect_images.clear()
        self.generated_images.clear()
        self.library, self.source_id = library, ''
        self.seek(0)

    def fail(self, text):
        self.pause()
        self.notice = text
        self.video.update()
        self.error.emit(text)

    def pause(self):
        self.playing = False
        self.source_channel.pause()
        for channel in self.channels.values():
            channel.pause()
        self.playingChanged.emit(False)

    def toggle(self):
        if self.playing:
            self.pause()
            return
        if not self.end:
            return
        if self.frame >= self.end:
            self.seek(0, self.mode)
        self.notice, self.playing = '', True
        self.waiting = True
        self.base = self.frame
        self.clock.start()
        self.sync(True)
        self.playingChanged.emit(True)

    def show_source(self, asset_id):
        self.pause()
        self.source_id = asset_id
        self.seek(0, 'source')
        self.modeChanged.emit('source')

    def seek(self, frame, mode='timeline'):
        if mode != self.mode:
            self.source_channel.pause()
            for channel in self.channels.values():
                channel.pause()
            self.mode = mode
            self.modeChanged.emit(mode)
        self.frame = max(0, min(frame, self.end))
        if self.frame >= self.end and self.playing:
            self.pause()
        self.base = self.frame
        self.clock.start()
        self.notice = ''
        self.waiting = True
        self.sync(True)
        self.waiting = any(not c.ready for c in self.visible_channels())
        self.frameChanged.emit(self.frame)
        self.video.update()

    def compose(self, width, height):
        def draw_sequence(sequence, frame, prefix=(), background=False):
            output = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            output.fill(QColor('#161616') if background else Qt.GlobalColor.transparent)
            p = QPainter(output)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            for clip in sequence.active_clips(frame):
                asset = self.library.assets[clip.asset_id]
                if not asset.has_video:
                    continue
                if asset.kind == 'compound':
                    child = next(s for s in self.library.sequences if s.id == asset.payload['sequence_id'])
                    image = draw_sequence(child, round((clip.source_in+frame-clip.start)*child.rate/sequence.rate), (*prefix, clip.id))
                else:
                    channel = self.channels.get(channel_key(prefix, clip.track))
                    image = channel.image if channel and channel.clip else QImage()
                if not image.isNull():
                    if clip.effects:
                        from .video_effects import apply_effects, effects_animated
                        # Bound interactive work even when the monitor is on a large display.
                        heavy = any(e.get('enabled', True) and e['type'] in
                                    ('zoom_blur', 'radial_blur', 'direction_blur') for e in clip.effects)
                        ew, eh = min(width, 320 if heavy else 480), min(height, 180 if heavy else 270)
                        # Keying and masks define the silhouette; a 480px proxy
                        # makes enlarged avatars visibly stair-step in the monitor.
                        if not heavy and any(e.get('enabled',True) and e['type'] in ('chroma_key','luma_key','shape_mask') for e in clip.effects):
                            ew,eh=min(1080,image.width()),min(1080,image.height())
                        key = (clip.id, image.cacheKey(), frame if effects_animated(clip.effects) else None,
                               ew, eh, json.dumps(clip.effects, sort_keys=True))
                        source = QImage(image)
                        effects = copy.deepcopy(clip.effects)
                        seconds = (frame-clip.start)/float(sequence.rate)
                        def render(source=source, effects=effects, seconds=seconds, ew=ew, eh=eh):
                            small = source.scaled(ew, eh, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation)
                            return apply_effects(small, effects, seconds, preview=True)
                        # A changed stack must not display the previous stack's result.
                        slot = (clip.id, key[-1], ew, eh)
                        processed = self.effect_images.request(slot, key, render)
                        if processed is not None:
                            image = processed
                    draw_image(p, image, clip, sequence, frame)
            p.end()
            return output
        def draw_image(p, image, clip, sequence, frame):
            tx, ty, scale, opacity = self.transforms.get(clip.id, clip.transform_at(clip.source_in+frame-clip.start))
            factor = min(width/image.width(), height/image.height())*scale
            source = clip.source_in+frame-clip.start
            sx, sy, angle = self.extra_transforms.get(
                clip.id, (clip.value_at('scale_x',source), clip.value_at('scale_y',source), clip.value_at('rotation',source)))
            w, h = image.width()*factor*sx, image.height()*factor*sy
            cx, cy = width/2+tx*width/sequence.width, height/2+ty*height/sequence.height
            p.save();p.translate(cx,cy);p.rotate(angle)
            p.setOpacity(opacity)
            p.drawImage(QRectF(-w/2,-h/2,w,h), image);p.restore()
        if self.mode == 'source':
            asset = self.library.assets.get(self.source_id)
            if asset and asset.kind == 'compound':
                child = next(s for s in self.library.sequences if s.id == asset.payload['sequence_id'])
                return draw_sequence(child, min(self.frame,self.end-1), ('source', asset.id), True)
            image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor('#161616'))
            if self.source_channel.clip and not self.source_channel.image.isNull():
                p = QPainter(image)
                draw_image(p, self.source_channel.image, self.source_channel.clip, self.sequence, self.frame)
                p.end()
            return image
        return draw_sequence(self.sequence, self.frame, background=True)

    def sync(self, force=False):
        if self.mode == 'source':
            asset = self.library.assets.get(self.source_id)
            if asset and asset.kind != 'compound':
                for channel in self.channels.values():
                    if channel.clip:
                        channel.clear()
                clip = Clip('source', asset.id, 0, 0, self.end, track=0 if asset.has_video else -1)
                self.source_channel.load(clip, min(self.frame, self.end-1), force)
                return
            if asset:
                child = next(s for s in self.library.sequences if s.id == asset.payload['sequence_id'])
                active = leaves_at(self.library, child, min(self.frame,self.end-1), prefix=('source', asset.id))
            else:
                active = {}
        else:
            active = leaves_at(self.library, self.sequence, self.frame)
        self.source_channel.clear()
        for track, channel in self.channels.items():
            if track not in active and channel.clip:
                channel.clear()
        for track, clip in active.items():
            if track not in self.channels:
                self.channels[track] = Channel(self)
            self.channels[track].load(clip, min(self.frame, self.end-1), force)

    def tick(self):
        if not self.playing:
            # A seek can finish after an older pause request. Retain the still-frame
            # request until the requested decoded frame actually arrives.
            for channel in self.visible_channels():
                if (channel.still_target is not None and channel.ready
                    and channel.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState):
                    channel.player.play()
            return
        if any(not c.ready for c in self.visible_channels()):
            if not self.waiting:
                for channel in self.visible_channels():
                    channel.pause()
            self.waiting = True
            return
        if self.waiting:
            self.base = self.frame
            self.clock.restart()
            self.waiting = False
            self.sync(True)
        frame = self.base + int(self.clock.elapsed() * self.rate / 1000)
        if frame >= self.end:
            self.seek(self.end, self.mode)
            return
        if frame == self.frame:
            return
        self.frame = frame
        self.sync()
        for channel in self.visible_channels():
            if channel.ready and channel.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                if self.library.assets[channel.clip.asset_id].kind == 'media':
                    channel.player.play()
        self.frameChanged.emit(frame)
        self.video.update()

    def shutdown(self):
        self.timer.stop()
        self.pause()
        self.source_channel.clear()
        for channel in self.channels.values():
            channel.clear()
        if self.frame_conversion_pool is not None:
            self.frame_conversion_pool.shutdown(wait=True, cancel_futures=True)
        self.effect_images.shutdown()
        self.generated_images.shutdown()
