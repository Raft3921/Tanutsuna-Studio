import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'vendor/cutraft-win/app'));sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from studio.takes import create_take, load_take, write_take, audio_filter, takes_for, discard_pending
from studio.workspace import Workspace
from studio.postprocess import finalize, transcribe, metadata
from cutraft.media_cache import ffmpeg_executable
from studio.jobs import run_command


class RecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=Workspace(self.root/'settings.json',self.root/'videos')
        self.project=self.workspace.create('1080p収録')
        self.options={'desktop':True,'voice':True,'avatar':False,'transcribe':False,'display':0,'mic':''}

    def test_manifest_recovery_and_path_escape_rejected(self):
        folder,take=create_take(self.project,self.options)
        self.assertEqual((take['width'],take['height'],take['fps']),(1920,1080,50))
        take['files']['voice']='../../secret.mp3';write_take(folder,take)
        with self.assertRaises(ValueError):load_take(folder)
        self.assertIn('atrim=start=0.250000',audio_filter(-.25,1))
        self.assertIn('adelay=250',audio_filter(.25,1))

    def make_take(self, avatar=False):
        folder,take=create_take(self.project,{**self.options,'avatar':avatar})
        for role in ('desktop','voice'):
            run_command([ffmpeg_executable(),'-loglevel','error','-nostdin','-y','-f','lavfi','-i',
                'sine=frequency=440:sample_rate=48000','-t','0.3',folder/(role+'-source.wav')],folder/'test.log')
        run_command([ffmpeg_executable(),'-loglevel','error','-nostdin','-y','-f','lavfi','-i',
            'color=c=blue:s=1920x1080:r=50','-t','0.3','-c:v','libx264','-threads','1',folder/'screen.mp4'],folder/'test.log')
        take['timestamps']={'screen':100,'desktop':100.04,'voice':99.98};take['duration']=.3
        take['state']='captured';write_take(folder,take)
        return folder,take

    def test_separate_mp3_finalize_and_import_all_tracks_without_duplicates(self):
        from studio.main import StudioWindow
        from studio.take_import import import_take
        folder,take=self.make_take()
        take=finalize(folder,take,threading.Event())
        self.assertEqual(take['state'],'ready')
        self.assertEqual(set(take['files']),{'screen','desktop','voice'})
        self.assertEqual(metadata(folder/'screen.mp4')['size'],(1920,1080))
        self.assertEqual(metadata(folder/'screen.mp4')['fps'],50)
        from cutraft.app import configure_app
        configure_app(self.app)
        window=StudioWindow(self.workspace,self.root)
        try:
            window.desk.projects.selectRow(0);self.app.processEvents()
            self.assertTrue(import_take(window,self.workspace,self.project,folder,take))
            self.assertEqual(len(window.sequence.clips),3)
            self.assertEqual({c.track for c in window.sequence.clips},{0,-1,-2})
            self.assertTrue(all(c.start==0 for c in window.sequence.clips))
            self.assertTrue(import_take(window,self.workspace,self.project,folder,take))
            self.assertEqual(len(window.sequence.clips),3)
            self.assertEqual(window.sequence.fps_num,50)
            import shutil
            shutil.copy2(folder/'voice-source.wav',folder/'yukkuri.wav')
            take['files']['yukkuri']='yukkuri.wav'
            take.setdefault('media',{})['yukkuri']={'duration':.3}
            self.assertTrue(import_take(window,self.workspace,self.project,folder,take))
            self.assertEqual(len(window.sequence.clips),4)
            voice_asset=next(a for a in window.library.assets.values() if a.path==str((folder/'voice.mp3').resolve()))
            self.assertTrue(next(c for c in window.sequence.clips if c.asset_id==voice_asset.id).muted)
            editor=window.loaded_editor
            editor.commit('収録をまとめる',lambda:editor.library.create_compound({c.id for c in editor.sequence.clips},'同期収録'))
            self.assertEqual(len(window.sequence.clips),1)
            self.assertTrue(import_take(window,self.workspace,self.project,folder,take))
            self.assertEqual(len(window.sequence.clips),1)
            # Removing every placed segment must not resurrect the original take.
            child=next(s for s in window.library.sequences if s.kind=='compound')
            child.clips=[]
            self.assertTrue(import_take(window,self.workspace,self.project,folder,take))
            self.assertEqual(len(window.sequence.clips),1)
        finally:window.close();self.app.processEvents()

    def test_missing_audio_is_reported_not_silently_faked(self):
        folder,take=self.make_take()
        (folder/'voice-source.wav').unlink()
        result=finalize(folder,take,threading.Event())
        self.assertNotIn('voice',result['files'])
        self.assertTrue(any('voice' in w for w in result['warnings']))

    def test_missing_edit_folder_rebuilt_and_recording_button_opens_timeline(self):
        import shutil
        from studio.main import StudioWindow
        from cutraft.storage import load_library
        folder,take=self.make_take();finalize(folder,take,threading.Event())
        shutil.rmtree(self.workspace.library_path(self.project).parent)
        window=StudioWindow(self.workspace,self.root)
        try:
            window.desk.select(self.project);window.desk.navigate('record')
            panel=window.desk.recording_panel
            self.assertTrue(panel.open_editor(),panel.status.text())
            self.assertEqual(window.desk.current_page,'edit')
            self.assertEqual(len(window.sequence.clips),3)
            self.assertEqual(len(load_library(str(self.workspace.library_path(self.project))).active.clips),3)
            # Removing a folder on disk must also remove references and cached placements.
            shutil.rmtree(folder)
            window.loaded_editor.background_save.drain()
            self.assertEqual(window.reconcile_media(self.project),3)
            self.assertEqual(window.library.assets,{})
            self.assertEqual(window.sequence.clips,[])
            self.assertEqual(window.loaded_editor.undo_stack,[])
            self.assertFalse(hasattr(panel,'auto_text'))
            self.assertFalse(hasattr(panel,'yukkuri'))
            self.assertFalse(folder.exists())
            window.desk.refresh_media();self.assertEqual(window.desk.media.rowCount(),0)
        finally:window.close();self.app.processEvents()

    def test_unfinished_capture_is_not_published_and_failure_is_discarded(self):
        folder,take=create_take(self.project,self.options,temporary=True)
        self.assertEqual(takes_for(self.project),[])
        with self.assertRaises(ValueError):finalize(folder,take,threading.Event())
        self.assertFalse(folder.exists())
        folder,take=create_take(self.project,self.options,temporary=True)
        discard_pending(folder);self.assertFalse(folder.exists())
        with self.assertRaises(ValueError):write_take(folder,take)

    def test_complete_staged_capture_publishes_and_media_can_be_deleted(self):
        from studio.main import StudioWindow
        folder,take=self.make_take()
        pending=Path(self.project['folder'])/'.pending-recordings'/folder.name
        pending.parent.mkdir();folder.rename(pending)
        result=finalize(pending,take,threading.Event())
        self.assertFalse(pending.exists());self.assertTrue(folder.is_dir())
        window=StudioWindow(self.workspace,self.root)
        try:
            window.desk.select(self.project);window.show_editor()
            self.assertEqual(len(window.sequence.clips),3)
            ticket=window.delete_data(paths=[folder/'voice.mp3'])
            self.assertFalse((folder/'voice.mp3').exists())
            self.assertEqual(len(window.sequence.clips),2)
            window.load_project(self.project)
            self.assertEqual(len(window.sequence.clips),2)
            self.assertNotIn('voice',load_take(folder)['files'])
            self.assertTrue(ticket)
        finally:window.close();self.app.processEvents()

    def test_aques_arguments_are_structured_and_output_committed(self):
        from studio.postprocess import synthesize
        executable=self.root/'AquesTalkPlayer.exe';executable.write_bytes(b'fake')
        folder,take=create_take(self.project,self.options)
        calls=[]
        def run(args,log,cancel,cwd):
            calls.append(args)
            # A valid WAV file is produced by the external engine in the real flow.
            import wave
            with wave.open(str(folder/'yukkuri.partial.wav'),'wb') as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*160)
        with patch('studio.postprocess.sys.platform','win32'),patch('studio.postprocess.run_command',side_effect=run):
            result=synthesize(folder,'こんにちは\nゆっくりです',str(executable),'れいむ',threading.Event())
        self.assertTrue(Path(result).is_file())
        self.assertEqual(calls[0][1],'/F');self.assertIn('/P',calls[0])
        self.assertEqual((folder/'yukkuri-input.txt').read_bytes().decode('cp932'),'こんにちは\r\nゆっくりです')

if __name__=='__main__':unittest.main()
