"""Game-styled scene picker for a live broadcast."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFrame


class LiveScenesDialog(QDialog):
    sceneRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('配信シーン')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(430)
        self.setStyleSheet('''
            QDialog { background:#081722; color:#e8f8fb; }
            QLabel#Eyebrow { color:#5be0c2; font-size:11px; font-weight:800; letter-spacing:2px; }
            QLabel#Title { color:#f0fbff; font-size:22px; font-weight:800; }
            QLabel#SceneHint { color:#9db8c1; font-size:12px; }
            QPushButton#SceneCard { text-align:left; color:#eaf8fa; background:#102631; border:1px solid #254754; border-radius:14px; padding:12px 16px; font-size:15px; font-weight:750; }
            QPushButton#SceneCard:hover { background:#173945; border-color:#55d9bd; }
            QPushButton#SceneCard:pressed { background:#1c4a50; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 22)
        layout.setSpacing(12)
        eyebrow = QLabel('LIVE CONTROL   /   SCENE SELECT')
        eyebrow.setObjectName('Eyebrow')
        layout.addWidget(eyebrow)
        title = QLabel('シーンを選択')
        title.setObjectName('Title')
        layout.addWidget(title)
        note = QLabel('切り替え中はマイクを自動でミュートします。')
        note.setObjectName('SceneHint')
        layout.addWidget(note)
        for title, scene, desc, key in (
            ('01   オープニング', 'intro', 'ゲーム起動風のロード画面から配信を開始', 'OPENING'),
            ('02   ゲーム配信', 'game', 'ゲーム映像とV体を表示してマイクをオン', 'LIVE'),
            ('03   離席中', 'break', '離席カードとV体を表示してマイクをミュート', 'BRB'),
        ):
            card = QPushButton(f'{title}\n{desc}     ·     {key}')
            card.setObjectName('SceneCard')
            card.setMinimumHeight(72)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.clicked.connect(lambda checked=False, scene=scene: self.sceneRequested.emit(scene))
            layout.addWidget(card)
