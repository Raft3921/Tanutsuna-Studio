"""Native Qt regression: finalize V media and enter an existing editor repeatedly."""
from pathlib import Path
import json,sys,time,tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/cutraft-win/app')]
from studio.avatar_gl import configure_opengl
configure_opengl()
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtCore import QEvent,QCoreApplication
from studio.workspace import Workspace
from studio.main import StudioWindow
from studio.takes import create_take,write_take,load_take
from studio.jobs import run_command,ffmpeg_executable
app=QApplication([])
with tempfile.TemporaryDirectory() as temporary:
 root=Path(temporary);w=Workspace(root/'settings.json',root/'videos');project=w.create('保存反復')
 window=StudioWindow(w,root);window.desk.select(project);window.show();window.show_editor()
 panel=window.desk.recording_panel;panel.project=project
 for cycle in range(5):
  folder,take=create_take(project,dict(desktop=False,voice=False,avatar=True,transcribe=False),temporary=True)
  run_command([ffmpeg_executable(),'-loglevel','error','-y','-f','lavfi','-i','color=c=blue:s=1920x1080:r=50','-t','0.12','-c:v','libx264','-threads','1',folder/'screen.mp4'],folder/'source.log')
  take.update(duration=.12,timestamps={'screen':0},state='captured');write_take(folder,take)
  panel.recorder.epoch=time.perf_counter();panel.capture_started(folder,take)
  panel.capture_stopped(folder,take)
  deadline=time.monotonic()+90
  while panel.busy and time.monotonic()<deadline:
   app.processEvents();time.sleep(.01)
  assert not panel.busy,'save timeout'
  saved=load_take(panel.folder)
  assert saved['state']=='ready' and not saved.get('avatar_pending'),saved
  assert (panel.folder/saved['files']['vtuber']).stat().st_size>128
  assert len(window.sequence.clips)==(cycle+1)*2,len(window.sequence.clips)
  # Exercise native widget destruction while the editor is present.
  for _ in range(50):
   child=QWidget(window);child.setProperty('cycle',cycle);child.deleteLater()
  QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
  print('saved and imported',cycle+1,flush=True)
 window.close();app.processEvents()
print('native save cycles: 5/5 OK',flush=True)
