"""Lightweight scene selection window for a live broadcast."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QGridLayout


class LiveScenesDialog(QDialog):
    sceneRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('配信シーンの切り替え')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        note = QLabel('シーンを選ぶと配信映像がワイプで切り替わります。')
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        for row, (label, scene, tip) in enumerate((
            ('オープニング演出', 'intro', 'V体のローディング演出からゲーム画面へ切り替えます'),
            ('ゲーム画面', 'game', 'ゲーム映像とV体を表示し、マイクをオンにします'),
            ('離席中', 'break', 'スタジオ背景とV体を表示し、マイクをミュートします'),
        )):
            button = QPushButton(label)
            button.setMinimumHeight(44)
            button.setToolTip(tip)
            button.clicked.connect(lambda checked=False, scene=scene: self.sceneRequested.emit(scene))
            grid.addWidget(button, row, 0)
        layout.addLayout(grid)
