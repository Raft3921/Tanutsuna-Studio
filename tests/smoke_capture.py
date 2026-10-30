"""Run explicitly on a Mac desktop; requests OS screen/microphone permissions."""
from pathlib import Path
import os,sys,json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor/cutraft-win/app'))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from studio.recording import Recorder
from studio.workspace import Workspace
from studio.jobs import Jobs
from studio.postprocess import finalize,metadata
app=QApplication([])
workspace=Workspace(ROOT/'build/capture-smoke/settings.json',ROOT/'build/capture-smoke/videos')
project=workspace.create('収録エンジン実機確認')
recorder=Recorder();jobs=Jobs()
def started(folder,take):
    print('STARTED',folder,flush=True);QTimer.singleShot(2000,recorder.stop)
def stopped(folder,take):
    print('STOPPED',flush=True)
    jobs.submit('finalize',lambda cancel:finalize(folder,take,cancel,False))
def complete(tag,result,error):
    print(json.dumps({'error':error,'take':result},ensure_ascii=False),flush=True)
    jobs.shutdown();app.exit(1 if error else 0)
recorder.started.connect(started);recorder.stopped.connect(stopped)
recorder.failed.connect(lambda e:(print('FAILED',e,flush=True),app.exit(1)))
jobs.finished.connect(complete)
QTimer.singleShot(0,lambda:recorder.start(project,{'desktop':True,'voice':True,'avatar':False,'transcribe':False,'display':0,'mic':''}))
QTimer.singleShot(60000,lambda:recorder.abort('Smoke timeout'))
sys.exit(app.exec())
