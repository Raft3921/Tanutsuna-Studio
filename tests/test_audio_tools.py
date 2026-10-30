import os,sys,unittest,tempfile,threading,wave,math,struct
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/cutraft-win/app')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from cutraft.model import Library,Asset,Clip,uid
from cutraft.storage import save_library,load_library
from studio.audio_tools import ripple_cut,cut_library,apply_mix,analyze_silence


class AudioToolsTests(unittest.TestCase):
    def library(self):
        lib=Library();seq=lib.active;seq.fps_num=50;seq.video_tracks=2;seq.audio_tracks=3
        for key,audio,video,path in [('screen',False,True,'screen.mp4'),('voice',True,False,'voice.mp3'),('desktop',True,False,'desktop.mp3'),('bgm',True,False,'song.mp3')]:
            lib.assets[key]=Asset(key,path,6000,has_audio=audio,has_video=video,payload={'studio_audio_role':key} if audio else {})
        seq.clips=[Clip('screen','screen',0,0,250,track=0),Clip('voice','voice',0,0,250,track=-1),Clip('desktop','desktop',0,0,250,track=-2),Clip('bgm','bgm',0,0,300,track=-3)]
        return lib

    def test_global_ripple_keeps_video_voice_game_and_bgm_in_sync(self):
        lib=self.library();seq=lib.active
        seq.clips[0].transform_keys={'x':{0:0,249:249}}
        seq.clips[1].volume_keys={0:0,249:1}
        ripple_cut(seq,[(50,100),(150,175)])
        for role in ('screen','voice','desktop'):
            self.assertEqual([(c.start,c.source_in,c.duration) for c in seq.clips if c.asset_id==role],[(0,0,50),(50,100,50),(100,175,75)])
        self.assertEqual(seq.end,225);lib.validate()
        screen=[c for c in seq.clips if c.asset_id=='screen'][1]
        self.assertEqual(screen.value_at('x',100),100)
        voice=[c for c in seq.clips if c.asset_id=='voice'][1]
        self.assertAlmostEqual(voice.gain_at(100),lib.assets['voice'].duration_ms and 10**((-96+96*100/249)/20))

    def test_trimmed_and_moved_clip_cuts_advance_source_only_by_removed_time(self):
        lib=self.library();seq=lib.active;seq.clips=[Clip('a','voice',100,40,100,track=-1),Clip('b','screen',230,0,50,track=0)]
        ripple_cut(seq,[(120,145)])
        self.assertEqual([(c.start,c.source_in,c.duration) for c in seq.clips],[(100,40,20),(120,85,55),(205,0,50)])
        lib.validate()

    def test_compound_cut_shortens_parent_and_preserves_wrapper_source_keys(self):
        lib=self.library();parent=lib.active
        child_clip=lib.create_compound({'screen','voice','desktop'},'収録ひとまとめ')
        child=next(s for s in lib.sequences if s.id==lib.assets[child_clip.asset_id].payload['sequence_id'])
        child_clip.transform_keys={'x':{0:0,249:249}}
        cut_library(lib,child.id,[(50,100)])
        self.assertEqual(child.end,200);self.assertEqual(parent.end,250)
        wrappers=[c for c in parent.clips if c.asset_id==child_clip.asset_id]
        self.assertEqual([(c.start,c.source_in,c.duration) for c in wrappers],[(0,0,50),(50,50,150)])
        self.assertEqual(wrappers[1].value_at('x',50),100)
        lib.validate()

    def test_role_mix_preserves_fades_and_roundtrips_without_touching_other_versions(self):
        lib=self.library();seq=lib.active;seq.clips[1].volume_keys={0:0,249:1}
        from copy import deepcopy
        other=deepcopy(seq);other.id=uid();lib.sequences.append(other)
        # IDs must remain globally unique only within a sequence in this format.
        for c in other.clips:c.id=uid()
        apply_mix(lib,seq,{'voice':'voice','desktop':'desktop','bgm':'bgm'},{'voice':1,'desktop':.4,'bgm':.2})
        self.assertEqual(seq.clips[2].volume,.4);self.assertEqual(other.clips[2].volume,1)
        self.assertEqual(seq.clips[1].volume_keys,{0:0,249:1})
        with tempfile.TemporaryDirectory() as temp:
            file=str(Path(temp)/'main.cutraft');save_library(lib,file);loaded=load_library(file)
            self.assertEqual(loaded.assets['voice'].payload['studio_audio_role'],'voice')
            self.assertEqual(loaded.active.clips[2].volume,.4)

    def test_bgm_replacement_uses_dedicated_track_and_keeps_voice(self):
        lib=self.library();seq=lib.active
        lib.assets['new']=Asset('new','new.mp3',1000,has_video=False,has_audio=True)
        apply_mix(lib,seq,{'new':'bgm'},{'bgm':.2},'new')
        self.assertFalse(any(c.asset_id=='bgm' for c in seq.clips))
        new=next(c for c in seq.clips if c.asset_id=='new')
        self.assertEqual((new.start,new.duration,new.volume),(0,50,.2));self.assertNotIn(new.track,(-1,-2))
        self.assertTrue(any(c.asset_id=='voice' for c in seq.clips));lib.validate()

    def test_ffmpeg_silence_analysis_honors_trim_and_padding(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'voice.wav';rate=8000
            with wave.open(str(file),'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(rate)
                stream.writeframes(b''.join(struct.pack('<h',int(12000*math.sin(2*math.pi*440*i/rate)) if i<rate or i>=2*rate else 0) for i in range(3*rate)))
            lib=self.library();lib.assets['voice'].path=str(file)
            lib.active.clips=[Clip('ref','voice',100,25,100,track=-1)]
            ranges=analyze_silence(lib,lib.active.id,{'ref'},-38,.5,.1,threading.Event())
            self.assertEqual(len(ranges),1)
            self.assertTrue(129<=ranges[0][0]<=131,ranges);self.assertTrue(169<=ranges[0][1]<=171,ranges)

if __name__=='__main__':unittest.main()

class AudioDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from studio.main import StudioWindow
        from studio.workspace import Workspace
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.ws=Workspace(root/'settings.json',root/'videos');self.project=self.ws.create('音の調整')
        self.window=StudioWindow(self.ws,root);self.window.desk.select(self.project);self.window.desk.open_editor()
        self.addCleanup(lambda:(self.window.close(),self.app.processEvents()))
        self.editor=self.window.loaded_editor
        self.asset=Asset('voice',str(root/'voice.mp3'),2000,has_video=False,has_audio=True,payload={'studio_audio_role':'voice'})
        self.editor.library.assets['voice']=self.asset
        self.editor.sequence.clips=[Clip('voice','voice',0,0,100,track=-1)]

    def test_mixer_roles_volume_save_and_undo(self):
        from studio.audio_dialogs import MixerDialog
        dialog=MixerDialog(self.editor);dialog.factors['voice'].setValue(60);dialog.apply_mix()
        self.assertAlmostEqual(self.editor.sequence.clips[0].volume,.6)
        self.editor.undo();self.assertEqual(self.editor.sequence.clips[0].volume,1)
        dialog.deleteLater()

    def test_silence_apply_is_undoable_and_stale_results_are_blocked(self):
        from studio.audio_dialogs import SilenceDialog
        dialog=SilenceDialog(self.editor);dialog.ranges=[(20,40)];dialog.revision=self.editor.revision-1
        dialog.apply_result();self.assertEqual(self.editor.sequence.end,100)
        dialog.ranges=[(20,40)];dialog.revision=self.editor.revision;dialog.apply_result()
        self.assertEqual(self.editor.sequence.end,80)
        self.editor.undo();self.assertEqual(self.editor.sequence.end,100)
        dialog.deleteLater()

    def test_bgm_candidates_are_not_placed_or_duplicated(self):
        import time
        paths=[]
        # Already registered media exercise the same candidate collection and commit path.
        self.editor.sequence.clips=[];self.editor.library.assets={}
        for index in range(2):
            file=Path(self.project['folder'])/'02-assets'/f'候補{index}.mp3';file.write_bytes(b'test candidate')
            key=f'mp3-{index}';self.editor.library.assets[key]=Asset(key,str(file),1000,has_audio=True,has_video=False)
            paths.append(str(file))
        self.editor.save(False)
        self.window.import_files(paths,audio_role='bgm',place=False)
        deadline=time.monotonic()+4
        while self.window.pending_media is not None and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.01)
        self.assertIsNone(self.window.pending_media)
        self.assertEqual(self.editor.sequence.clips,[])
        self.assertEqual(len(self.editor.library.assets),2)
        self.assertTrue(all(a.payload['studio_audio_role']=='bgm' for a in self.editor.library.assets.values()))
