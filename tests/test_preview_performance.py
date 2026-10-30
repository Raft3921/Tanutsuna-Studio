"""Regression checks: slow image processing must leave the Qt event loop free."""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, PropertyMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vendor/cutraft-win/app'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication
from cutraft.async_images import AsyncImages
from cutraft.effects_editor import EffectsEditor
from cutraft.preview import Preview
from cutraft.video_effects import new_effect


class PreviewPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pump_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate())

    def test_slow_worker_coalesces_requests_and_keeps_ui_responsive(self):
        worker = AsyncImages()
        release, started = threading.Event(), threading.Event()
        calls, heartbeat = [], []
        def slow():
            started.set()
            release.wait(3)
            calls.append(0)
            return 'first'
        try:
            worker.request('layer', 0, slow)
            self.assertTrue(started.wait(1))
            for index in range(1, 101):
                worker.request('layer', index, lambda n=index: calls.append(n) or n)
            self.assertEqual(len(worker.pending), 1)
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            self.pump_until(lambda: bool(heartbeat))
            self.assertEqual(calls, [])
            release.set()
            self.pump_until(lambda: 100 in worker.cache)
            self.assertEqual(calls, [0, 100])
            self.assertEqual(worker.cache[100], 100)
        finally:
            release.set()
            worker.shutdown()

    def test_reset_discards_result_from_previous_project(self):
        worker = AsyncImages()
        release = threading.Event()
        try:
            worker.request('layer', 'old', lambda: release.wait(3) or 'old')
            worker.clear()
            worker.request('layer', 'new', lambda: 'new-image')
            release.set()
            self.pump_until(lambda: 'new' in worker.cache)
            self.assertNotIn('old', worker.cache)
            self.assertEqual(worker.latest['layer'], 'new-image')
        finally:
            release.set()
            worker.shutdown()

    def test_effect_dialog_does_not_render_on_gui_thread_or_repeat_completed_job(self):
        image = QImage(640, 360, QImage.Format.Format_ARGB32)
        image.fill(QColor('red'))
        calls = []
        gui_thread = threading.get_ident()
        def render(source, effects, seconds, preview):
            calls.append(threading.get_ident())
            return source
        with patch('cutraft.effects_editor.apply_effects', side_effect=render):
            dialog = EffectsEditor([new_effect('zoom_blur')], image)
            try:
                self.pump_until(lambda: bool(dialog.worker.cache))
                dialog.timer.stop()
                self.app.processEvents()
                for _ in range(5):
                    dialog.paint_preview()
                self.assertEqual(len(calls), 1)
                self.assertNotEqual(calls[0], gui_thread)
            finally:
                dialog.reject()

    def test_idle_channel_clear_and_repeated_frame_do_not_restart_decoders(self):
        preview = Preview()
        try:
            with patch.object(preview.source_channel.player, 'setSource') as source:
                preview.source_channel.clear()
                preview.source_channel.clear()
                source.assert_not_called()
            preview.playing = True
            preview.waiting = False
            preview.clock.start()
            with patch.object(Preview, 'end', new_callable=PropertyMock, return_value=100), patch.object(preview, 'sync') as sync:
                preview.tick()
                sync.assert_not_called()
        finally:
            preview.shutdown()

if __name__ == '__main__':
    unittest.main()
