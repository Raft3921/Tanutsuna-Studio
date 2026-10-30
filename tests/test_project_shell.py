import os
import sys
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor/cutraft-win/app'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings,QEvent,Qt
from PySide6.QtGui import QKeyEvent,QImage,QColor
from studio.main import StudioWindow
from studio.workspace import Workspace,DuplicateProject
from studio.media_store import collect_media
from cutraft.storage import save_library,load_library
from cutraft.model import Asset,Clip,uid
from cutraft.session import SessionStore


class ProjectShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.settings=tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,cls.settings.name)

    @classmethod
    def tearDownClass(cls):cls.settings.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=Workspace(self.root/'settings.json',self.root/'videos')

    def open_window(self):
        window=StudioWindow(self.workspace,self.root);window.show();self.app.processEvents()
        self.addCleanup(lambda:(window.close(),self.app.processEvents()))
        return window

    def test_home_is_only_initial_surface_and_navigation_keeps_one_window(self):
        project=self.workspace.create('新しい動画')
        window=self.open_window()
        self.assertIsNone(window.loaded_editor)
        self.assertIsNone(window.desk._vtuber)
        self.assertIsNone(window.desk._recording_panel)
        window.desk.select(project)
        self.assertTrue(window.desk.open_editor());self.app.processEvents()
        surface=window.loaded_editor
        self.assertTrue(window.desk.isVisible())
        self.assertEqual(window.desk.current_page,'edit')
        self.assertIs(surface.window(),window)
        self.assertFalse(surface.library_tabs.isTabVisible(1))
        self.assertFalse(surface.library_tabs.tabBar().isVisible())
        window.open_desk();self.app.processEvents()
        self.assertEqual(window.desk.current_page,'home')
        self.assertFalse(surface.isVisible());self.assertFalse(surface.preview.timer.isActive())
        window.desk.open_editor();self.app.processEvents()
        self.assertIs(window.loaded_editor,surface)
        self.assertEqual(window.path,str(self.workspace.library_path(project)))

    def test_duplicate_name_and_registering_same_folder_do_not_add_projects(self):
        project=self.workspace.create('動画A')
        with self.assertRaises(DuplicateProject):self.workspace.create('  動画a  ')
        self.assertIs(self.workspace.attach(project['folder']),project)
        self.assertEqual(len(self.workspace.data['projects']),1)

    def test_owned_content_dedup_and_portable_relative_paths(self):
        project=self.workspace.create('素材を管理')
        a=self.root/'image.png';b=self.root/'same.png'
        image=QImage(80,50,QImage.Format.Format_RGB32);image.fill(QColor('red'));image.save(str(a));b.write_bytes(a.read_bytes())
        copied=collect_media(project,[str(a),str(b)],threading.Event())
        self.assertEqual(len(copied),1)
        self.assertTrue(Path(copied[0]).is_relative_to(project['folder']))
        self.assertEqual(collect_media(project,[str(a)],threading.Event()),copied)
        path=self.workspace.library_path(project);library=load_library(str(path))
        asset=Asset(uid(),copied[0],1000,80,50,has_audio=False,kind='image');library.assets[asset.id]=asset
        library.active.clips=[Clip(uid(),asset.id,0,0,50)]
        save_library(library,str(path))
        with sqlite3.connect(path) as db:stored=db.execute('SELECT path FROM assets').fetchone()[0]
        self.assertFalse(Path(stored).is_absolute());self.assertTrue(stored.startswith('../02-assets/'))
        self.assertEqual(load_library(str(path)).assets[asset.id].path,copied[0])
        import shutil
        moved=self.root/'moved-video';shutil.copytree(project['folder'],moved)
        reopened=self.workspace.attach(moved)
        self.assertEqual(len(self.workspace.data['projects']),1)
        media=load_library(str(self.workspace.library_path(reopened)))
        self.assertTrue(Path(media.assets[asset.id].path).is_relative_to(moved.resolve()))

    def test_editor_shortcuts_work_inside_embedded_editor_only(self):
        project=self.workspace.create('ショートカット')
        window=self.open_window();window.desk.select(project);window.show_editor();self.app.processEvents()
        surface=window.loaded_editor
        from unittest.mock import patch
        event=QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Space,Qt.KeyboardModifier.NoModifier)
        with patch.object(surface,'handle_shortcut',return_value=True) as handler:
            self.assertTrue(surface.eventFilter(surface.timeline,event));handler.assert_called_once()
        window.open_desk();self.app.processEvents()
        with patch.object(surface,'handle_shortcut',return_value=True) as handler:
            surface.eventFilter(surface.timeline,event);handler.assert_not_called()

    def test_switch_saves_to_same_project_and_restores_own_recovery(self):
        a=self.workspace.create('動画A');b=self.workspace.create('動画B')
        window=self.open_window();window.desk.select(a);window.show_editor();self.app.processEvents()
        surface=window.loaded_editor
        surface.commit('変更',lambda:setattr(surface.sequence,'name','Aの編集'))
        self.assertTrue(window.desk.select(b));window.show_editor()
        self.assertEqual(load_library(str(self.workspace.library_path(a))).active.name,'Aの編集')
        self.assertEqual(window.path,str(self.workspace.library_path(b)))
        snapshot=load_library(str(self.workspace.library_path(a)));snapshot.active.name='保存途中のA'
        SessionStore(self.workspace.library_path(a).parent/'recovery').write(snapshot,{'path':str(self.workspace.library_path(a)),'dirty':True,'frame':0})
        window.desk.select(a);window.show_editor()
        self.assertEqual(window.sequence.name,'保存途中のA')
        self.assertEqual(window.path,str(self.workspace.library_path(a)))

if __name__=='__main__':unittest.main()
