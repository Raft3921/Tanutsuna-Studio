"""ScreenCaptureKit window inventory/capture, including Java and untitled windows."""
from dataclasses import dataclass
from pathlib import Path
import ctypes as c
import json
import sys
from PySide6.QtCore import QObject,QProcess,Signal,QTimer
from PySide6.QtGui import QImage


@dataclass
class NativeWindow:
    ident:int
    title:str
    app:str
    pid:int=0
    def description(self):return f'{self.app} — {self.title or "タイトルなし"}  [ウィンドウ {self.ident}]'
    def isValid(self):
        if sys.platform=='win32':return bool(c.windll.user32.IsWindow(c.c_void_p(self.ident)))
        if sys.platform!='darwin':return False
        cg=c.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        cf=c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        cg.CGWindowListCopyWindowInfo.argtypes=[c.c_uint32,c.c_uint32];cg.CGWindowListCopyWindowInfo.restype=c.c_void_p
        cf.CFArrayGetCount.argtypes=[c.c_void_p];cf.CFArrayGetCount.restype=c.c_long;cf.CFRelease.argtypes=[c.c_void_p]
        result=cg.CGWindowListCopyWindowInfo(8,self.ident)
        if not result:return False
        try:return cf.CFArrayGetCount(result)>0
        finally:cf.CFRelease(result)


def windows_fallback(known):
    """Win32 inventory includes GLFW/Java windows omitted by Qt's inventory."""
    if sys.platform!='win32':return []
    from ctypes import wintypes as w
    user=c.WinDLL('user32',use_last_error=True)
    user.GetWindowTextLengthW.argtypes=[w.HWND];user.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
    user.IsWindowVisible.argtypes=[w.HWND];user.GetWindowRect.argtypes=[w.HWND,c.POINTER(w.RECT)]
    user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
    descriptions=[item.description().casefold() for item in known];result=[]
    callback=c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    def collect(hwnd,_):
        if not user.IsWindowVisible(hwnd):return True
        length=user.GetWindowTextLengthW(hwnd)
        if length<=0:return True
        title=c.create_unicode_buffer(length+1);user.GetWindowTextW(hwnd,title,length+1)
        if any(title.value.casefold() in text for text in descriptions):return True
        rect=w.RECT();user.GetWindowRect(hwnd,c.byref(rect))
        if rect.right<=rect.left or rect.bottom<=rect.top:return True
        pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,c.byref(pid))
        import os
        if pid.value!=os.getpid():result.append(NativeWindow(int(hwnd),title.value,'互換取得',pid.value))
        return True
    handler=callback(collect);user.EnumWindows.argtypes=[callback,w.LPARAM];user.EnumWindows(handler,0)
    return result


class NativeWindowSource(QObject):
    listed=Signal(object)
    frame=Signal(QImage)
    failed=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent);self.helper=Path(__file__).parent/'native/live-window';self.process=None;self.enumerator=None;self.buffer=bytearray();self.size=None;self.active=False;self.stopping=False
    @property
    def available(self):return sys.platform=='darwin' and self.helper.is_file()
    def enumerate(self):
        if self.enumerator and self.enumerator.state()!=QProcess.ProcessState.NotRunning:return
        process=QProcess(self);self.enumerator=process;process.setProgram(str(self.helper));process.setArguments(['--list'])
        def done(code,*_):
            try:
                if code:raise ValueError()
                rows=json.loads(bytes(process.readAllStandardOutput()))
                result=[NativeWindow(int(r['id']),r.get('title',''),r.get('app','アプリ'),int(r.get('pid',0))) for r in rows]
                self.listed.emit(result)
            except Exception:self.failed.emit('ゲームの一覧を取得できません。macOSの「画面収録」許可を確認し、ウィンドウを更新してください。')
            process.deleteLater();self.enumerator=None
        process.finished.connect(done);process.errorOccurred.connect(lambda _:self.failed.emit('Macのウィンドウ取得エンジンを起動できません。'))
        timeout=QTimer(process);timeout.setSingleShot(True);timeout.timeout.connect(process.kill);process.finished.connect(timeout.stop);process.start();timeout.start(15000)
    def start(self,window,width,height):
        self.stop();self.stopping=False;self.buffer=bytearray();self.size=None;self.active=True
        process=QProcess(self);self.process=process
        if sys.platform=='win32':
            from .jobs import ffmpeg_executable
            self.size=(width,height)
            process.setProgram(str(ffmpeg_executable()))
            process.setArguments(['-hide_banner','-loglevel','error','-nostdin','-f','gdigrab','-draw_mouse','1','-framerate','60','-i',f'hwnd={window.ident}',
                '-vf',f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2','-threads','1','-pix_fmt','bgra','-f','rawvideo','pipe:1'])
        else:
            process.setProgram(str(self.helper));process.setArguments(['--capture',str(window.ident),str(width),str(height)])
        process.readyReadStandardError.connect(lambda:process.readAllStandardError())
        process.readyReadStandardOutput.connect(lambda:self.read(process))
        process.finished.connect(lambda code,*_:self.ended(process,code))
        process.errorOccurred.connect(lambda _:self.failed.emit('ゲームのウィンドウを取得できません。') if self.process is process and not self.stopping else None)
        process.start()
    def read(self,process):
        if self.process is not process:process.readAllStandardOutput();return
        self.buffer.extend(bytes(process.readAllStandardOutput()))
        if self.size is None:
            newline=self.buffer.find(b'\n')
            if newline<0:return
            try:
                width,height,kind=bytes(self.buffer[:newline]).decode('ascii').split();width,height=int(width),int(height)
                if (width,height) not in ((1280,720),(1920,1080)) or kind!='BGRA':raise ValueError()
                self.size=(width,height);del self.buffer[:newline+1]
            except Exception:self.stop();self.failed.emit('ゲーム映像の形式が一致しません。');return
        width,height=self.size;length=width*height*4;count=len(self.buffer)//length
        if count:
            # Keep only the most recent complete frame, plus any partial next frame.
            image=QImage(bytes(self.buffer[(count-1)*length:count*length]),width,height,width*4,QImage.Format.Format_ARGB32).copy()
            del self.buffer[:count*length];self.frame.emit(image)
    def ended(self,process,code):
        if self.process is process:
            self.active=False;self.process=None
            if not self.stopping:self.failed.emit('選択したゲームの取得が停止しました。ゲームと画面収録の許可を確認してください。')
        process.deleteLater()
    def stop(self):
        self.stopping=True;self.active=False;process=self.process;self.process=None;self.buffer=bytearray()
        if process and process.state()!=QProcess.ProcessState.NotRunning:
            process.write(b'stop\n');process.closeWriteChannel();process.terminate()
            timeout=QTimer(process);timeout.setSingleShot(True);timeout.timeout.connect(process.kill);process.finished.connect(timeout.stop);timeout.start(750)
