from pathlib import Path
import sys
import json
import math
import time
import unittest
import urllib.request
import urllib.error
import tempfile
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from studio.tracking import Pose,parse_pose,smooth_pose,TrackerServer
from studio.avatar_model import AvatarModel


class TrackingTests(unittest.TestCase):
    def test_camera_home_is_served_through_bundle_symlink(self):
        import studio.tracking as tracking
        with tempfile.TemporaryDirectory() as folder:
            link=Path(folder)/'assets';link.symlink_to(tracking.ASSETS,target_is_directory=True)
            with patch.object(tracking,'ASSETS',link):
                server=TrackerServer();server.start()
                try:
                    with urllib.request.urlopen(server.url) as response:
                        self.assertIn('たぬつな カメラトラッカー',response.read().decode())
                finally:server.stop()

    def test_local_bridge_requires_origin_and_token_and_expires_face(self):
        server=TrackerServer();server.start();self.addCleanup(server.stop)
        origin=server.url.split('/#')[0]
        data=json.dumps({'detected':True,'pose':{'yaw':28,'jawOpen':.8}}).encode()
        def post(token,origin_header):
            req=urllib.request.Request(origin+'/pose',data=data,headers={'Content-Type':'application/json','X-Tracking-Token':token,'Origin':origin_header})
            return urllib.request.urlopen(req)
        with self.assertRaises(urllib.error.HTTPError):post('wrong',origin)
        with self.assertRaises(urllib.error.HTTPError):post(server.token,'https://example.com')
        with post(server.token,origin) as response:self.assertEqual(response.status,204)
        pose,detected,_,_=server.state.snapshot()
        self.assertTrue(detected);self.assertEqual(pose.yaw,28)
        server.state.last_seen-=1
        self.assertFalse(server.state.snapshot()[1])
        with urllib.request.urlopen(origin+'/tracker/face_landmarker.task') as response:self.assertGreater(len(response.read()),1000000)
        with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(origin+'/camera/%2e%2e/%2e%2e/desk.py')

    def test_nonfinite_data_rejected_and_smoothing_stays_bounded(self):
        for value in [float('nan'),float('inf'),'30',True]:
            with self.assertRaises(ValueError):parse_pose({'yaw':value})
        self.assertEqual(parse_pose({'yaw':999,'jawOpen':-1}).yaw,65)
        self.assertEqual(parse_pose({'jawOpen':-1}).jawOpen,0)
        pose=smooth_pose(Pose(yaw=0),Pose(yaw=40),1/30)
        self.assertGreater(pose.yaw,0);self.assertLess(pose.yaw,40)


class ModelTests(unittest.TestCase):
    def test_positive_pitch_matches_camera_downward_rotation(self):
        model=AvatarModel()
        neutral=model.transforms(Pose(),0)['head']
        down=model.transforms(Pose(pitch=20),0)['head']
        front=np.array([0,1.18,.79,1])
        self.assertLess((down@front)[1],(neutral@front)[1])

    def test_model_triangle_budget(self):
        self.assertLess(sum(len(m['positions'])//3 for m in AvatarModel().meshes),30000)

    def test_mesh_has_volume_and_expressions_change_geometry(self):
        model=AvatarModel()
        all_points=np.concatenate([m['positions'] for m in model.meshes])
        self.assertTrue(np.all(np.ptp(all_points,axis=0)>.9))
        mouth=next(m for m in model.meshes if m['name']=='mouth_cavity')
        closed=model.vertices(mouth,Pose(jawOpen=0));opened=model.vertices(mouth,Pose(jawOpen=1))
        self.assertGreater(np.ptp(opened[:,1]),np.ptp(closed[:,1])+.35)
        names=[m['name'] for m in model.meshes]
        self.assertFalse(any(n.startswith(('eye_','paw_','foot_','nose')) for n in names))
        self.assertTrue(model.data['fixedEyes'])
        self.assertTrue(any(m.get('texture') for m in model.meshes))
        self.assertTrue(any(m.get('outline') for m in model.meshes))
        transforms=model.transforms(Pose(yaw=40),0)
        self.assertFalse(np.allclose(transforms['head'],np.eye(4)))
        self.assertTrue(np.all(np.isfinite(transforms['head'])))

    def test_ear_outline_is_convex_and_does_not_outline_the_inner_bowl(self):
        model=AvatarModel()
        for side in ['L','R']:
            mesh=next(m for m in model.meshes if m['name']=='ear_'+side+'_outline')
            pivot=np.array(model.data['bones']['ear_'+side]['pivot'])
            inward=np.sum((mesh['positions']-pivot)*mesh['normals'],axis=1)
            self.assertLess(float(inward.max()),-.10)

    def test_glb_contains_skin_and_morph_targets(self):
        import struct
        file=ROOT/'studio/assets/tanuki/tanuki.glb'
        data=file.read_bytes();self.assertEqual(data[:4],b'glTF')
        length,kind=struct.unpack_from('<II',data,12)
        gltf=json.loads(data[20:20+length])
        self.assertTrue(gltf.get('skins'))
        targets=[p for mesh in gltf['meshes'] for p in mesh['primitives'] if p.get('targets')]
        self.assertGreaterEqual(len(targets),3)
        names=[node.get('name') for node in gltf['nodes']]
        self.assertIn('head',names);self.assertIn('ear_L',names)


if __name__=='__main__':unittest.main()
