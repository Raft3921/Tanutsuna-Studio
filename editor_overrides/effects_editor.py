"""Clip effect stack, with an actual processed preview and transactional Apply."""
import copy
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                              QHBoxLayout, QLabel, QListWidget, QPushButton, QScrollArea,
                              QSplitter, QVBoxLayout, QWidget)
from .video_effects import EFFECTS, apply_effects, new_effect, validate_effects
from .widgets import FocusDoubleSpinBox
from .async_images import AsyncImages
import json


class EffectsEditor(QDialog):
    def __init__(self, effects, image, seconds=0, parent=None):
        super().__init__(parent)
        self.effects = copy.deepcopy(effects)
        self.image, self.seconds = image, seconds
        self.preview_source = image.scaled(480, 270, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.loading = False
        self.setWindowTitle('映像エフェクト')
        self.resize(1040, 660)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('上から順番に適用します。同じエフェクトも複数追加できます。'))
        splitter = QSplitter()
        layout.addWidget(splitter, 1)
        left = QWidget()
        ll = QVBoxLayout(left)
        self.category = QComboBox()
        self.category.addItems(['すべて', *dict.fromkeys(row[1] for row in EFFECTS.values())])
        ll.addWidget(self.category)
        self.available = QComboBox()
        ll.addWidget(self.available)
        self.category.currentTextChanged.connect(self.filter)
        self.filter('すべて')
        add = QPushButton('追加')
        add.clicked.connect(self.add)
        ll.addWidget(add)
        self.stack = QListWidget()
        self.stack.currentRowChanged.connect(self.select)
        self.stack.itemChanged.connect(self.toggle)
        ll.addWidget(self.stack, 1)
        row = QHBoxLayout()
        for name, operation in [('上へ', lambda: self.move(-1)), ('下へ', lambda: self.move(1)), ('削除', self.remove)]:
            button = QPushButton(name)
            button.clicked.connect(operation)
            row.addWidget(button)
        ll.addLayout(row)
        splitter.addWidget(left)
        center = QWidget()
        self.form = QFormLayout(center)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(center)
        splitter.addWidget(scroll)
        preview_panel = QWidget()
        pl = QVBoxLayout(preview_panel)
        self.preview = QLabel()
        self.preview.setMinimumSize(320, 180)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pl.addWidget(self.preview, 1)
        self.compare = QCheckBox('元の映像と比較')
        self.compare.toggled.connect(self.paint_preview)
        pl.addWidget(self.compare)
        self.error = QLabel()
        self.error.setWordWrap(True)
        pl.addWidget(self.error)
        splitter.addWidget(preview_panel)
        splitter.setSizes([240, 260, 500])
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('適用')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.paint_preview)
        self.worker = AsyncImages(self)
        self.worker.changed.connect(self.paint_preview)
        self.worker.failed.connect(self.error.setText)
        self.finished.connect(lambda _: self.worker.shutdown())
        self.refresh()
        self.paint_preview()

    def filter(self, category):
        self.available.clear()
        for key, (name, group, _) in EFFECTS.items():
            if category in ('すべて', group):
                self.available.addItem(name, key)

    def refresh(self, selected=0):
        self.loading = True
        self.stack.clear()
        for effect in self.effects:
            self.stack.addItem(EFFECTS[effect['type']][0])
            item = self.stack.item(self.stack.count()-1)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if effect.get('enabled', True) else Qt.CheckState.Unchecked)
        self.loading = False
        if self.effects:
            self.stack.setCurrentRow(max(0, min(selected, len(self.effects)-1)))
        else:
            self.select(-1)
        self.timer.start()

    def add(self):
        if len(self.effects) >= 64:
            self.error.setText('エフェクトは64個まで追加できます。')
            return
        self.effects.append(new_effect(self.available.currentData()))
        self.refresh(len(self.effects)-1)

    def remove(self):
        index = self.stack.currentRow()
        if index >= 0:
            self.effects.pop(index)
            self.refresh(index)

    def move(self, direction):
        index = self.stack.currentRow()
        target = index+direction
        if index >= 0 and 0 <= target < len(self.effects):
            self.effects[index], self.effects[target] = self.effects[target], self.effects[index]
            self.refresh(target)

    def toggle(self, item):
        if not self.loading:
            self.effects[self.stack.row(item)]['enabled'] = item.checkState() == Qt.CheckState.Checked
            self.timer.start()

    def select(self, index):
        if self.loading:
            return
        while self.form.rowCount():
            self.form.removeRow(0)
        if index < 0 or index >= len(self.effects):
            self.form.addRow(QLabel('エフェクトを追加してください。'))
            return
        effect = self.effects[index]
        for key, (name, low, high, default) in EFFECTS[effect['type']][2].items():
            field = FocusDoubleSpinBox()
            field.setRange(low, high)
            field.setDecimals(3)
            field.setSingleStep((high-low)/100)
            field.setValue(effect.get('params', {}).get(key, default))
            field.valueChanged.connect(lambda value, k=key, e=effect: self.change(e, k, value))
            self.form.addRow(name, field)

    def change(self, effect, key, value):
        effect.setdefault('params', {})[key] = value
        self.timer.start()

    def paint_preview(self):
        try:
            source = self.preview_source
            if any(e.get('enabled', True) and e['type'] in ('zoom_blur', 'radial_blur', 'direction_blur') for e in self.effects):
                source = source.scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            if self.compare.isChecked():
                result = source
            else:
                effects, seconds = copy.deepcopy(self.effects), self.seconds
                key = (self.preview_source.cacheKey(), source.width(), source.height(), json.dumps(effects, sort_keys=True), seconds)
                result = self.worker.request('effects', key,
                    lambda: apply_effects(source, effects, seconds, preview=True))
                if result is None:
                    result = source
            self.preview.setPixmap(QPixmap.fromImage(result).scaled(480, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.error.clear()
        except Exception as exc:
            self.error.setText(str(exc))

    def accept(self):
        try:
            validate_effects(self.effects)
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        super().accept()
