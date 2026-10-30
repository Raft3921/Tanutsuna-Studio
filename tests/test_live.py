import os,sys,tempfile,time,threading,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'vendor/cutraft-win/app')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QColor
from studio.live_av import av
from studio.live_engine import LiveEngine,AudioRing,destination
from studio.live_audio import PCMDecoder
from studio.workspace import Workspace


class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_destinations_and_secret_validation(self):
        self.assertEqual(destination('rtmps://a.rtmps.youtube.com:443/live2','secret-key'),'rtmps://a.rtmps.youtube.com:443/live2/secret-key')
        for url in ['https://youtube.com/watch?v=video','rtmps://example.com/live2','rtmps://secret@example.com/']:
            with self.assertRaises(ValueError):destination(url,'key')
    def test_youtube_paste_formats_and_duplicate_keys(self):
        from studio.live_engine import stream_settings
        self.assertEqual(destination('rtmp://a.rtmp.youtube.com/live2','key'),'rtmp://a.rtmp.youtube.com:1935/live2/key')
        self.assertEqual(destination('rtmps://b.rtmps.youtube.com/live2/key',''),'rtmps://b.rtmps.youtube.com:443/live2/key')
        self.assertEqual(destination('rtmps://a.rtmps.youtube.com/live2/key','key'),'rtmps://a.rtmps.youtube.com:443/live2/key')
        with self.assertRaises(ValueError):destination('rtmps://a.rtmps.youtube.com/live2/one','two')
        with self.assertRaises(ValueError):destination('rtmp://a.rtmp.youtube.com/live2','')
        with self.assertRaises(ValueError):destination('rtmps://a.rtmps.youtube.com/wrong','key')
        server,key=stream_settings('rtmp://a.rtmp.youtube.com/live2/private-key')
        self.assertEqual(server,'rtmp://a.rtmp.youtube.com:1935/live2');self.assertEqual(key,'private-key')

    def test_camera_selection_is_shared_and_notifies_tracker(self):
        from unittest.mock import patch,Mock
        from PySide6.QtCore import QSettings
        from studio.camera_choice import CameraChoice
        settings=QSettings('TanutsunaStudio','Avatar');before=settings.value('cameraLabel')
        settings.remove('cameraLabel')
        first=second=None
        try:
            devices=[Mock(description=lambda:'Camera A'),Mock(description=lambda:'Camera B')]
            with patch('studio.camera_choice.QMediaDevices.videoInputs',return_value=devices):
                first=CameraChoice();second=CameraChoice();changed=[]
                second.selectedCameraChanged.connect(changed.append)
                first.setCurrentIndex(2)
                self.assertEqual(second.currentData(),'Camera B');self.assertEqual(changed,['Camera B'])
                self.assertEqual(settings.value('cameraLabel'),'Camera B')
        finally:
            if before is None:settings.remove('cameraLabel')
            else:settings.setValue('cameraLabel',before)
            for widget in (first,second):
                if widget:widget.deleteLater()
            self.app.processEvents()

    def test_stopped_window_process_reference_is_released(self):
        from unittest.mock import Mock
        from studio.window_source import NativeWindowSource
        source=NativeWindowSource();process=Mock();source.process=process;source.stopping=True
        source.ended(process,1)
        self.assertIsNone(source.process);source.stop();process.state.assert_not_called()

    def test_bounded_audio_and_pcm_conversion(self):
        ring=AudioRing();ring.push(np.ones((48000,2),np.float32));self.assertLessEqual(len(ring.data),12000)
        self.assertEqual(ring.dropped,36000);self.assertEqual(ring.read(1600).shape,(1600,2))
        decoder=PCMDecoder(48000,1,16,'int');samples=decoder.feed(np.full(1600,16384,dtype='<i2').tobytes())
        self.assertEqual(samples.shape,(1600,2));self.assertAlmostEqual(float(samples.mean()),.5,places=4)
    def test_real_encode_mix_archive_and_local_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ws=Workspace(root/'settings.json',root/'projects');project=ws.create('ライブテスト')
            engine=LiveEngine();result=[];engine.finished.connect(lambda *args:result.append(args))
            image=QImage(640,360,QImage.Format.Format_RGB32);image.fill(QColor('#b12739'))
            engine.submit_frame(image,image,{},{});engine.start(str(root/'program.flv'),640,360,project,False)
            deadline=time.monotonic()+2.3
            while engine.active and time.monotonic()<deadline:
                engine.inputs['voice'].push(np.full((480,2),.1,np.float32));self.app.processEvents();time.sleep(.01)
            engine.stop();engine.thread.join(8)
            if engine.publisher:engine.publisher.thread.join(8)
            self.app.processEvents()
            self.assertTrue(result);folder,take,error=result[0];self.assertFalse(error,getattr(engine,'last_error',error))
            self.assertEqual(take['fps'],60);self.assertEqual((take['width'],take['height']),(640,360))
            with av.open(str(folder/'screen.mp4')) as media:
                self.assertGreater(sum(1 for _ in media.decode(video=0)),10)
            with av.open(str(root/'program.flv')) as media:
                self.assertEqual(len(media.streams.video),1);self.assertEqual(len(media.streams.audio),1)
                self.assertGreater(sum(1 for _ in media.decode(video=0)),10)
            self.assertTrue((folder/'bgm-source.wav').exists());engine.shutdown()
            from studio.postprocess import finalize
            ready=finalize(folder,take,threading.Event())
            self.assertEqual(set(ready['files']),{'screen','desktop','voice','bgm','sfx'})
            from studio.main import StudioWindow
            from studio.take_import import import_take
            window=StudioWindow(ws,root)
            try:
                window.desk.select(project)
                self.assertTrue(import_take(window,ws,project,folder,ready))
                self.assertEqual(len(window.sequence.clips),5)
                self.assertEqual(window.sequence.fps_num,60)
                self.assertTrue(import_take(window,ws,project,folder,ready))
                self.assertEqual(len(window.sequence.clips),5)
            finally:window.close();self.app.processEvents()
    def test_publisher_stop_never_retries_after_stop(self):
        from unittest.mock import patch
        from studio.live_engine import Publisher
        entered=threading.Event();attempts=[]
        def fail(*args):attempts.append(1);entered.set();raise OSError('offline')
        with patch('studio.live_engine.PacketWriter',side_effect=fail):
            publisher=Publisher('local',640,360,lambda _:None,(None,None));publisher.start()
            self.assertTrue(entered.wait(2));publisher.close();publisher.thread.join(2)
            self.assertFalse(publisher.thread.is_alive());self.assertEqual(len(attempts),1)

    def test_missing_window_keeps_waiting_image_without_screen_fallback(self):
        from studio.main import StudioWindow
        from unittest.mock import patch,Mock
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ws=Workspace(root/'settings.json',root/'projects');window=StudioWindow(ws,root)
            try:
                with patch('studio.live_panel.LivePanel.load_secret'),patch('studio.live_panel.LivePanel.refresh_windows'):
                    window.desk.navigate('live')
                panel=window.desk._live;panel.avatar.setChecked(False);panel.previewing=True;panel.target=Mock()
                panel.target.isValid.return_value=False;panel.game=QImage(10,10,QImage.Format.Format_RGB32)
                with patch.object(panel.capture,'start') as start:
                    panel.monitor_window();self.assertIsNone(panel.game);start.assert_not_called()
                panel.tick();self.assertIsNotNone(panel.engine.latest)
            finally:window.close();self.app.processEvents()

    def test_home_has_three_choices_and_live_needs_no_project(self):
        from studio.main import StudioWindow
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ws=Workspace(root/'settings.json',root/'projects');window=StudioWindow(ws,root)
            try:
                self.assertIsNone(window.desk.current);self.assertEqual(window.desk.current_page,'home')
                with patch('studio.live_panel.LivePanel.load_secret'),patch('studio.live_panel.LivePanel.refresh_windows'):
                    window.desk.navigate('live')
                self.assertEqual(window.desk.current_page,'live');self.assertEqual(ws.available_projects(),[])
                self.assertFalse(window.desk._live.record.isChecked())
            finally:window.close();self.app.processEvents()

if __name__=='__main__':unittest.main()
