from pathlib import Path
from collections import OrderedDict
import math
import copy
import json
from PySide6.QtCore import QElapsedTimer, QObject, QRectF, Qt, QTimer, QUrl, Signal, QSize
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter
from PySide6.QtMultimedia import QAudioBufferOutput, QAudioFormat, QAudioOutput, QMediaDevices, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QVBoxLayout, QWidget
from .model import Clip, Library
from .scene import leaves_at, channel_key
from .audio import AudioStream, Compressor, apply_gain, decode_pcm
from .async_images import AsyncImages


class Canvas(QWidget):
    clipClicked = Signal(str)

    def __init__(self, preview):
        super().__init__(preview)
        self.preview = preview
        self.setMinimumSize(320, 180)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def videoSink(self):
        return self.preview.main_channel.video

    def composite(self, width, height):
        return self.preview.compose(width, height)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            clip = self.preview.clip_at(event.position())
            if clip is not None:
                self.clipClicked.emit(clip.id)
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#252525'))
        seq = self.preview.sequence
        ratio = min(self.width() / seq.width, self.height() / seq.height)
        width, height = max(1, round(seq.width * ratio)), max(1, round(seq.height * ratio))
        p.drawImage((self.width() - width) // 2, (self.height() - height) // 2, self.composite(width, height))
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
            self.image = frame.toImage()
            self.owner.video.update()

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
        self.pause()
        self.clip, self.ready, self.pending = None, False, None
        self.generated_key = None
        self.image = QImage()
        self.player.setSource(QUrl())


class Preview(QWidget):
    frameChanged = Signal(int)
    playingChanged = Signal(bool)
    error = Signal(str)
    modeChanged = Signal(str)
    meter = Signal(str, float, float)

    def __init__(self):
        super().__init__()
        self.library = Library()
        self.frame, self.mode, self.source_id = 0, 'timeline', ''
        self.playing, self.notice, self.channels = False, '', {}
        self.transforms = {}
        self.generated_images = AsyncImages(self)
        self.generated_images.changed.connect(self.generated_ready)
        self.generated_images.failed.connect(self.fail)
        self.effect_images = AsyncImages(self)
        self.effect_images.changed.connect(lambda: self.video.update())
        self.effect_images.failed.connect(self.fail)
        self.clock = QElapsedTimer()
        self.base, self.waiting = 0, False
        self.source_channel = Channel(self)
        self.video = Canvas(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video)
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

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
            factor = min(sequence.width / image_width, sequence.height / image_height) * scale
            draw_width, draw_height = image_width * factor, image_height * factor
            left = (sequence.width - draw_width) / 2 + tx
            top = (sequence.height - draw_height) / 2 + ty
            if not (left <= x <= left + draw_width and top <= y <= top + draw_height):
                continue
            channel = self.channels.get(channel_key((), clip.track))
            image = channel.image if channel and channel.clip and channel.clip.id == clip.id else None
            if image is not None and not image.isNull() and asset.kind != 'compound':
                px = min(image.width() - 1, max(0, int((x - left) / max(1, draw_width) * image.width())))
                py = min(image.height() - 1, max(0, int((y - top) / max(1, draw_height) * image.height())))
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
            w, h = image.width()*factor, image.height()*factor
            p.setOpacity(opacity)
            p.drawImage(QRectF((width-w)/2+tx*width/sequence.width, (height-h)/2+ty*height/sequence.height, w, h), image)
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
        self.effect_images.shutdown()
        self.generated_images.shutdown()
        self.timer.stop()
        self.pause()
        self.source_channel.clear()
        for channel in self.channels.values():
            channel.clear()
