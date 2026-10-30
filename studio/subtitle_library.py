"""Project-local subtitle shelf, separate from the media bin."""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QVBoxLayout)
from cutraft.model import Asset, Clip, uid
from cutraft.titles import default_title


TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})")


def shelf(project):
    folder = Path(project['folder']) / '03-edit' / 'subtitles'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_srt(source, destination_folder):
    source = Path(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:10]
    name = re.sub(r'[^\wぁ-んァ-ヶ一-龠.-]+', '_', source.stem).strip('._') or '字幕'
    destination = Path(destination_folder) / f'{name}-{digest}.srt'
    if not destination.exists():
        shutil.copy2(source, destination)
    return destination


def discover(project, folder):
    # Keep generated transcript files in their recording folders; the shelf holds
    # independent copies that remain usable when a take is moved or cleaned up.
    from .takes import takes_for
    for take_folder, take in takes_for(project):
        for role in ('subtitles', 'transcript'):
            filename = take.get('files', {}).get(role)
            if not filename:
                continue
            try:
                save_srt(take_folder / filename, folder)
            except OSError:
                continue


def parse_srt(path):
    text = Path(path).read_text(encoding='utf-8-sig', errors='replace')
    cues = []
    for block in re.split(r'\n\s*\n', text.replace('\r', '').strip()):
        lines = block.splitlines()
        timing = next((i for i, line in enumerate(lines) if '-->' in line), -1)
        if timing < 0:
            continue
        pair = lines[timing].split('-->', 1)
        left, right = TIME.search(pair[0]), TIME.search(pair[1])
        if not left or not right:
            continue
        def seconds(match):
            h, m, s, ms = map(int, match.groups())
            return h * 3600 + m * 60 + s + ms / 1000
        start, end = seconds(left), seconds(right)
        caption = '\n'.join(lines[timing + 1:]).strip()
        if caption and end > start:
            cues.append((start, end, caption))
    return cues


class SubtitleLibrary(QDialog):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.folder = shelf(editor.shell.desk.selected_project())
        discover(editor.shell.desk.selected_project(), self.folder)
        self.setWindowTitle('字幕ライブラリ')
        self.resize(520, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('字幕ファイルを素材とは別に保存します。選んだ字幕、または保存済みの全字幕をタイムラインへ追加できます。'))
        self.items = QListWidget()
        self.items.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.items, 1)
        actions = QHBoxLayout()
        self.add_file = QPushButton('SRTを保存…')
        self.add_file.clicked.connect(self.import_srt)
        actions.addWidget(self.add_file)
        self.add_selected = QPushButton('選択を追加')
        self.add_selected.clicked.connect(lambda: self.place(False))
        actions.addWidget(self.add_selected)
        self.add_all = QPushButton('保存済みを全て追加')
        self.add_all.clicked.connect(lambda: self.place(True))
        actions.addWidget(self.add_all)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.items.clear()
        for path in sorted(self.folder.glob('*.srt'), key=lambda p: p.name.casefold()):
            item = QListWidgetItem(path.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            try:
                item.setToolTip(f'{len(parse_srt(path))} 個の字幕')
            except OSError:
                item.setToolTip('読み込めません')
            self.items.addItem(item)

    def import_srt(self):
        files, _ = QFileDialog.getOpenFileNames(self, '字幕ファイルを保存', '', '字幕 (*.srt);;すべて (*)')
        for file in files:
            try:
                save_srt(file, self.folder)
            except OSError as exc:
                QMessageBox.warning(self, '字幕を保存できません', str(exc))
        self.refresh()

    def place(self, all_saved):
        paths = ([self.items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.items.count())]
                 if all_saved else [item.data(Qt.ItemDataRole.UserRole) for item in self.items.selectedItems()])
        if not paths:
            QMessageBox.information(self, '字幕ライブラリ', '追加する字幕を選んでください。')
            return
        editor = self.editor
        sequence = editor.sequence
        rate = sequence.rate
        entries = []
        try:
            for path in paths:
                entries.extend(parse_srt(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '字幕を読み込めません', str(exc))
            return
        if not entries:
            QMessageBox.information(self, '字幕ライブラリ', '選んだファイルに字幕がありません。')
            return

        def apply():
            # Subtitle overlays occupy the upper video layers and automatically
            # share a layer only when their time ranges do not overlap.
            for start_sec, end_sec, caption in entries:
                start = max(0, round(start_sec * rate))
                end = max(start + 1, round(end_sec * rate))
                duration = end - start
                track = next((candidate for candidate in range(1, sequence.video_tracks)
                              if all(c.track != candidate or c.end <= start or c.start >= end
                                     for c in sequence.clips)), None)
                if track is None:
                    if sequence.video_tracks >= 16:
                        raise ValueError('字幕の重なりが多く、映像レイヤーの上限を超えました。')
                    track = sequence.video_tracks
                    sequence.video_tracks += 1
                spec = default_title(caption)
                spec['canvas'] = [sequence.width, sequence.height]
                spec['size'] = max(36, round(sequence.height * .052))
                spec['motion'] = {
                    'in': {'duration': 0, 'keys': []},
                    'middle': {'duration': 1, 'keys': [{'frame': 0, 'x': 0., 'y': sequence.height * .36,
                                                         'scale': 1., 'rotation': 0., 'opacity': 1.}]},
                    'out': {'duration': 0, 'keys': []},
                }
                asset = Asset(uid(), '', round(duration * 1000 / rate), sequence.width, sequence.height,
                              sequence.fps_num, sequence.fps_den, False, True, 'title', spec, caption[:50])
                editor.library.assets[asset.id] = asset
                sequence.clips.append(Clip(uid(), asset.id, start, 0, duration, track=track, title=spec))
                event = next((e for e in editor.library.events if e.id == editor.library.active_event_id), None)
                if event:
                    event.asset_ids.append(asset.id)

        try:
            if editor.commit('字幕をタイムラインに追加', apply):
                editor.preview.seek(min((round(start * rate) for start, _, _ in entries), default=0))
                editor.statusBar().showMessage(f'{len(entries)} 個の字幕を追加しました。', 5000)
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, '字幕を追加できません', str(exc))
