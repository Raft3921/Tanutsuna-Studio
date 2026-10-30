"""Small, explicit edits to upstream Cut Raft files before native packaging."""
from pathlib import Path


PATCHES = {
    'model.py': [
        ('    video_tracks: int = 2\n    audio_tracks: int = 2',
         '    video_tracks: int = 1\n    audio_tracks: int = 1'),
        ('            if clip.track >= 0 and not assets[clip.asset_id].has_video:',
         '            if clip.track > 0 and not assets[clip.asset_id].has_video:'),
        ('        video = any(c.track >= 0 for c in chosen)\n        track = max(c.track for c in chosen) if video else min(c.track for c in chosen)',
         '        video = any(self.assets[c.asset_id].has_video for c in chosen)\n        track = max((c.track for c in chosen if self.assets[c.asset_id].has_video), default=0) if video else min(c.track for c in chosen)'),
    ],
    'timeline.py': [
        ('import json\nfrom bisect import bisect_left, bisect_right',
         'import json\nfrom collections import OrderedDict\nfrom bisect import bisect_left, bisect_right'),
        ("        self._filmstrip_signatures = {}\n        self.playhead, self.selected_id, self.selected_ids = 0, '', set()",
         "        self._filmstrip_signatures = {}\n        self._wave_buffers = OrderedDict()\n        self.playhead, self.selected_id, self.selected_ids = 0, '', set()"),
        ("            p.drawText(QRectF(14, y, 48, height), Qt.AlignmentFlag.AlignVCenter, f'V{track + 1}' if track >= 0 else f'A{-track}')",
         "            label = ('メイン' if track == 0 else f'上レイヤー {track + 1}') if track >= 0 else f'音声 {abs(track)}'\n            p.drawText(QRectF(8, y, self.header - 12, height), Qt.AlignmentFlag.AlignVCenter, label)"),
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
    ],
    'app.py': [
        ("            self.destination_track.addItem(f'映像 V{track + 1}', track)",
         "            self.destination_track.addItem('メイン' if track == 0 else f'上レイヤー {track + 1}', track)"),
        ("            self.destination_track.addItem(f'音声 A{track}', -track)",
         "            self.destination_track.addItem(f'音声 {track}', -track)"),
        ('                destination = track if asset.has_video or track < 0 else -1',
         '                destination = track if track == 0 or asset.has_video or track < 0 else -1'),
        ('        if not a.has_video and track >= 0:', '        if not a.has_video and track > 0:'),
    ],
    'rendering.py': [
        ('                if clip.track < 0:\n                    continue\n                asset = self.library.assets[clip.asset_id]',
         '                asset = self.library.assets[clip.asset_id]\n                if not asset.has_video:\n                    continue'),
    ],
}


def apply_source_patches(package):
    package = Path(package)
    for filename, replacements in PATCHES.items():
        path = package / filename
        content = path.read_text(encoding='utf-8')
        for old, new in replacements:
            if new in content:
                continue
            if old not in content:
                raise RuntimeError(f'Cut Raftの{filename}が想定と異なります。レイヤー表示の変更を適用できません。')
            content = content.replace(old, new, 1)
        path.write_text(content, encoding='utf-8')
