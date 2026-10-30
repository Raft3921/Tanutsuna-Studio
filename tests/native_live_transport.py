"""Local RTMP ingest, deliberate disconnect/reconnect and independent archive continuity."""
import os,sys,time,tempfile,socket,subprocess,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'vendor/cutraft-win/app')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QColor
from studio.live_engine import LiveEngine
from studio.workspace import Workspace
from cutraft.media_cache import ffmpeg_executable
app=QApplication([]);report={};receiver=None;engine=LiveEngine()
def pump(seconds):
    until=time.monotonic()+seconds
    while time.monotonic()<until:app.processEvents();time.sleep(.01)
try:
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);ws=Workspace(root/'settings.json',root/'projects');project=ws.create('再接続テスト')
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'rtmp://127.0.0.1:{port}/live/test'
        logs=[]
        def server(index):
            log=(ROOT/f'build/local-ingest-{index}.log').open('wb');logs.append(log)
            return subprocess.Popen([ffmpeg_executable(),'-hide_banner','-loglevel','error','-listen','1','-i',url,'-c','copy','-y',str(root/f'network{index}.flv')],stdout=log,stderr=log)
        receiver=server(1);pump(.3);states=[];result=[];engine.state.connect(states.append);engine.finished.connect(lambda *args:result.append(args))
        image=QImage(640,360,QImage.Format.Format_RGB32);image.fill(QColor('#166a81'));engine.submit_frame(image,image,{},{});engine.start(url,640,360,project,False)
        pump(3);receiver.terminate();receiver.wait(3);receiver=None;pump(.7)
        receiver=server(2);pump(7)
        report['states']=states;report['activeDuringDisconnect']=engine.active
        engine.stop();engine.thread.join(8);engine.publisher.thread.join(8);pump(.2)
        receiver.wait(5);receiver=None
        folder,take,error=result[0];report.update(recordingSeconds=take['duration'],error=error,reconnected=states.count('live')>=2,network='localhost RTMP only; no YouTube or real stream key')
        assert not error,getattr(engine,'last_error',error)
        assert take['duration']>=8,take['duration']
        assert report['reconnected'],states
        assert not engine.publisher.thread.is_alive()
finally:
    engine.stop()
    if receiver and receiver.poll() is None:receiver.terminate();receiver.wait(5)
    for log in locals().get('logs',[]):log.close()
    (ROOT/'build/live-reconnect-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
