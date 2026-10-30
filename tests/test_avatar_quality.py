import os
import sys
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor/cutraft-win/app'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from cutraft.model import Asset,Clip,uid
from cutraft.storage import load_library,save_library
from cutraft.media_cache import ffmpeg_executable,process_options
from cutraft.video_effects import new_effect
from studio.workspace import Workspace
from studio.takes import create_take,write_take


class AvatarQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.render=cls.root/'render';cls.render.mkdir()
        (cls.render/'take.json').write_text(json.dumps({'duration':.04}))
        (cls.render/'vtuber-motion.jsonl').write_text('{"orbit":0}\n{"time":0,"pose":{"jawOpen":0.6}}\n')
        subprocess.run([sys.executable,str(ROOT/'run.py'),'--studio-worker','avatar',str(cls.render)],
            cwd=ROOT,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=90)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_encoded_alpha_keeps_smooth_edge_without_green(self):
        data=subprocess.check_output([ffmpeg_executable(),'-loglevel','error','-i',str(self.render/'vtuber.mov'),
            '-frames:v','1','-pix_fmt','rgba','-f','rawvideo','pipe:1'],**process_options())
        pixels=np.frombuffer(data,dtype=np.uint8).reshape(1080,1080,4)
        alpha=pixels[:,:,3];edge=(alpha>0)&(alpha<255)
        self.assertGreater(int((alpha==0).sum()),100000)
        self.assertGreater(int(edge.sum()),1000)
        self.assertGreater(len(np.unique(alpha)),8)
        rgb=pixels[:,:,:3].astype(int)
        self.assertFalse(np.any((rgb[:,:,1]>rgb[:,:,0]+20)&edge))

    def test_upgrade_reuses_placement_and_removes_only_chroma_key(self):
        from studio.main import StudioWindow
        from studio.take_import import import_take
        root=self.root/'migration';w=Workspace(root/'settings.json',root/'videos');p=w.create('透過へ更新')
        folder,take=create_take(p,{'avatar':True})
        for file in ('vtuber.mp4','vtuber.mov'):shutil.copy2(self.render/'vtuber.mov',folder/file)
        library=load_library(str(w.library_path(p)));aid=uid()
        library.assets[aid]=Asset(aid,str((folder/'vtuber.mp4').resolve()),40,720,720,50,1,False,True)
        clip=Clip(uid(),aid,12,0,2,track=0);clip.scale=.65;clip.x=123;clip.y=234
        clip.effects=[new_effect('chroma_key'),new_effect('color_adjust')]
        library.active.clips=[clip];save_library(library,str(w.library_path(p)))
        take.update(state='ready',duration=.04,files={'vtuber':'vtuber.mov'},replaced_files={'vtuber':'vtuber.mp4'},
            media={'vtuber':{'duration':.04,'width':1080,'height':1080,'alpha':True}})
        write_take(folder,take)
        window=StudioWindow(w,root)
        try:
            window.desk.select(p);editor=window.load_project(p,sync=False)
            self.assertTrue(import_take(editor,w,p,folder,take))
            self.assertEqual(len(editor.sequence.clips),1)
            current=editor.sequence.clips[0]
            self.assertEqual((current.start,current.scale,current.x,current.y),(12,.65,123,234))
            self.assertEqual([e['type'] for e in current.effects],['color_adjust'])
            self.assertTrue(editor.library.assets[aid].path.endswith('vtuber.mov'))
            self.assertEqual(editor.library.assets[aid].width,1080)
        finally:window.close();self.app.processEvents()
