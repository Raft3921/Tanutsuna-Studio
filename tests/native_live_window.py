"""Real OS window capture + real GPU V-body composite + local publisher; no YouTube broadcast."""
import os,sys,tempfile,time,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'vendor/cutraft-win/app')]
from studio.avatar_gl import configure_opengl
configure_opengl()
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage,QColor
from studio.main import StudioWindow
from studio.workspace import Workspace
from studio.avatar_offline import OfflineAvatar
from studio.live_av import av
from unittest.mock import patch
app=QApplication([]);temp=tempfile.TemporaryDirectory();root=Path(temp.name)
ws=Workspace(root/'settings.json',root/'projects');window=StudioWindow(ws,root)
fixture=QWidget();fixture.setWindowTitle('Capture fixture game');fixture.resize(640,360);fixture.setStyleSheet('background:#176d61;color:white;font-size:32px')
layout=QVBoxLayout(fixture);label=QLabel('WINDOW ONLY');layout.addWidget(label);fixture.show();window.show()
with patch('studio.live_panel.LivePanel.load_secret'):window.desk.navigate('live')
panel=window.desk._live;started=time.monotonic();phase='find';output=ROOT/'build/native-live-program.flv';result={}
def finish(error=''):
    result['beforeStopStatus']=panel.status.text();result['beforeStopCapture']=panel.capture.isActive();result['beforeStopPreview']=panel.previewing
    panel.stop_all();result['error']=error;result['phase']=phase;result['windowCount']=panel.windows.count();result['status']=panel.status.text();result['captureActive']=panel.capture.isActive();result['captureError']=panel.capture.errorString();result['previewing']=panel.previewing
    if panel.engine.thread:panel.engine.thread.join(5)
    if panel.engine.publisher:panel.engine.publisher.thread.join(5)
    panel.stop_preview();app.processEvents()
    if output.exists():
        with av.open(str(output)) as media:
            frames=list(media.decode(video=0));result['frames']=len(frames)
            if frames:
                last=frames[-1].to_ndarray(format='rgb24');result['differentPixels']=int(((last[:,:,0]>100)&(last[:,:,1]>80)).sum())
                QImage(last.data,last.shape[1],last.shape[0],last.strides[0],QImage.Format.Format_RGB888).copy().save(str(ROOT/'build/native-live-composite.png'))
    result['windowOnly']=True;result['networkDestination']='local FLV file; no YouTube transmission'
    (ROOT/'build/native-live-window.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    window.close();fixture.close();app.quit()
def check():
    global phase,started
    try:
        if time.monotonic()-started>10:finish('capture/encode timeout');return
        if phase=='find':
            panel.refresh_windows()
            idx=next((i for i in range(panel.windows.count()) if 'Capture fixture game' in panel.windows.itemText(i)),-1)
            if idx<0:QTimer.singleShot(300,check);return
            panel.windows.setCurrentIndex(idx);panel.avatar.setChecked(False)
            with patch('studio.live_audio.LiveAudio.start',lambda audio,*args,**kwargs:audio.ready.emit()),patch('studio.live_panel.QMediaDevices.audioInputs',return_value=[]):panel.prepare_preview()
            panel.avatar.setChecked(True);vt=window.desk.vtuber
            panel.renderer=OfflineAvatar(vt.viewport.model,size=512,samples=1)
            phase='capture'
        elif phase=='capture' and panel.game is not None:
            panel.preparing=False;panel.quality.setCurrentIndex(1)
            panel.tick();panel.engine.start(str(output),1280,720,avatar=True);phase='live';started=time.monotonic()
        elif phase=='live':
            label.setText(f'WINDOW ONLY {int((time.monotonic()-started)*10)}')
            if time.monotonic()-started>2.5:
                result['encoderError']=getattr(panel.engine,'last_error','');finish();return
    except Exception as exc:finish(type(exc).__name__+': '+str(exc));return
    QTimer.singleShot(100,check)
QTimer.singleShot(700,check)
app.exec();temp.cleanup()
if result.get('error') or result.get('frames',0)<10:sys.exit(1)
