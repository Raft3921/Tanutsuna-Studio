from pathlib import Path
import os
import sys
import tempfile
import unittest
import shutil
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vendor/cutraft-win/app'))
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox
from cutraft.app import configure_app
from cutraft.storage import load_library
from studio.workspace import Workspace, atomic_text


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root / 'settings.json', self.root / 'videos')

    def test_unique_safe_paths_and_real_cutraft_library(self):
        a = self.workspace.create('../CON/たぬつな：実況？')
        b = self.workspace.create('../CON/たぬつな：実況？', allow_duplicate=True)
        self.assertNotEqual(a['folder'], b['folder'])
        self.assertEqual(Path(a['folder']).parent, (self.root / 'videos').resolve())
        library = load_library(str(self.workspace.library_path(a)))
        self.assertEqual(library.active.name, a['title'])
        self.assertEqual((library.active.width, library.active.height), (1920, 1080))
        self.assertEqual(library.active.fps_num, 50)
        self.workspace.save_steps(a, [True, False, True, False])
        reloaded = Workspace(self.workspace.file, self.root)
        self.assertEqual(reloaded.data['projects'][1]['steps'], [True, False, True, False])

    def test_invalid_input_and_corrupted_settings_preserved(self):
        with self.assertRaises(ValueError):
            self.workspace.create('  ')
        atomic_text(self.workspace.file, '{broken')
        with self.assertRaises(ValueError):
            Workspace(self.workspace.file, self.root)
        self.assertEqual(self.workspace.file.read_text(), '{broken')

    def test_only_existing_folders_are_available_and_can_be_recovered(self):
        project=self.workspace.create('外付け動画')
        folder=Path(project['folder']);moved=self.root/'disconnected'
        folder.rename(moved)
        self.assertEqual(self.workspace.available_projects(),[])
        self.assertIn(project,self.workspace.data['projects'])
        replacement=self.workspace.create('外付け動画')
        self.assertEqual(self.workspace.available_projects(),[replacement])
        moved.rename(folder)
        self.assertEqual(len(self.workspace.available_projects()),2)

    def test_delete_moves_entire_folder_and_unregisters_only_on_success(self):
        project=self.workspace.create('削除テスト');self.workspace.remember(project)
        folder=Path(project['folder']);(folder/'02-assets/sample.txt').write_text('sample')
        with self.assertRaises(ValueError):self.workspace.delete(project,lambda p:(False,''))
        self.assertTrue(folder.is_dir());self.assertIn(project,self.workspace.available_projects())
        trash=self.root/'test-trash'
        def move(path):Path(path).rename(trash);return True
        self.workspace.delete(project,move)
        self.assertFalse(folder.exists());self.assertTrue((trash/'02-assets/sample.txt').is_file())
        self.assertEqual(self.workspace.available_projects(),[])
        reloaded=Workspace(self.workspace.file,self.root)
        self.assertNotIn('last_project',reloaded.data)
        self.assertEqual(reloaded.data['projects'],[])

    def test_delete_rejects_replaced_folder_metadata(self):
        project=self.workspace.create('動画')
        (Path(project['folder'])/'project.json').write_text('{"id":"unrelated"}')
        with self.assertRaises(ValueError):self.workspace.delete(project,lambda p:self.fail('must not delete'))


class NativeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory()
        cls.app = QApplication.instance() or QApplication([])
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.home.name)
        configure_app(cls.app)
        # Avoid persistent preferences or tutorial windows in the test.
        QSettings('TanutsunaStudio', 'Editor').setValue('tutorial_seen', True)

    @classmethod
    def tearDownClass(cls):
        cls.home.cleanup()

    def test_desk_opens_managed_project_in_cutraft_and_vtuber_controls(self):
        from studio.main import StudioWindow
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = Workspace(root / 'settings.json', root / 'videos')
            project = workspace.create('初回ゲーム実況')
            window = StudioWindow(workspace, root)
            window.show()
            try:
                window.desk.projects.selectRow(0)
                self.app.processEvents()


                desk = window.desk
                self.assertEqual(desk.current, project)
                desk.open_editor()
                self.assertEqual(window.path, str(workspace.library_path(project)))
                self.assertEqual(window.sequence.name, '初回ゲーム実況')
                self.assertIsNotNone(window.timeline)
                self.assertIsNotNone(window.title_inspector)
                self.assertEqual(self.app.palette().color(self.app.palette().ColorRole.Window).name(), '#f3f5f6')
                self.assertTrue(desk.isVisible())
                self.assertEqual(desk.current_page, 'edit')
                window.open_desk()
                self.assertEqual(desk.current_page,'home')
                desk.navigate('vtuber')
                self.app.processEvents()
                desk.vtuber.sliders['yaw'].setValue(35)
                desk.vtuber.mode.setCurrentIndex(0)
                desk.vtuber.last -= .1
                desk.vtuber.tick()
                self.assertGreater(desk.vtuber.pose.yaw, 0)
                window.show()
                self.app.processEvents()
                (ROOT / 'docs').mkdir(exist_ok=True)
                desk.show()
                self.app.processEvents()
                desk.grab().save(str(ROOT / 'docs/production-desk.png'))
                desk.navigate('vtuber')
                self.app.processEvents()
                desk.vtuber.timer.stop()
                desk.vtuber.viewport.update_pose(desk.vtuber.pose,0)
                desk.grab().save(str(ROOT / 'docs/vtuber-desk.png'))
                desk.open_editor()
                window.grab().save(str(ROOT / 'docs/cutraft-editor.png'))
            finally:
                window.close()
                self.app.processEvents()


    def test_missing_folder_hidden_and_delete_releases_editor(self):
        from studio.main import StudioWindow
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=Workspace(root/'settings.json',root/'videos')
            missing=workspace.create('存在しない動画');shutil.rmtree(missing['folder'])
            project=workspace.create('削除する動画')
            window=StudioWindow(workspace,root);window.show()
            try:
                desk=window.desk
                self.assertEqual(desk.visible_projects,[project])
                desk.select(project);desk.open_editor()
                desk.delete_project()
                self.app.processEvents()
                self.assertIsNone(desk.current);self.assertIsNone(window.loaded_editor)
                self.assertEqual(desk.visible_projects,[])
                self.assertEqual(desk.current_page,'home')
                self.assertFalse(Path(project['folder']).exists())
                entry=next(e for e in window.data_trash.entries() if e['kind']=='project')
                window.restore_data(entry['ticket'])
                self.assertTrue(Path(project['folder']).is_dir())
                self.assertIn(project['id'],[p['id'] for p in workspace.available_projects()])
            finally:window.close();self.app.processEvents()

if __name__ == '__main__':
    unittest.main()
