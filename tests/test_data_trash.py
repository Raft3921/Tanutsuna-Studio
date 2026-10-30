import os
import sys
import tempfile
import unittest
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor/cutraft-win/app'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from studio.workspace import Workspace
from studio.trash import DataTrash
from studio.takes import create_take,write_take,load_take,takes_for
from cutraft.model import Asset,Clip,uid
from cutraft.storage import load_library,save_library


class DataTrashTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.workspace=Workspace(self.root/'settings.json',self.root/'videos')
        self.project=self.workspace.create('ゴミ箱の確認')
        self.trash=DataTrash(self.workspace,self.root/'app-data')
        self.path=self.workspace.library_path(self.project)

    def add_asset(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'test media')
        library=load_library(str(self.path));asset=Asset(uid(),str(path),2000,80,50,has_audio=False,kind='image')
        library.assets[asset.id]=asset;clip=Clip(uid(),asset.id,0,0,50)
        library.active.clips.append(clip);save_library(library,str(self.path))
        return asset,clip

    def test_delete_owned_file_and_restore_without_overwriting_later_edit(self):
        file=Path(self.project['folder'])/'02-assets/a.png';asset,clip=self.add_asset(file)
        ticket=self.trash.delete_data(self.project,paths=[file],label='画像A')
        self.assertFalse(file.exists());self.assertNotIn(asset.id,load_library(str(self.path)).assets)
        current=load_library(str(self.path));other=Asset(uid(),'',1000,has_audio=False,kind='image')
        current.assets[other.id]=other;current.active.clips.append(Clip(uid(),other.id,0,0,50));save_library(current,str(self.path))
        self.trash.restore(ticket)
        self.assertEqual(file.read_bytes(),b'test media')
        restored=load_library(str(self.path));self.assertIn(other.id,restored.assets)
        self.assertEqual(len(restored.active.clips),2);restored.validate()
        self.assertNotEqual(next(c for c in restored.active.clips if c.id==clip.id).track,0)
        self.assertEqual(self.trash.entries(),[])

    def test_external_source_is_not_deleted(self):
        file=self.root/'external.png';asset,_=self.add_asset(file)
        ticket=self.trash.delete_data(self.project,asset_ids=[asset.id])
        self.assertTrue(file.exists());self.assertNotIn(asset.id,load_library(str(self.path)).assets)
        self.trash.restore(ticket);self.assertIn(asset.id,load_library(str(self.path)).assets)

    def test_take_deletion_and_restore_include_all_raw_materials(self):
        folder,take=create_take(self.project,{'desktop':True,'voice':True,'avatar':True})
        asset,_=self.add_asset(folder/'screen.mp4')
        (folder/'voice-source.wav').write_bytes(b'raw audio')
        take.update(state='ready',duration=1,files={'screen':'screen.mp4'});write_take(folder,take)
        ticket=self.trash.delete_data(self.project,take_folders=[folder],label='収録')
        self.assertFalse(folder.exists());self.assertEqual(takes_for(self.project),[])
        self.assertNotIn(asset.id,load_library(str(self.path)).assets)
        self.trash.restore(ticket)
        self.assertEqual((folder/'voice-source.wav').read_bytes(),b'raw audio')
        self.assertEqual(len(takes_for(self.project)),1)

    def test_single_take_role_does_not_reappear_on_import_and_can_be_restored(self):
        folder,take=create_take(self.project,{'desktop':True,'voice':True,'avatar':True})
        asset,_=self.add_asset(folder/'voice.mp3')
        take.update(state='ready',duration=1,files={'voice':'voice.mp3'});write_take(folder,take)
        ticket=self.trash.delete_data(self.project,paths=[folder/'voice.mp3'])
        deleted=load_take(folder);self.assertNotIn('voice',deleted['files']);self.assertFalse(deleted['options']['voice'])
        self.assertIn('voice',deleted['deleted_roles'])
        self.trash.restore(ticket)
        restored=load_take(folder);self.assertEqual(restored['files']['voice'],'voice.mp3');self.assertTrue(restored['options']['voice'])

    def test_project_restore_keeps_nested_trash_recoverable(self):
        asset,_=self.add_asset(Path(self.project['folder'])/'02-assets/a.png')
        self.trash.delete_data(self.project,asset_ids=[asset.id],label='先に削除した素材')
        project_ticket=self.trash.delete_project(self.project)
        self.assertEqual(self.workspace.available_projects(),[])
        self.assertEqual(len(self.trash.entries()),2)
        media=next(e for e in self.trash.entries() if e['kind']=='media')
        with self.assertRaises(ValueError):self.trash.restore(media['ticket'])
        self.trash.restore(project_ticket)
        self.assertEqual(len(self.workspace.available_projects()),1)
        media=next(e for e in self.trash.entries() if e['kind']=='media')
        self.trash.restore(media['ticket'])
        self.assertTrue((Path(self.project['folder'])/'02-assets/a.png').is_file())

    def test_last_edit_version_can_be_deleted_and_restored_without_extra_blank_version(self):
        library=load_library(str(self.path));sid=library.active.id
        ticket=self.trash.delete_data(self.project,sequence_id=sid,label='編集版')
        current=load_library(str(self.path));self.assertEqual(len(current.sequences),1);self.assertNotEqual(current.active.id,sid)
        self.trash.restore(ticket)
        restored=load_library(str(self.path));self.assertEqual(len(restored.sequences),1);self.assertEqual(restored.active.id,sid)

    def test_restore_never_overwrites_a_new_file_at_original_path(self):
        file=Path(self.project['folder'])/'02-assets/a.png';self.add_asset(file)
        ticket=self.trash.delete_data(self.project,paths=[file])
        file.write_bytes(b'new contents')
        with self.assertRaises(ValueError):self.trash.restore(ticket)
        self.assertEqual(file.read_bytes(),b'new contents');self.assertEqual(len(self.trash.entries()),1)

    def test_shell_delete_and_restore_do_not_resurrect_deleted_take(self):
        from studio.main import StudioWindow
        app=QApplication.instance() or QApplication([])
        folder,take=create_take(self.project,{'desktop':False,'voice':False,'avatar':False})
        self.add_asset(folder/'screen.mp4');take.update(state='ready',files={'screen':'screen.mp4'},duration=1);write_take(folder,take)
        window=StudioWindow(self.workspace,self.root/'app-data');window.show();app.processEvents()
        try:
            window.desk.select(self.project);window.show_editor()
            ticket=window.delete_data(take_folders=[folder],label='収録')
            window.open_desk();window.show_editor()
            self.assertEqual(len(window.sequence.clips),0);self.assertFalse(folder.exists())
            window.restore_data(ticket);self.assertTrue(folder.exists());self.assertEqual(len(window.sequence.clips),1)
        finally:window.close();app.processEvents()

if __name__=='__main__':unittest.main()
