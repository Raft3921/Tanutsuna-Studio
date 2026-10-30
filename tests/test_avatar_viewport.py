"""Regressions for native surface re-entry and a silent blank framebuffer."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import Signal
from PySide6.QtGui import QShowEvent,QImage,QColor
from PySide6.QtWidgets import QApplication,QWidget
from studio.avatar_view import AvatarViewport,SoftwareViewport


class ViewportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_reentrant_show_constructs_only_one_renderer(self):
        made=[]
        class ReentrantGPU(QWidget):
            renderFailed=Signal(str)
            def __init__(self,model,parent):
                super().__init__(parent);made.append(self)
                if len(made)>2:raise AssertionError('Renderer construction recursed')
                parent.showEvent(QShowEvent())
            def update_pose(self,*args):pass
            def release(self):pass
        view=AvatarViewport(software=True)
        old=view.backend;view.layout_.removeWidget(old);old.deleteLater();view.backend=None
        try:
            with patch('studio.avatar_view.GPUViewport',ReentrantGPU):view.set_software(False)
            self.assertEqual(len(made),1)
            self.assertEqual(view.layout_.count(),1)
            self.assertIs(view.backend,made[0])
        finally:view.release();view.close()

    def test_blank_gpu_framebuffer_automatically_falls_back(self):
        class BlankGPU(QWidget):
            renderFailed=Signal(str)
            ready=True;frame_count=1;error=''
            def __init__(self,model,parent):super().__init__(parent)
            def update_pose(self,*args):pass
            def release(self):pass
            def isValid(self):return True
            def grabFramebuffer(self):
                image=QImage(64,64,QImage.Format.Format_RGBA8888);image.fill(QColor('#4b535c'));return image
        view=AvatarViewport(software=True)
        try:
            with patch('studio.avatar_view.GPUViewport',BlankGPU):view.set_software(False)
            view.show()
            view._verify_backend(view.backend,0)
            self.assertTrue(view.software)
            self.assertIsInstance(view.backend,SoftwareViewport)
            self.assertEqual(view.layout_.count(),1)
            self.assertTrue(view.last_render_error)
        finally:view.release();view.close()


if __name__=='__main__':unittest.main()
