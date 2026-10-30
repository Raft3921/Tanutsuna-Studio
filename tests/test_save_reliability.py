"""Captured media survives failed conversion; mouth tubes retain their thickness."""
import os,sys,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/cutraft-win/app')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from studio.workspace import Workspace
from studio.takes import create_take,write_take,load_take,recover_captures,takes_for
from studio.postprocess import finalize,render_avatar_take
from studio.avatar_model import AvatarModel
from studio.tracking import Pose,parse_pose
from studio.mouth import DEFAULT_SHAPES,opening

class SaveReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.workspace=Workspace(self.root/'settings.json',self.root/'videos')
        self.project=self.workspace.create('保存の回帰確認')
    def captured(self):
        folder,take=create_take(self.project,{'avatar':True,'desktop':False,'voice':False},temporary=True)
        (folder/'screen.mp4').write_bytes(b'x'*256)
        (folder/'vtuber-motion.jsonl').write_text('{"orbit":0}\n{"time":0,"pose":{}}\n')
        take.update(state='captured',duration=1,timestamps={'screen':0});write_take(folder,take)
        return folder,take
    def test_failed_conversion_keeps_all_sources_in_final_folder(self):
        folder,take=self.captured()
        with patch('studio.postprocess.metadata',side_effect=RuntimeError('decoder failed')):
            with self.assertRaises(RuntimeError):finalize(folder,take,threading.Event())
        entries=takes_for(self.project);self.assertEqual(len(entries),1)
        saved,data=entries[0];self.assertEqual(data['state'],'incomplete')
        self.assertTrue((saved/'screen.mp4').exists());self.assertTrue((saved/'vtuber-motion.jsonl').exists())
        self.assertFalse(folder.exists())
    def test_interrupted_capture_becomes_visible_and_recovery_is_idempotent(self):
        folder,take=self.captured();recover_captures(self.project);recover_captures(self.project)
        self.assertEqual(len(takes_for(self.project)),1);self.assertFalse(folder.exists())
    def test_missing_worker_output_never_marked_complete(self):
        folder,take=self.captured();take['avatar_pending']=True
        with patch('studio.postprocess.run_command'):
            with self.assertRaises(ValueError):render_avatar_take(folder,take,threading.Event())
        self.assertNotIn('vtuber',take['files']);self.assertTrue(take['avatar_pending'])
    def test_vowel_tubes_preserve_radius_during_every_intermediate_opening(self):
        model=AvatarModel();mesh=next(m for m in model.meshes if m['name']=='mouth_lip')
        idx=np.asarray(mesh['procedural_indices']);t=idx//12*2*np.pi/64
        for width,height in DEFAULT_SHAPES.values():
            for jaw in np.linspace(0,1,11):
                pose=Pose(jawOpen=float(jaw),mouthWidth=width,mouthHeight=height)
                points=model.vertices(mesh,pose);x,y=opening(t,jaw,width,height)
                # Cross-section projected into the face plane is radius*cos(phi).
                radial=np.hypot(points[:,0]-x,points[:,1]-y)
                expected=np.abs(.021*pose.mouthStroke*np.cos(idx%12*2*np.pi/12))
                np.testing.assert_allclose(radial,expected,atol=1e-6)
                self.assertTrue(np.isfinite(points).all())
    def test_every_vowel_closes_without_a_gap(self):
        upper=np.linspace(-np.pi/2,np.pi/2,65)
        lower=np.pi-upper
        for width,height in DEFAULT_SHAPES.values():
            x,y=opening(upper,0,width,height);xx,yy=opening(lower,0,width,height)
            np.testing.assert_allclose(x,xx,atol=1e-10);np.testing.assert_allclose(y,yy,atol=1e-10)

    def test_old_motion_defaults_keep_mouth_dimensions(self):
        pose=parse_pose({'jawOpen':.5});self.assertEqual(pose.mouthWidth,1);self.assertEqual(pose.mouthHeight,1)

if __name__=='__main__':unittest.main()
