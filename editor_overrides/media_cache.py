"""Bounded asynchronous filmstrip and peak caches; source files stay untouched."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import subprocess
import threading
import time
import copy
import numpy as np
from PySide6.QtCore import QObject, QStandardPaths, Signal, QTimer, QSize, Qt
from PySide6.QtGui import QImage, QImageReader


def ffmpeg_executable():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def process_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def media_signature(asset):
    stat = Path(asset.path).stat()
    return hashlib.sha256(f'{Path(asset.path).resolve()}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()[:24]


class MediaCache(QObject):
    changed = Signal()
    failed = Signal(str)
    result = Signal(object)

    def __init__(self, parent=None, root=None):
        super().__init__(parent)
        base = root or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / 'media-v1'
        self.root = Path(base)
        self.root.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='CutRaft-cache')
        self.images, self.peaks, self.pending, self.errors = OrderedDict(), {}, set(), set()
        self.closed = False
        self.suspended = False
        self.processes, self.lock = set(), threading.Lock()
        self.result.connect(self.accept_result)
        self.source_info = OrderedDict()
        self.repaint_timer = QTimer(self)
        self.repaint_timer.setSingleShot(True)
        self.repaint_timer.setInterval(16)
        self.repaint_timer.timeout.connect(self.changed)

    def accept_result(self, data):
        if self.closed:
            return
        key, kind, value, error = data
        self.pending.discard(key)
        if error:
            self.errors.add(key)
            self.failed.emit(error)
        elif kind == 'image':
            self.images[key] = value
            while len(self.images) > 512:
                self.images.popitem(last=False)
        else:
            self.peaks[key] = value
            while len(self.peaks) > 64:
                self.peaks.pop(next(iter(self.peaks)))
        if not self.repaint_timer.isActive():
            self.repaint_timer.start()

    def source_signature(self, asset):
        now = time.monotonic()
        cached = self.source_info.get(asset.path)
        if cached and now - cached[0] < .25:
            self.source_info.move_to_end(asset.path)
            return cached[1]
        try:
            stat = Path(asset.path).stat()
            value = hashlib.sha256(f'{asset.path}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()[:24]
        except OSError:
            value = None
        self.source_info[asset.path] = (now, value)
        while len(self.source_info) > 4096:
            self.source_info.popitem(last=False)
        return value

    def run_process(self, args, timeout=30):
        if self.closed:
            raise RuntimeError('キャッシュ生成を中止しました。')
        process = subprocess.Popen([ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-nostdin', *args],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **process_options())
        with self.lock:
            self.processes.add(process)
            if self.closed and process.poll() is None:
                process.kill()
        try:
            output, error = process.communicate(timeout=timeout)
            if process.returncode:
                raise ValueError(error.decode(errors='replace')[-500:])
            return output
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            with self.lock:
                self.processes.discard(process)

    def submit(self, key, kind, operation):
        if self.closed or self.suspended or key in self.pending or key in self.errors or len(self.pending) >= 48:
            return
        self.pending.add(key)
        def work():
            try:
                value = operation()
                if not self.closed:
                    self.result.emit((key, kind, value, ''))
            except Exception as exc:
                if not self.closed:
                    self.result.emit((key, kind, None, str(exc)))
        self.pool.submit(work)

    def thumbnail(self, asset, seconds):
        if asset.kind not in ('media', 'image') or not asset.has_video:
            return None
        signature = self.source_signature(asset)
        if signature is None:
            return None
        stamp = 0 if asset.kind == 'image' else max(0, round(seconds * 2))
        key = (signature, stamp)
        if key in self.images:
            self.images.move_to_end(key)
            return self.images[key]
        def create():
            path = self.root / f'{signature}-{stamp}.png'
            if not path.exists():
                if asset.kind == 'image':
                    reader = QImageReader(asset.path)
                    size = reader.size()
                    reader.setScaledSize(size.scaled(QSize(160, 90), Qt.AspectRatioMode.KeepAspectRatio))
                    image = reader.read()
                else:
                    at = min(stamp / 2, max(0, asset.duration_ms / 1000 - .05))
                    data = self.run_process(['-ss', str(at), '-threads', '1', '-i', asset.path, '-frames:v', '1', '-an',
                                             '-vf', 'scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2',
                                             '-f', 'image2pipe', '-vcodec', 'png', '-threads', '1', 'pipe:1'])
                    image = QImage.fromData(data)
                if image.isNull():
                    raise ValueError('サムネイルを生成できません：' + asset.name)
                temp = path.with_suffix('.tmp.png')
                image.save(str(temp), 'PNG')
                os.replace(temp, path)
            return QImage(str(path))
        self.submit(key, 'image', create)
        return None

    def title_thumbnail(self, spec, frame, duration, signature):
        key = ('title', signature, frame, duration)
        if key in self.images:
            self.images.move_to_end(key)
            return self.images[key]
        if key in self.pending or key in self.errors or self.closed or len(self.pending) >= 48:
            return None
        frozen = copy.deepcopy(spec)
        def create():
            from .titles import render_title
            return render_title(frozen, frame, duration, 160, 90)
        self.submit(key, 'image', create)
        return None

    def waveform(self, asset):
        if asset.kind != 'media' or not asset.has_audio:
            return None
        key = self.source_signature(asset)
        if key is None:
            return None
        if key in self.peaks:
            return self.peaks[key]
        def create():
            path = self.root / f'{key}-peaks.npz'
            if path.exists():
                with np.load(path) as data:
                    return data['peaks']
            # 100 envelope buckets/second, bounded raw chunks even for long sources.
            process = subprocess.Popen([ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-threads', '1', '-i', asset.path, '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', 'pipe:1'],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **process_options())
            with self.lock:
                self.processes.add(process)
                if self.closed and process.poll() is None:
                    process.kill()
            values, carry = [], b''
            try:
                while not self.closed:
                    chunk = process.stdout.read(320 * 1024)
                    if not chunk:
                        break
                    carry += chunk
                    count = len(carry) // 320 * 320
                    samples = np.frombuffer(carry[:count], dtype='<f4')
                    if len(samples):
                        values.append(np.abs(samples).reshape(-1, 80).max(axis=1))
                    carry = carry[count:]
                if self.closed:
                    raise RuntimeError('キャッシュ生成を中止しました。')
                if carry:
                    values.append(np.array([np.abs(np.frombuffer(carry[:len(carry)//4*4], dtype='<f4')).max(initial=0)]))
                if process.wait(timeout=10):
                    raise ValueError('波形を生成できません：' + asset.name)
                peaks = np.concatenate(values).astype('float32') if values else np.zeros(1, dtype='float32')
                temp = path.with_suffix('.tmp.npz')
                np.savez_compressed(temp, peaks=peaks)
                os.replace(temp, path)
                return peaks
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                with self.lock:
                    self.processes.discard(process)
        self.submit(key, 'peaks', create)
        return None

    def shutdown(self):
        self.closed = True
        self.repaint_timer.stop()
        with self.lock:
            for process in list(self.processes):
                if process.poll() is None:
                    process.kill()
        self.pool.shutdown(wait=True, cancel_futures=True)
