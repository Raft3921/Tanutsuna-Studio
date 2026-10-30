"""One undoable import places all take tracks at a common timeline origin."""
from pathlib import Path
from fractions import Fraction
import json
from cutraft.model import Asset, Clip, uid
from cutraft.video_effects import new_effect
from .takes import write_take
from .takes import ready_files
import math


def import_take(editor, workspace, project, folder, take):
    if hasattr(editor, 'load_project'):
        editor = editor.load_project(project, sync=False)
        if editor is None:return False
    folder = Path(folder)
    path = str(workspace.library_path(project))
    if editor.path != path:
        editor.open_library(path)
    if editor.path != path: return False
    if editor.importer.running: raise ValueError('素材の読み込みが完了してから収録を読み込んでください。')
    supported = ('screen', 'desktop', 'voice', 'vtuber', 'yukkuri')
    files = ready_files(folder,take)
    if not files:raise ValueError('編集できる完成済みの収録素材がありません。')
    existing = {str(Path(a.path).resolve()): a for a in editor.library.assets.values()}
    from .audio_tools import sequence_tree
    root=editor.sequence
    if root.kind=='compound' and editor.sequence_stack:
        root=next(s for s in editor.library.sequences if s.id==editor.sequence_stack[0][0])
    sequences=list(sequence_tree(editor.library,root))
    placed_ids = {c.asset_id for seq in sequences for c in seq.clips}
    if files and all(str(file) in existing and (existing[str(file)].id in placed_ids or take.get('imported')) for file in files.values()):
        if not take.get('imported'):
            take['imported'] = True; write_take(folder, take)
        return True
    screen = existing.get(str(files.get('screen', '')))
    target=next((seq for seq in sequences if screen and any(c.asset_id==screen.id for c in seq.clips)),root)
    old = next((c for c in target.clips if screen and c.asset_id == screen.id), None)
    start = old.start if old else target.end
    def apply():
        sequence = target
        # Existing empty projects follow the fixed recording profile.
        if not sequence.clips:
            sequence.width, sequence.height = take.get('width',1920),take.get('height',1080)
            sequence.fps_num, sequence.fps_den = take.get('fps',50),1
        rate = sequence.rate
        frames = max(1, round(take['duration'] * rate))
        base_video_track = old.track if old else 0
        base_audio_track = -1
        if not old and any(c.start < start+frames and c.end > start for c in sequence.clips):
            base_video_track = sequence.video_tracks
            base_audio_track = -sequence.audio_tracks-1
        for role, file in files.items():
            asset = existing.get(str(file))
            alpha=role=='vtuber' and take.get('media',{}).get('vtuber',{}).get('alpha',False)
            previous=take.get('replaced_files',{}).get(role)
            if asset is None and previous:asset=existing.get(str((folder/previous).resolve()))
            if alpha and asset:
                asset.path=str(file)
                asset.width=asset.height=1080
                for placed in sequence.clips:
                    if placed.asset_id==asset.id:
                        placed.effects=[e for e in placed.effects if e.get('type')!='chroma_key']
            if asset and any(c.asset_id == asset.id for c in sequence.clips): continue
            video = role in ('screen', 'vtuber')
            seconds = take.get('media', {}).get(role, {}).get('duration', take['duration'])
            duration = max(1, round(seconds*rate))
            media=take.get('media',{}).get(role,{})
            asset = asset or Asset(uid(), str(file), max(1, math.ceil(seconds*1000)),
                media.get('width',720 if role=='vtuber' else 1920),media.get('height',720 if role=='vtuber' else 1080),
                take.get('fps',50), 1, has_audio=not video, has_video=video)
            if role in ('voice','desktop','bgm','sfx'):asset.payload['studio_audio_role']=role
            editor.library.assets[asset.id] = asset
            event = next((e for e in editor.library.events if e.id == editor.library.active_event_id), None)
            if event and asset.id not in event.asset_ids: event.asset_ids.append(asset.id)
            track = base_video_track+(1 if role == 'vtuber' else 0) if video else base_audio_track-({'desktop':0,'voice':1,'yukkuri':2,'bgm':3,'sfx':4}[role])
            step = 1 if video else -1
            while any(c.track == track and c.start < start+duration and c.end > start for c in sequence.clips):
                track += step
            clip = Clip(uid(), asset.id, start, 0, min(duration,asset.frames(rate)), track=track)
            if role == 'vtuber':
                clip.effects = [] if alpha else [new_effect('chroma_key')]
                clip.scale = .30; clip.x = 650; clip.y = 300
            mix=take.get('live_mix',{}).get(role,{})
            clip.volume=mix.get('volume',1.);clip.muted=mix.get('muted',False)
            if role=='vtuber' and take.get('avatar_transform'):
                transform=take['avatar_transform'];width,height=sequence.width,sequence.height
                clip.x=width*(transform['x']-.5);clip.y=height*(transform['y']-.5);clip.scale=width*transform['size']/height
            if role == 'voice' and 'yukkuri' in files: clip.muted = True
            if role == 'yukkuri':
                for c in sequence.clips:
                    if c.start == start and c.asset_id in {a.id for a in editor.library.assets.values() if a.path == str(files.get('voice',''))}: c.muted = True
            sequence.clips.append(clip)
            if video: sequence.video_tracks = max(sequence.video_tracks, track+1)
            else: sequence.audio_tracks = max(sequence.audio_tracks, -track)
        return True
    changed = editor.commit('収録素材をまとめて配置', apply)
    placed = {editor.library.assets[c.asset_id].path for seq in sequence_tree(editor.library,root) for c in seq.clips}
    # Existing registered clips may have been intentionally removed by editing.
    if take.get('imported'):placed.update(a.path for a in existing.values())
    if any(str(file) not in placed for file in files.values()):
        raise ValueError('素材の配置が完了しませんでした。編集画面のエラーを確認してください。')
    if changed:
        # Refresh the editor's incremental asset-path index, then persist once.
        editor._asset_paths = {a.path for a in editor.library.assets.values()}
        if not editor.save(False): raise ValueError('配置しましたが保存に失敗しました。編集画面で保存先を確認してください。')
    take['imported'] = True
    write_take(folder, take)
    editor.preview.seek(start)
    editor.fit_timeline()
    return True
