"""Recoverable, project-local recording manifests. Paths remain portable."""
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import json
import os
import shutil
from .workspace import atomic_text

WIDTH, HEIGHT, FPS = 1920, 1080, 50


def create_take(project, options, temporary=False):
    folder = Path(project['folder']) / ('.pending-recordings' if temporary else '01-recordings') / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid4().hex[:6])
    folder.mkdir(parents=True, exist_ok=False)
    data = {'format': 'tanutsuna-take-v1', 'id': folder.name, 'state': 'preparing',
            'created': datetime.now().astimezone().isoformat(), 'width': WIDTH, 'height': HEIGHT,
            'fps': FPS, 'options': options, 'duration': 0, 'files': {}, 'warnings': [], 'imported': False}
    write_take(folder, data)
    return folder, data


def write_take(folder, data):
    if not Path(folder).is_dir():raise ValueError('収録フォルダが削除されています。')
    atomic_text(Path(folder) / 'take.json', json.dumps(data, ensure_ascii=False, indent=2))


def discard_pending(folder):
    folder=Path(folder)
    if folder.parent.name!='.pending-recordings' or not folder.is_dir():return
    data=load_take(folder)
    if data.get('id')!=folder.name:raise ValueError('一時収録の情報が一致しません。')
    shutil.rmtree(folder)


def publish_take(folder):
    folder=Path(folder)
    if folder.parent.name!='.pending-recordings':return folder
    destination=folder.parent.parent/'01-recordings'/folder.name
    destination.parent.mkdir(exist_ok=True)
    os.rename(folder,destination)
    return destination


def ready_files(folder,take):
    if take.get('state')!='ready':return {}
    return {role:(Path(folder)/file).resolve() for role,file in take.get('files',{}).items()
        if role in ('screen','desktop','voice','vtuber','yukkuri','bgm','sfx') and (Path(folder)/file).is_file()}


def load_take(folder):
    folder = Path(folder)
    data = json.loads((folder / 'take.json').read_text(encoding='utf-8'))
    if data.get('format') != 'tanutsuna-take-v1':
        raise ValueError('収録情報の形式が違います。')
    for file in data.get('files', {}).values():
        path = (folder / file).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError('素材のパスが収録フォルダ外を指しています。')
    return data


def takes_for(project):
    root = Path(project['folder']) / '01-recordings'
    result = []
    for file in sorted(root.glob('*/take.json'), reverse=True):
        try:
            result.append((file.parent, load_take(file.parent)))
        except (OSError, ValueError):
            continue
    return result


def audio_filter(offset, duration):
    # Match actual first-sample timestamps, then pad/trim to the video duration.
    prefix = f'atrim=start={-offset:.6f},asetpts=PTS-STARTPTS,' if offset < 0 else f'adelay={round(offset*1000)}:all=1,'
    return prefix + f'apad,atrim=duration={duration:.6f}'


def recover_captures(project):
    """Expose interrupted captures without deleting raw video, audio or motion."""
    root = Path(project['folder']) / '.pending-recordings'
    for manifest in root.glob('*/take.json'):
        try:
            folder = manifest.parent
            take = load_take(folder)
            video = folder/'screen.mp4'
            if take.get('state') == 'preparing' or not video.is_file() or video.stat().st_size < 128:
                continue
            take['state'] = 'incomplete'
            note = '前回終了時の収録を保持しました。「未完了の整理・映像化を再開」で保存できます。'
            if note not in take['warnings']: take['warnings'].append(note)
            write_take(folder, take)
            publish_take(folder)
        except (OSError, ValueError):
            continue
