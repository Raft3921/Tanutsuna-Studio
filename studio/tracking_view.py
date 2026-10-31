"""Invisible in-app web surface for camera permissions and face tracking."""
from PySide6.QtCore import QEvent, Qt, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePermission
from PySide6.QtWebEngineWidgets import QWebEngineView


class TrackingView(QWebEngineView):
    statusChanged = Signal(bool, str)

    def __init__(self, parent):
        # Keep the tracker as a child of Studio's main window. It stays alive
        # while the user switches pages and never creates another OS window.
        super().__init__(parent)
        self.setFixedSize(2, 2)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.page().permissionRequested.connect(self.permission_requested)
        self.loadFinished.connect(self.loaded)
        self.setStyleSheet('background: transparent; border: none;')
        parent.installEventFilter(self)
        self.move(max(0, parent.width()-2), max(0, parent.height()-2))

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.move(max(0, watched.width()-2), max(0, watched.height()-2))
        return super().eventFilter(watched, event)

    def start(self, url):
        self.setUrl(QUrl(url))
        self.show()

    def stop(self):
        self.setUrl(QUrl('about:blank'))

    def permission_requested(self, permission):
        allowed = {
            QWebEnginePermission.PermissionType.MediaAudioCapture,
            QWebEnginePermission.PermissionType.MediaVideoCapture,
            QWebEnginePermission.PermissionType.MediaAudioVideoCapture,
        }
        if permission.origin().host() == '127.0.0.1' and permission.permissionType() in allowed:
            permission.grant()
        else:
            permission.deny()

    def loaded(self, ok):
        self.statusChanged.emit(bool(ok), '追跡画面を準備できません。アプリ内のカメラ機能を確認してください。' if not ok else '')
