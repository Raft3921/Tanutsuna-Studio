"""Actual embedded camera lifecycle with a synthetic webcam (no personal camera)."""
import os
os.environ['QTWEBENGINE_CHROMIUM_FLAGS']='--use-fake-ui-for-media-stream --use-fake-device-for-media-stream'
import sys,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from studio.avatar_gl import configure_opengl
configure_opengl()
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from studio.camera_window import CameraWindow
from studio.tracking import TrackerServer
app=QApplication([]);server=TrackerServer();window=CameraWindow()
automatic='--automatic' in sys.argv
phase='start' if automatic else 'load';started=time.monotonic();result=[]
hidden_verified=False
tracking_started=None;initial_generation=0
def check():
    if time.monotonic()-started>50:
        result.append('timeout');window.stop();server.stop();app.exit(1);return
    window.view.page().runJavaScript("document.querySelector('#status')?.textContent || ''",received)
def received(status):
    global phase,tracking_started,initial_generation,hidden_verified
    if phase=='load' and status:
        window.view.page().runJavaScript("document.querySelector('#start').click()")
        phase='start'
    elif phase=='start' and ('顔を探しています' in status or '追跡中' in status):
        if tracking_started is None:
            tracking_started=time.monotonic();initial_generation=server.state.generation
        if time.monotonic()-tracking_started>=1.5:
            if automatic and (window.isVisible() or server.state.generation-initial_generation<8):
                result.append('hidden tracking stalled');window.stop();server.stop();app.exit(1);return
            if automatic:
                hidden_verified=True;window.open_tracker(server.start())
                phase='reopen';tracking_started=time.monotonic();initial_generation=server.state.generation
            else:
                window.view.page().runJavaScript("document.querySelector('#stop').click()")
                phase='stop'
    elif phase=='reopen' and time.monotonic()-tracking_started>=1.5:
        if server.state.generation-initial_generation<8:
            result.append('opening settings interrupted tracking');window.stop();server.stop();app.exit(1);return
        window.view.page().runJavaScript("document.querySelector('#stop').click()");phase='stop'
    elif phase=='stop' and '停止しました' in status:
        print(json.dumps({'embeddedCamera':'start/stop OK','messages':server.state.generation,'automatic':automatic,'visible':window.isVisible(),'hiddenAutomaticVerified':hidden_verified}))
        window.stop();server.stop();app.quit();return
    QTimer.singleShot(250,check)
window.open_tracker(server.start(),automatic=automatic);QTimer.singleShot(500,check)
code=app.exec()
if result:print(result)
sys.exit(code)
