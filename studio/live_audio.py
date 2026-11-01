"""Native PC loopback and Qt microphone, feeding a common float-stereo 48kHz clock."""
from pathlib import Path
import hashlib
import os
import sys
import numpy as np
from PySide6.QtCore import QObject,QProcess,QStandardPaths,Signal
from PySide6.QtMultimedia import QAudioSource,QAudioFormat,QMediaDevices,QAudio
from .live_av import av


class PCMDecoder:
    def __init__(self,rate,channels,bits,kind):
        self.channels=channels;self.bits=bits;self.kind=kind;self.pending=b'';self.rate=rate
        self.resampler=av.AudioResampler(format='fltp',layout='stereo',rate=48000)
    def feed(self,data):
        data=self.pending+data;size=self.channels*self.bits//8;count=len(data)//size*size
        self.pending=data[count:]
        if not count:return np.empty((0,2),np.float32)
        if self.bits==32 and self.kind=='float':values=np.frombuffer(data[:count],'<f4')
        elif self.bits==32:values=np.frombuffer(data[:count],'<i4').astype(np.float32)/2147483648
        elif self.bits==16:values=np.frombuffer(data[:count],'<i2').astype(np.float32)/32768
        elif self.bits==8:values=(np.frombuffer(data[:count],np.uint8).astype(np.float32)-128)/128
        elif self.bits==24:
            b=np.frombuffer(data[:count],np.uint8).reshape(-1,3).astype(np.int32)
            value=b[:,0]|(b[:,1]<<8)|(b[:,2]<<16);values=((value^0x800000)-0x800000).astype(np.float32)/8388608
        else:raise ValueError('この音声形式を取得できません。')
        values=values.reshape(-1,self.channels)
        values=values[:,:2] if self.channels>=2 else np.repeat(values,2,axis=1)
        frame=av.AudioFrame.from_ndarray(values.T.copy().astype(np.float32),format='fltp',layout='stereo');frame.sample_rate=self.rate
        frames=self.resampler.resample(frame)
        return np.concatenate([f.to_ndarray().T for f in frames]) if frames else np.empty((0,2),np.float32)


class LiveAudio(QObject):
    failed=Signal(str)
    ready=Signal()
    def __init__(self,engine,parent=None):
        super().__init__(parent);self.engine=engine;self.process=None;self.microphone=None;self.io=None;self.stopping=False;self.header=b'';self.decoder=None
    def start(self,mic_id=b'',desktop=True,voice=True):
        self.stopping=False;self.header=b'';self.decoder=None
        if voice:
            devices=QMediaDevices.audioInputs();device=next((d for d in devices if bytes(d.id())==mic_id),QMediaDevices.defaultAudioInput())
            if device.isNull():raise ValueError('マイクがありません。マイクをオフにするか入力機器を接続してください。')
            fmt=device.preferredFormat();bits=fmt.bytesPerSample()*8
            self.mic_decoder=PCMDecoder(fmt.sampleRate(),fmt.channelCount(),bits,'float' if fmt.sampleFormat()==QAudioFormat.SampleFormat.Float else 'int')
            self.microphone=QAudioSource(device,fmt,self);self.microphone.stateChanged.connect(self.mic_state);self.microphone.setBufferSize(max(4096,fmt.bytesForDuration(100000)))
            self.io=self.microphone.start()
            if self.io is None:
                if sys.platform=='win32':
                    raise ValueError('マイクを開始できません。Windows「設定 > プライバシーとセキュリティ > マイク」で「マイクへのアクセス」と「デスクトップ アプリにマイクへのアクセスを許可する」をオンにしてください。デスクトップアプリには個別の許可ダイアログは表示されません。')
                raise ValueError('マイクを開始できません。OSの許可を確認してください。')
            self.io.readyRead.connect(self.read_mic)
        if not desktop:self.ready.emit();return
        native=Path(__file__).parent/'native'
        if sys.platform=='darwin':self.launch(native/'desktop-audio');return
        if sys.platform!='win32':raise ValueError('PC音の取得はMac/Windowsに対応しています。')
        cache=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))/'native';cache.mkdir(parents=True,exist_ok=True)
        source=native/'LiveLoopback.cs';signature=hashlib.sha256(source.read_bytes()).hexdigest()[:16];helper=cache/('live-loopback-'+signature+'.exe')
        if helper.is_file():self.launch(helper);return
        self.compiler=QProcess(self);self.compiler.setProgram(str(Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'))
        self.compiler.setArguments(['/nologo','/optimize+','/platform:x64','/out:'+str(helper),str(source)])
        self.compiler.finished.connect(lambda code,_:self.launch(helper) if code==0 and not self.stopping else self.failed.emit('PC音エンジンを準備できませんでした。') if not self.stopping else None)
        self.compiler.errorOccurred.connect(lambda _:self.failed.emit('Windows内蔵コンパイラを起動できません。'));self.compiler.start()
    def launch(self,helper):
        if self.stopping:return
        if not helper.is_file():raise ValueError('PC音エンジンがありません。配布物を確認してください。')
        self.process=QProcess(self);self.process.setProgram(str(helper));self.process.readyReadStandardOutput.connect(self.read_desktop)
        self.process.finished.connect(lambda *_:self.failed.emit('PC音の取得が停止しました。機器とOSの許可を確認してください。') if not self.stopping else None)
        self.process.errorOccurred.connect(lambda _:self.failed.emit('PC音エンジンを開始できません。') if not self.stopping else None);self.process.start()
    def mic_state(self,state):
        if not self.stopping and state==QAudio.State.StoppedState and self.microphone and self.microphone.error()!=QAudio.Error.NoError:
            if sys.platform=='win32':
                self.failed.emit('マイク入力が停止しました。Windows「設定 > プライバシーとセキュリティ > マイク」の「デスクトップ アプリにマイクへのアクセスを許可する」と入力機器を確認してください。')
            else:self.failed.emit('マイク入力が停止しました。入力機器とOSの許可を確認してください。')

    def read_mic(self):
        if self.io:
            try:
                data=self.mic_decoder.feed(bytes(self.io.readAll()))
                if len(data):self.engine.inputs['voice'].push(data)
            except Exception:self.failed.emit('マイクの音声を読み込めません。入力機器を確認してください。')
    def read_desktop(self):
        data=bytes(self.process.readAllStandardOutput())
        if self.stopping:return
        if self.decoder is None:
            self.header+=data
            if b'\n' not in self.header:return
            line,data=self.header.split(b'\n',1);self.header=b''
            try:
                rate,channels,bits,kind=line.decode('ascii').split();self.decoder=PCMDecoder(int(rate),int(channels),int(bits),kind)
            except Exception:self.failed.emit('PC音の形式を読み込めません。');return
            self.ready.emit()
        try:
            samples=self.decoder.feed(data)
            if len(samples):self.engine.inputs['desktop'].push(samples)
        except Exception:self.failed.emit('PC音の音声を読み込めません。出力機器を確認してください。')
    def stop(self):
        self.stopping=True
        if self.microphone:self.microphone.stop();self.microphone.deleteLater();self.microphone=None;self.io=None
        if hasattr(self,'compiler') and self.compiler.state()!=QProcess.ProcessState.NotRunning:self.compiler.kill()
        if self.process and self.process.state()!=QProcess.ProcessState.NotRunning:
            process=self.process;process.write(b'stop\n');process.closeWriteChannel()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000,lambda:process.kill() if process.state()!=QProcess.ProcessState.NotRunning else None)
