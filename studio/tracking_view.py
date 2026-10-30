"""In-app camera and microphone capture for local face tracking."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineCore import QWebEnginePermission
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout


class TrackingDialog(QDialog):
    def __init__(self, parent, url):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle('V体のカメラ追跡 — たぬつな Studio')
        self.resize(600, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.status = QLabel('カメラを準備しています。初回のみOSの許可ダイアログを確認してください。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.view = QWebEngineView(self)
        self.view.page().permissionRequested.connect(self.permission_requested)
        self.view.loadFinished.connect(self.loaded)
        layout.addWidget(self.view, 1)
        close = QPushButton('追跡を停止して閉じる')
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.view.setUrl(QUrl(url))

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
        if not ok:
            self.status.setText('アプリ内のカメラ画面を読み込めませんでした。追跡モデルの配置を確認してください。')
        else:
            self.status.setText('カメラ映像はこのPC内で処理されます。顔画像・音声を保存したり送信したりしません。')
