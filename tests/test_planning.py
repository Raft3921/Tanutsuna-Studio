import os, sys, tempfile, unittest, json
from pathlib import Path
from unittest.mock import patch, Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/cutraft-win/app')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from studio.workspace import Workspace
from studio.main import StudioWindow
from studio.planning import load_plan


class PlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.workspace=Workspace(root/'settings.json',root/'videos')
        self.a=self.workspace.create('企画あ');self.b=self.workspace.create('企画い')
        self.window=StudioWindow(self.workspace,root)
        self.addCleanup(lambda:(self.window.close(),self.app.processEvents()))
        self.desk=self.window.desk;self.desk.select(self.a);self.desk.navigate('plan')
        self.panel=self.desk._planning

    def test_switch_and_reopen_preserves_rich_notes_drawings_and_schedule(self):
        p=self.panel;p.notes.setHtml('<p><b>太字の日本語</b>とアイデア</p>')
        p.board.strokes=[{'color':'#d93025','width':4,'points':[[.1,.2],[.8,.7]]}]
        p.game.setText('Minecraft');p.scheduled.setChecked(True)
        self.desk.select(self.b)
        self.assertEqual(p.notes.toPlainText(),'');self.assertEqual(p.board.strokes,[])
        self.desk.select(self.a)
        self.assertIn('太字の日本語',p.notes.toPlainText())
        self.assertEqual(p.board.strokes[0]['color'],'#d93025')
        self.assertTrue(p.scheduled.isChecked())
        self.assertIn('700',load_plan(self.a)['notes'])
        self.assertFalse((Path(self.b['folder'])/'00-plan/plan.json').exists())

    def test_overview_shows_saved_schedule_stage_and_draft_search(self):
        p=self.panel;p.title.setText('検索する仮タイトル');p.summary.setPlainText('本格的な概要')
        p.scheduled.setChecked(True);p.schedule.setDateTime(p.schedule.dateTime().addDays(1))
        self.desk.navigate('library')
        self.desk.search.setText('検索する仮タイトル')
        self.assertEqual(len(self.desk.visible_projects),1)
        self.assertIs(self.desk.visible_projects[0],self.a)
        self.assertEqual(self.desk.projects.item(0,2).text(),'概要あり')
        self.assertEqual(self.desk.continue_button.text(),'録画へ進む')
        self.assertNotEqual(self.desk.projects.item(0,1).text(),'未定')
        from studio.planning import summary_for
        self.assertEqual(summary_for(self.a)['title'],'検索する仮タイトル')

    def test_failed_save_blocks_project_switch_and_keeps_draft(self):
        self.panel.notes.setPlainText('消えてはいけないメモ')
        with patch('studio.planning.atomic_text',side_effect=OSError('disk full')):
            self.assertFalse(self.desk.select(self.b))
        self.assertIs(self.desk.current,self.a);self.assertTrue(self.panel.dirty)
        self.panel.flush();self.assertIn('消えてはいけない',load_plan(self.a)['notes'])

    def test_overview_creates_matching_video_name_before_recording(self):
        p=self.panel;p.title.setText('本番の企画名');p.summary.setPlainText('雪山で建築する実況')
        p.advance();self.assertEqual(p.tabs.currentIndex(),1)
        with patch.object(self.desk,'open_recording') as record:
            p.advance();record.assert_called_once()
        from cutraft.storage import load_library
        self.assertEqual(self.a['title'],'本番の企画名')
        self.assertEqual(load_library(str(self.workspace.library_path(self.a))).active.name,'本番の企画名')

    def test_corrupt_plan_is_preserved(self):
        self.desk.navigate('home');self.desk.select(self.b)
        file=Path(self.b['folder'])/'00-plan/plan.json';file.parent.mkdir();file.write_text('{broken')
        with self.assertRaises(ValueError):self.desk.navigate('plan')
        self.assertEqual(file.read_text(),'{broken')

    def test_recording_waits_for_camera_and_cancelling_releases_it(self):
        self.desk.navigate('record');p=self.desk.recording_panel
        fake=Mock();fake.recording=None
        fake.server.state.snapshot.return_value=(None,False,'未接続',0)
        self.desk._vtuber=fake
        with patch.object(p.recorder,'start') as start:
            p.begin();self.assertTrue(p.awaiting_camera);start.assert_not_called()
            fake.start_tracking.assert_called_once_with(automatic=True)
            p.stop_or_cancel();self.assertFalse(p.busy);fake.stop_tracking.assert_called_once()
            p.begin();fake.server.state.snapshot.return_value=(None,True,'カメラ追跡中',1)
            p.check_camera();self.assertFalse(p.awaiting_camera)
            start.assert_called_once();self.assertFalse(start.call_args.args[1]['transcribe'])

    def test_camera_failure_does_not_start_a_silent_stationary_take(self):
        self.desk.navigate('record');p=self.desk.recording_panel
        fake=Mock();fake.recording=None
        fake.server.state.snapshot.return_value=(None,False,'未接続',0)
        self.desk._vtuber=fake
        with patch.object(p.recorder,'start') as start:
            p.begin();p.camera_deadline=0;p.check_camera()
            start.assert_not_called();self.assertFalse(p.busy)
            self.assertIn('収録は開始していません',p.status.text())


if __name__=='__main__':unittest.main()
