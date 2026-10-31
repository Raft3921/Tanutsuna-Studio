"""OS capture control; no Python screen polling, no live recognition or mixing."""
from pathlib import Path
import json
import os
import sys
import time
import wave
from PySide6.QtCore import QObject, QProcess, QTimer, QSize, QUrl, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import (QMediaCaptureSession, QMediaRecorder, QMediaFormat,
    QScreenCapture, QAudioSource, QAudioFormat, QMediaDevices, QVideoSink)
from .takes import create_take, write_take, discard_pending, publish_take, WIDTH, HEIGHT, FPS

NATIVE = Path(__file__).parent / 'native'


class Microphone(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.file = None
        self.first = None

    def start(self, path, device_id=b''):
        devices = QMediaDevices.audioInputs()
        device = next((d for d in devices if bytes(d.id()) == device_id), QMediaDevices.defaultAudioInput())
        if device.isNull(): raise ValueError('マイクがありません。')
        format = QAudioFormat()
        format.setSampleRate(48000); format.setChannelCount(1)
        format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(format):
            raise ValueError('このマイクは48kHz・16bitモノラルに対応していません。別の入力を選択してください。')
        self.file = wave.open(str(path), 'wb')
        self.file.setnchannels(1); self.file.setsampwidth(2); self.file.setframerate(48000)
        self.source = QAudioSource(device, format, self)
        self.source.setBufferSize(9600)
        self.io = self.source.start()
        if self.io is None: self.stop(); raise ValueError('マイク入力を開始できません。')
        self.io.readyRead.connect(self.read)

    def read(self):
        data = bytes(self.io.readAll())
        if data:
            if self.first is None: self.first = time.perf_counter() - len(data)/96000
            self.file.writeframesraw(data)

    def stop(self):
        if self.source:
            self.read(); self.source.stop(); self.source.deleteLater(); self.source = None
        if self.file: self.file.close(); self.file = None


class Recorder(QObject):
    changed = Signal(str)
    started = Signal(object, object)
    stopped = Signal(object, object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = 'idle'
        self.folder = self.take = None
        self.process = None
        self.session = self.capture = self.encoder = self.sink = None
        self.mic = Microphone(self)
        self.lines = b''
        self.stop_requested = False
        self.timeout = QTimer(self); self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda: self.abort('収録の準備・終了処理がタイムアウトしました。収録済みの元データは保持します。'))

    @property
    def active(self): return self.state in ('preparing', 'recording', 'stopping')

    def set_state(self, state):
        self.state = state; self.changed.emit(state)

    def start(self, project, options):
        if self.active: raise ValueError('収録中です。')
        if self.process is not None:
            self.process.deleteLater(); self.process = None
        self.folder, self.take = create_take(project, options,temporary=True)
        self.lines = b''; self.stop_requested = False; self.preserve_capture = False
        self.take['timestamps'] = {}
        self.take['project_id'] = project['id']
        self.set_state('preparing'); self.timeout.start(60000)
        try:
            if sys.platform == 'darwin': self.start_mac(options)
            elif sys.platform == 'win32': self.start_windows(options)
            else: raise ValueError('独自収録はWindows 11 / macOS 15以降に対応しています。')
        except Exception as exc:
            self.abort(str(exc))

    def child(self, program, arguments):
        self.process = QProcess(self)
        self.process.setProgram(str(program)); self.process.setArguments([str(a) for a in arguments])
        self.process.setStandardErrorFile(str(self.folder / 'capture.log'))
        self.process.readyReadStandardOutput.connect(self.read_messages)
        self.process.errorOccurred.connect(lambda e: self.abort(self.process.errorString()) if e == QProcess.ProcessError.FailedToStart else None)
        self.process.finished.connect(self.child_finished)
        self.process.start()

    def start_mac(self, options):
        helper = NATIVE / 'screen-recorder'
        if not helper.is_file(): raise ValueError('Mac収録エンジンがありません。アプリを再ビルドしてください。')
        screens = QGuiApplication.screens()
        if not 0 <= options['display'] < len(screens): raise ValueError('ディスプレイが見つかりません。')
        geometry = screens[options['display']].geometry()
        self.child(helper, [self.folder, options['display'], int(options['desktop']), int(options['voice']), options.get('mic', ''), geometry.x(), geometry.y()])

    def start_windows(self, options):
        screens = QGuiApplication.screens()
        index = options['display']
        if not 0 <= index < len(screens): raise ValueError('ディスプレイが見つかりません。')
        self.session = QMediaCaptureSession(self)
        self.capture = QScreenCapture(self)
        self.capture.setScreen(screens[index]); self.session.setScreenCapture(self.capture)
        self.encoder = QMediaRecorder(self)
        fmt = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
        fmt.setVideoCodec(QMediaFormat.VideoCodec.H264)
        self.encoder.setMediaFormat(fmt)
        self.encoder.setVideoResolution(QSize(WIDTH, HEIGHT)); self.encoder.setVideoFrameRate(FPS)
        self.encoder.setVideoBitRate(10_000_000)
        self.encoder.setEncodingMode(QMediaRecorder.EncodingMode.ConstantBitRateEncoding)
        self.encoder.setOutputLocation(QUrl.fromLocalFile(str(self.folder / 'screen.mp4')))
        self.encoder.errorOccurred.connect(lambda e, msg: self.abort(msg))
        self.capture.errorOccurred.connect(lambda e, msg: self.abort(msg))
        self.session.setRecorder(self.encoder)
        self.sink = QVideoSink(self)
        self.session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self.first_video)
        self.encoder.recorderStateChanged.connect(self.video_state)
        if options['voice']:
            self.mic.first = None
            self.mic.start(self.folder / 'voice-source.wav', options.get('mic', '').encode())
        if options['desktop']:
            # No shell interpolation; the OS compiler is used only on our fixed source.
            from PySide6.QtCore import QStandardPaths
            cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / 'native'
            cache.mkdir(parents=True, exist_ok=True)
            import hashlib
            signature = hashlib.sha256((NATIVE / 'Loopback.cs').read_bytes()).hexdigest()[:16]
            helper = cache / ('loopback-' + signature + '.exe')
            if not helper.is_file():
                self.compiler = QProcess(self)
                self.compiler.setProgram(str(Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'))
                self.compiler.setArguments(['/nologo', '/optimize+', '/platform:x64', '/out:'+str(helper), str(NATIVE / 'Loopback.cs')])
                self.compiler.setStandardOutputFile(str(self.folder / 'compiler.log'))
                self.compiler.setStandardErrorFile(str(self.folder / 'compiler.log'), mode=__import__('PySide6.QtCore', fromlist=['QIODevice']).QIODevice.OpenModeFlag.Append)
                self.compiler.errorOccurred.connect(lambda _: self.abort('Windows内蔵コンパイラを起動できません。'))
                self.compiler.finished.connect(lambda code, _: self.child(helper, [self.folder / 'desktop-source.wav']) if code == 0 and self.state == 'preparing' else self.abort('PC音エンジンを準備できません。compiler.logを確認してください。'))
                self.compiler.start()
            else: self.child(helper, [self.folder / 'desktop-source.wav'])
        else: self.begin_video()

    def begin_video(self):
        if self.state != 'preparing': return
        self.capture.setActive(True); self.encoder.record()

    def first_video(self, frame):
        if frame.isValid() and 'screen' not in self.take['timestamps'] and self.encoder.recorderState() == QMediaRecorder.RecorderState.RecordingState:
            # Windows Qt does not expose the DXGI/QPC capture clock. This is an arrival estimate.
            self.take['timestamps']['screen'] = time.perf_counter()
            self.take['sync_method'] = 'first-buffer-arrival-estimate'
            self.mark_started()

    def video_state(self, state):
        if state == QMediaRecorder.RecorderState.StoppedState and self.state == 'stopping':
            self.finish_windows_if_ready()

    def read_messages(self):
        self.lines += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.lines:
            line, self.lines = self.lines.split(b'\n', 1)
            try: message = json.loads(line)
            except ValueError: continue
            kind = message.get('type')
            if kind == 'error': self.abort(message.get('message', '収録エラー')); return
            if kind == 'track':
                self.take['timestamps'][message['role']] = message['pts']
                if message['role'] == 'screen':
                    self.take['sync_method'] = 'ScreenCaptureKit-sample-timestamps'
                    self.mark_started()
            elif kind == 'ready' and sys.platform == 'win32':
                self.take['timestamps']['desktop'] = message['pts']; self.begin_video()
            elif kind == 'stats':
                self.take.setdefault('stats', {})[message['role']] = message

    def mark_started(self):
        if self.state != 'preparing': return
        self.timeout.stop(); self.epoch = time.perf_counter()
        self.take['state'] = 'recording'; write_take(self.folder, self.take)
        self.set_state('recording'); self.started.emit(self.folder, self.take)

    def stop(self):
        if self.state != 'recording': return
        self.take['duration'] = max(.02, time.perf_counter() - self.epoch)
        self.set_state('stopping')
        # High resolution hardware encoders can take considerably longer than
        # 30 seconds to finalize a long file, particularly while face tracking
        # is sharing CPU/GPU resources. Keep the original capture intact and
        # allow a duration-scaled finalization window before declaring failure.
        finalize_timeout=max(120000,min(600000,120000+int(self.take['duration']*250)))
        self.timeout.start(finalize_timeout)
        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self.process.write(b'stop\n'); self.process.closeWriteChannel()
        if sys.platform == 'win32':
            self.mic.stop()
            if self.mic.first is not None: self.take['timestamps']['voice'] = self.mic.first
            self.encoder.stop(); self.capture.setActive(False)
            self.finish_windows_if_ready()

    def child_finished(self, code, status):
        if self.state == 'failed': self.cleanup_pending();return
        if code != 0 or status != QProcess.ExitStatus.NormalExit:
            self.abort('収録エンジンが停止しました。capture.logを確認してください。'); return
        if self.state != 'stopping':
            self.abort('収録が予期せず終了しました。'); return
        if sys.platform == 'darwin': self.complete()
        else: self.finish_windows_if_ready()

    def finish_windows_if_ready(self):
        if self.state != 'stopping': return
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning: return
        if self.encoder.recorderState() != QMediaRecorder.RecorderState.StoppedState: return
        self.complete()

    def complete(self):
        self.timeout.stop()
        self.take['state'] = 'captured'
        write_take(self.folder, self.take)
        self.set_state('captured'); self.stopped.emit(self.folder, self.take)
        self.release_video()

    def release_video(self):
        if self.session:
            self.session.setVideoSink(None); self.session.setRecorder(None); self.session.setScreenCapture(None)
            for obj in (self.capture, self.encoder, self.sink, self.session): obj.deleteLater()
            self.session = self.capture = self.encoder = self.sink = None

    def abort(self, message):
        if self.state == 'failed': return
        self.preserve_capture = self.state in ('recording','stopping')
        if self.preserve_capture and not self.take.get('duration'):
            self.take['duration'] = max(.02,time.perf_counter()-self.epoch)
        self.set_state('failed'); self.timeout.stop(); self.mic.stop()
        if self.process: self.process.kill()
        if hasattr(self, 'compiler'): self.compiler.kill()
        if self.encoder: self.encoder.stop()
        if self.capture: self.capture.setActive(False)
        if self.take and self.folder.is_dir():
            self.take['state'] = 'incomplete'
            if message not in self.take['warnings']:self.take['warnings'].append(message)
            write_take(self.folder, self.take)
        self.release_video(); self.failed.emit(message)
        QTimer.singleShot(0,self.cleanup_pending)

    def cleanup_pending(self,attempt=0):
        folder=self.folder
        if self.state!='failed' or not folder:return
        if getattr(self,'preserve_capture',False):return
        try:discard_pending(folder)
        except OSError:
            if attempt<20:QTimer.singleShot(250,lambda:self.cleanup_pending(attempt+1))
