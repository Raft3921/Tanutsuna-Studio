"""Simple review-first local transcription and subtitle placement workflow."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QHeaderView, QAbstractItemView)

from .audio_tools import audio_role, role_paths
from .jobs import Jobs
from .subtitle_library import add_entries, save_generated
from .transcription import MODELS, split_timed_cues, transcribe_clip


def note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    return label


class TranscriptionDialog(QDialog):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.project = editor.shell.desk.selected_project()
        self.revision = None
        self.cues = []
        self.setWindowTitle('自動文字起こし・仮テロップ')
        self.resize(900, 680)
        layout = QVBoxLayout(self)
        layout.addWidget(note('タイムライン上の音声を選び、字幕を自動生成します。AIモデルはこのPCに保存し、音声も外部へ送らずPC内で処理します。初回だけモデルをダウンロードします。'))
        form = QFormLayout()
        self.audio = QComboBox()
        self.clips = {}
        paths = role_paths(self.project)
        choices = []
        for clip in editor.sequence.ordered():
            asset = editor.library.assets[clip.asset_id]
            if asset.kind != 'media' or not asset.has_audio:
                continue
            role = audio_role(asset, paths)
            label = f'{asset.name} ・ {clip.start / float(editor.sequence.rate):.1f}秒〜 ・ {"マイク" if role == "voice" else "音声"}'
            choices.append((label, clip, role))
        choices.sort(key=lambda item: (item[2] != 'voice', item[1].start, item[0].casefold()))
        for label, clip, _role in choices:
            self.audio.addItem(label, clip.id)
            self.clips[clip.id] = clip
        selected = editor.selected_clip()
        if selected and self.audio.findData(selected.id) >= 0:
            self.audio.setCurrentIndex(self.audio.findData(selected.id))
        if not choices:
            self.audio.addItem('音声のある素材がありません')
        form.addRow('文字起こしする音声', self.audio)
        self.model = QComboBox()
        for key, definition in MODELS.items():
            self.model.addItem(definition['label'], key)
        form.addRow('AIモデル', self.model)
        self.language = QComboBox()
        for label, code in [('日本語', 'ja'), ('自動判定', 'auto'), ('英語', 'en')]:
            self.language.addItem(label, code)
        form.addRow('話し言葉', self.language)
        self.max_chars = QSpinBox(); self.max_chars.setRange(4, 80); self.max_chars.setValue(20); self.max_chars.setSuffix(' 文字')
        form.addRow('1枚の最大文字数', self.max_chars)
        self.max_words = QSpinBox(); self.max_words.setRange(0, 40); self.max_words.setValue(8); self.max_words.setSpecialValueText('制限なし')
        form.addRow('最大単語数（空白区切りの言語）', self.max_words)
        self.line_break = QCheckBox('長い文に自動で改行を入れる'); self.line_break.setChecked(True)
        form.addRow('改行', self.line_break)
        self.line_chars = QSpinBox(); self.line_chars.setRange(4, 40); self.line_chars.setValue(12); self.line_chars.setSuffix(' 文字／行')
        form.addRow('1行の文字数', self.line_chars)
        layout.addLayout(form)
        layout.addWidget(note('文字数で区切った場合の各字幕の時間は、AIが検出した発話区間を文字量に応じて分けます。内容とタイミングを確認してから追加してください。'))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['開始（秒）', '終了（秒）', '仮テロップ（編集できます）'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.select_all = QPushButton('全て選択'); self.select_all.clicked.connect(self.table.selectAll)
        self.clear_selection = QPushButton('選択解除'); self.clear_selection.clicked.connect(self.table.clearSelection)
        self.start_button = QPushButton('文字起こしを開始'); self.start_button.setObjectName('Primary'); self.start_button.clicked.connect(self.start_transcription)
        self.add_button = QPushButton('選択した仮テロップを追加'); self.add_button.clicked.connect(self.add_selected)
        self.add_button.setEnabled(False)
        for button in (self.select_all, self.clear_selection, self.start_button, self.add_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.status = note('字幕を追加すると、字幕ライブラリにもSRT形式で保存します。追加後はタイムライン上で全字幕が選択され、デザインをまとめて変更できます。')
        layout.addWidget(self.status)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        layout.addWidget(close)
        self.jobs = Jobs(self)
        self.jobs.finished.connect(self.finished)
        self.jobs.progress.connect(self.status.setText)

    def start_transcription(self):
        clip_id = self.audio.currentData()
        if not clip_id or clip_id not in self.clips:
            self.status.setText('音声のある素材を編集版に配置してから実行してください。')
            return
        if self.editor.shell.desk.busy:
            self.status.setText('収録や素材の処理が終わってから実行してください。')
            return
        clip = self.clips[clip_id]
        asset = self.editor.library.assets[clip.asset_id]
        model, language = self.model.currentData(), self.language.currentData()
        self.editor.preview.pause()
        self.revision = self.editor.revision
        self.cues = []
        self.table.setRowCount(0)
        self.add_button.setEnabled(False)
        self._set_inputs(False)
        self.start_button.setText('中止')
        self.start_button.clicked.disconnect()
        self.start_button.clicked.connect(self.cancel_job)
        self.status.setText('文字起こしを準備しています…')
        self.jobs.submit('transcription', lambda cancel: transcribe_clip(
            asset.path, clip.source_in, clip.duration, clip.start,
            float(self.editor.sequence.rate), model, language, cancel, self.jobs.progress.emit))

    def cancel_job(self):
        self.jobs.cancel.set()
        self.status.setText('中止しています。作業中のファイルは破棄し、元の音声は保持します。')
        self.start_button.setEnabled(False)

    def finished(self, tag, result, error):
        if tag != 'transcription':
            return
        self._set_inputs(True)
        self.start_button.setEnabled(True)
        self.start_button.setText('もう一度文字起こし')
        self.start_button.clicked.disconnect()
        self.start_button.clicked.connect(self.start_transcription)
        if error:
            self.status.setText(error)
            return
        if self.editor.revision != self.revision:
            self.status.setText('解析中に編集が変更されました。字幕を追加する前に、もう一度文字起こししてください。')
            return
        self.cues = split_timed_cues(result, self.max_chars.value(), self.max_words.value(),
            self.line_break.isChecked(), self.line_chars.value(), float(self.editor.sequence.rate))
        self.table.setRowCount(len(self.cues))
        for row, (start, end, text) in enumerate(self.cues):
            for column, value in enumerate((f'{start:.2f}', f'{end:.2f}', text)):
                item = QTableWidgetItem(value)
                if column < 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        self.table.selectAll()
        self.add_button.setEnabled(bool(self.cues))
        self.status.setText(f'{len(self.cues)}個の仮テロップを作成しました。文章を確認し、必要なら編集してから追加してください。')

    def add_selected(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not rows:
            QMessageBox.information(self, '仮テロップ', '追加する字幕を選択してください。')
            return
        entries = []
        try:
            for row in rows:
                start = float(self.table.item(row, 0).text())
                end = float(self.table.item(row, 1).text())
                text = self.table.item(row, 2).text().strip()
                if end <= start or not text:
                    raise ValueError('字幕の開始・終了時間と文字を確認してください。')
                entries.append((start, end, text))
            if add_entries(self.editor, entries):
                try:
                    path = save_generated(self.project, entries)
                    self.status.setText(f'{len(entries)}個の仮テロップを追加し、字幕ライブラリに保存しました：{path.name}')
                except OSError as exc:
                    QMessageBox.warning(self, '字幕ファイルを保存できません', f'仮テロップはタイムラインへ追加しましたが、SRT保存に失敗しました。\n{exc}')
                self.accept()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, '仮テロップを追加できません', str(exc))

    def _set_inputs(self, enabled):
        for widget in (self.audio, self.model, self.language, self.max_chars, self.max_words,
                       self.line_break, self.line_chars, self.select_all, self.clear_selection):
            widget.setEnabled(enabled)

    def done(self, result):
        self.jobs.shutdown()
        super().done(result)
