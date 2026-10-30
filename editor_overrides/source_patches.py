"""Small, explicit edits to upstream Cut Raft files before native packaging."""
from pathlib import Path


PATCHES = {
    'model.py': [
        ('            if not 0 <= clip.volume <= 1:\n                raise ValueError("音量は 0〜100% で指定してください。")',
         '            if not 0 <= clip.volume <= 5:\n                raise ValueError("音量は 0〜500% で指定してください。")'),
        ('or not 0 <= value <= 1):\n                    raise ValueError("音量キーフレームの範囲が不正です。")',
         'or not 0 <= value <= 5):\n                    raise ValueError("音量キーフレームは0〜500%の範囲で指定してください。")'),
        ('            if clip.track >= 0 and not assets[clip.asset_id].has_video:',
         '            if clip.track > 0 and not assets[clip.asset_id].has_video:'),
        ('        video = any(c.track >= 0 for c in chosen)\n        track = max(c.track for c in chosen) if video else min(c.track for c in chosen)',
         '        video = any(self.assets[c.asset_id].has_video for c in chosen)\n        track = max((c.track for c in chosen if self.assets[c.asset_id].has_video), default=0) if video else min(c.track for c in chosen)'),
    ],
    'storage.py': [
        ('            library.sequences.append(sequence)',
         '            sequence.video_tracks = max(2, sequence.video_tracks)\n            sequence.audio_tracks = max(2, sequence.audio_tracks)\n            library.sequences.append(sequence)'),
    ],
    'timeline.py': [
        ('import json\nfrom html import escape',
         'import json\nfrom collections import OrderedDict\nfrom html import escape'),
        ("        self._filmstrip_signatures = {}\n        self.playhead, self.selected_id, self.selected_ids = 0, '', set()",
         "        self._filmstrip_signatures = {}\n        self._wave_buffers = OrderedDict()\n        self.playhead, self.selected_id, self.selected_ids = 0, '', set()"),
        ('height = 72 if track >= 0 else 48', 'height = 88 if track >= 0 else 60'),
        ('self.sequence.video_tracks * 72 + self.sequence.audio_tracks * 48', 'self.sequence.video_tracks * 88 + self.sequence.audio_tracks * 60'),
        ('        if follow and (self.x(frame) > self.viewport().width() - 30 or self.x(frame) < self.header):\n            self.horizontalScrollBar().setValue(max(0, round(frame * self.scale - 100)))',
         '        x = self.x(frame)\n        width = self.viewport().width()\n        if x < self.header + 8 or x > width - 8 or (follow and (x > width - 30 or x < self.header + 30)):\n            self.horizontalScrollBar().setValue(max(0, round(frame * self.scale - 100)))'),
        ('source+4/self.scale', 'source+2/self.scale'),
        ('        x = self.x(self.playhead)\n        if x >= self.header:\n            p.setPen(QPen(QColor(\'#be352b\'), 2))\n            p.drawLine(round(x), 28, round(x), self.viewport().height())\n            p.fillRect(QRectF(x - 4, 24, 8, 8), QColor(\'#be352b\'))',
         '        x = self.x(self.playhead)\n        if self.header <= x <= self.viewport().width():\n            p.setPen(QPen(QColor(\'#7f1714\'), 4))\n            p.drawLine(round(x), 30, round(x), self.viewport().height())\n            p.setPen(Qt.PenStyle.NoPen)\n            p.setBrush(QColor(\'#c62828\'))\n            p.drawPolygon(QPolygonF([QPointF(x - 7, 24), QPointF(x + 7, 24), QPointF(x, 36)]))\n            p.setPen(QPen(QColor(\'#ffffff\'), 1))\n            p.drawLine(round(x), 38, round(x), self.viewport().height())'),
        ('        _, y, height = self.row(clip.track)\n        return (y + 49, y + height - 6) if clip.track >= 0 else (y + 6, y + height - 6)',
         '        _, y, height = self.row(clip.track)\n        if not self.library.assets[clip.asset_id].has_video:\n            return (y + 6, y + height - 6)\n        return (y + 49, y + height - 6) if clip.track >= 0 else (y + 6, y + height - 6)'),
        ("            color = '#b7cde1' if clip.track >= 0 else '#bed3bb'", "            color = '#b7cde1' if asset.has_video else '#bed3bb'"),
        ('            if clip.track >= 0:\n                film_top, film_height = rect.top()+22, 20',
         '            if asset.has_video:\n                film_top, film_height = rect.top()+22, 20'),
        ('            if clip.track >= 0:\n                p.drawText(rect.adjusted(5, 5, -3, -2), Qt.AlignmentFlag.AlignTop, asset.name)',
         '            if asset.has_video:\n                p.drawText(rect.adjusted(5, 5, -3, -2), Qt.AlignmentFlag.AlignTop, asset.name)'),
        ('        if asset.id not in self._wave_buffers:\n            self._wave_buffers[asset.id]=self.cache.waveform(asset)\n        peaks=self._wave_buffers[asset.id]',
         '        key = (asset.id, self.cache.source_signature(asset))\n        peaks = self._wave_buffers.get(key)\n        if peaks is None:\n            peaks = self.cache.waveform(asset)\n            if peaks is not None:\n                self._wave_buffers[key] = peaks\n                self._wave_buffers.move_to_end(key)\n                while len(self._wave_buffers) > 64:\n                    self._wave_buffers.popitem(last=False)'),
        ('        self._wave_buffers = {}\n', ''),
        ('                    for px in range(left,right,4):', '                    for px in range(left,right,2)'),
    ],
    'app.py': [
        ('        self.volume_field.setRange(0, 100)', '        self.volume_field.setRange(0, 500)'),
        ('        self.volume_slider.setRange(0, 100)', '        self.volume_slider.setRange(0, 500)'),
        ('            self.preview.audio.setVolume(value / 100)', '            self.preview.audio.setVolume(min(1.0, value / 100))'),
        ("        self.import_button = self.button('素材を読み込む…', self.import_assets)",
         "        self.import_button = self.button('素材を読み込み（動画・画像・音声）…', self.import_assets)"),
        ("        self.append_button = self.button('末尾に追加', self.append_asset)",
         "        self.append_button = self.button('選択素材を末尾に追加', self.append_asset)"),
        ("        self.insert_button = self.button('挿入', self.insert_asset)",
         "        self.insert_button = self.button('再生位置に挿入', self.insert_asset)"),
        ('self.action("再生位置で分割", self.split, "Ctrl+B")',
         'self.action("再生位置で全トラックを分割", self.split, "Ctrl+B")'),
        ("        frame = self.timeline.playhead\n        def apply():\n            tracks = {c.track for c in self.selected_clips()} or {self.timeline.current_track}\n            rights = [right for track in tracks if (right := self.sequence.split(frame, track))]\n            if rights:\n                self.timeline.selected_id = rights[0].id\n                self.timeline.selected_ids = {c.id for c in rights}\n        self.commit(\"再生位置で分割\", apply)",
         "        frame = self.timeline.playhead\n        tracks = {c.track for c in self.sequence.active_clips(frame)}\n        if not tracks:\n            self.statusBar().showMessage('再生位置をまたぐクリップがありません。', 3000)\n            return\n        def apply():\n            rights = [right for track in sorted(tracks) if (right := self.sequence.split(frame, track))]\n            if rights:\n                self.timeline.selected_id = rights[0].id\n                self.timeline.selected_ids = {c.id for c in rights}\n        self.commit('再生位置で全トラックを分割', apply)"),
        ('                destination = track if asset.has_video or track < 0 else -1',
         '                destination = track if track == 0 or asset.has_video or track < 0 else -1'),
        ('        if not a.has_video and track >= 0:', '        if not a.has_video and track > 0:'),
    ],
    'rendering.py': [
        ('                if clip.track < 0:\n                    continue\n                asset = self.library.assets[clip.asset_id]',
         '                asset = self.library.assets[clip.asset_id]\n                if not asset.has_video:\n                    continue'),
    ],
    'title_editor.py': [
        ("        motion_layout.addLayout(key_row)\n        preset_row = QHBoxLayout()",
         "        motion_layout.addLayout(key_row)\n        adjust_form = QFormLayout()\n        self.adjust_sliders = {}\n        for name, label, minimum, maximum, scale in [('x','左右位置',-100000,100000,1),('y','上下位置',-100000,100000,1),('scale','大きさ',10,400,100),('rotation','角度',-180,180,1)]:\n            slider = QSlider(Qt.Orientation.Horizontal); slider.setRange(minimum, maximum); slider.setTracking(True)\n            slider.valueChanged.connect(lambda value, name=name, scale=scale: self.adjust_selected_key(name, value / scale))\n            self.adjust_sliders[name] = slider; adjust_form.addRow(label, slider)\n        motion_layout.addLayout(adjust_form)\n        preset_row = QHBoxLayout()"),
        ("        if self.keys.rowCount():\n            self.keys.selectRow(0)",
         "        if self.keys.rowCount():\n            self.keys.selectRow(0)\n        selected = sorted(data['keys'], key=lambda k:k['frame'])\n        key = selected[self.keys.currentRow()] if 0 <= self.keys.currentRow() < len(selected) else {}\n        for name, slider in self.adjust_sliders.items():\n            factor = 100 if name == 'scale' else 1\n            slider.blockSignals(True); slider.setValue(round(key.get(name, 1 if name == 'scale' else 0) * factor)); slider.blockSignals(False)"),
        ('    def motion_preset(self, preset):',
         "    def adjust_selected_key(self, name, value):\n        phase = self.spec['motion'][self.phase.currentData()]\n        ordered = sorted(phase['keys'], key=lambda k:k['frame'])\n        row = self.keys.currentRow()\n        if 0 <= row < len(ordered):\n            ordered[row][name] = value\n            self.phase_seek(ordered[row]['frame']); self.render()\n\n    def motion_preset(self, preset):"),
    ],
}


def apply_source_patches(package):
    package = Path(package)
    for filename, replacements in PATCHES.items():
        path = package / filename
        content = path.read_text(encoding='utf-8')
        for old, new in replacements:
            if new and new in content:
                continue
            if old not in content:
                if not new:
                    continue
                raise RuntimeError(f'Cut Raftの{filename}が想定と異なります。適用箇所: {old.splitlines()[0]}')
            content = content.replace(old, new, 1)
        path.write_text(content, encoding='utf-8')
