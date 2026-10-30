from pathlib import Path
import json
import os
import tempfile
from uuid import uuid4
from datetime import datetime
import unicodedata
from cutraft.model import Library
from cutraft.storage import save_library

FOLDERS = {'recordings': '01-recordings', 'assets': '02-assets', 'edit': '03-edit', 'exports': '04-exports'}


def atomic_text(file, text):
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.saving-', dir=file.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, file)
    finally:
        Path(temporary).unlink(missing_ok=True)


def title_key(text):
    return unicodedata.normalize('NFKC', text).strip().casefold()


class DuplicateProject(ValueError):
    def __init__(self, project):
        self.project = project
        super().__init__('同じ名前の動画があります。一覧から続きを開けます。')


class Workspace:
    def __init__(self, settings_file, default_root):
        self.file = Path(settings_file)
        self.data = {'root': str(default_root), 'projects': [], 'apps': {}, 'voice': '未定', 'workflowVersion': 2}
        if self.file.is_file():
            loaded = json.loads(self.file.read_text(encoding='utf-8'))
            if not isinstance(loaded, dict) or not isinstance(loaded.get('projects'), list):
                raise ValueError('制作デスクの設定を読み込めません。元の設定は保持しています。')
            if loaded.get('workflowVersion', 1) < 2:
                for project in loaded['projects']:
                    project['steps'][1] = False
                loaded['workflowVersion'] = 2
            self.data.update(loaded)
        # One registered entry per physical project. Keep all files on disk untouched.
        unique, folders = [], set()
        for project in self.data['projects']:
            if not isinstance(project, dict) or not all(k in project for k in ('id','title','folder')):
                continue
            folder = str(Path(project['folder']).expanduser().resolve())
            if folder in folders: continue
            folders.add(folder); project['folder'] = folder
            unique.append(project)
        self.data['projects'] = unique

    def save(self):
        atomic_text(self.file, json.dumps(self.data, ensure_ascii=False, indent=2))

    def available_projects(self):
        # Retain unavailable entries for removable drives and moved-folder recovery,
        # but never present them as folders that can currently be opened.
        return [p for p in self.data['projects'] if Path(p['folder']).is_dir()]

    def delete(self, project, move_to_trash=None):
        if project not in self.data['projects']:raise ValueError('動画を選択してください。')
        folder=Path(project['folder'])
        metadata=folder/'project.json'
        if not folder.is_dir():raise ValueError('動画フォルダが見つかりません。一覧を更新してください。')
        if not metadata.is_file() or json.loads(metadata.read_text(encoding='utf-8')).get('id')!=project['id']:
            raise ValueError('動画情報が一致しないため削除できません。フォルダを確認してください。')
        if move_to_trash is None:
            from PySide6.QtCore import QFile
            move_to_trash=QFile.moveToTrash
        result=move_to_trash(str(folder))
        # PySide6 versions differ: some expose bool, others (bool, trashPath).
        ok=result[0] if isinstance(result,tuple) else result
        if not ok:raise ValueError('ゴミ箱へ移動できませんでした。使用中のファイルや権限を確認してください。')
        self.data['projects'].remove(project)
        if self.data.get('last_project')==project['id']:self.data.pop('last_project',None)
        self.save()

    def create(self, title, allow_duplicate=False):
        title = title.strip()
        if not title or len(title) > 100 or any(ord(c) < 32 for c in title):
            raise ValueError('タイトルは1〜100文字で入力してください。')
        match = next((p for p in self.available_projects() if title_key(p['title']) == title_key(title)), None)
        if match and not allow_duplicate: raise DuplicateProject(match)
        folder = Path(self.data['root']).expanduser().resolve() / uuid4().hex
        for name in FOLDERS.values():
            (folder / name).mkdir(parents=True, exist_ok=True)
        library = Library()
        library.active.name = title
        library.active.video_tracks, library.active.audio_tracks = 1, 1
        library.active.width, library.active.height = 1920, 1080
        library.active.fps_num, library.active.fps_den = 50, 1
        save_library(library, str(folder / FOLDERS['edit'] / 'main.cutraft'))
        project = {'id': folder.name, 'title': title, 'folder': str(folder), 'steps': [False] * 4, 'updated': datetime.now().astimezone().isoformat()}
        atomic_text(folder / 'project.json', json.dumps(project, ensure_ascii=False, indent=2))
        self.data['projects'].insert(0, project)
        try:
            self.save()
        except Exception:
            self.data['projects'].remove(project)
            raise
        return project

    def library_path(self, project):
        return Path(project['folder']) / FOLDERS['edit'] / 'main.cutraft'

    def ensure_library(self, project):
        path=self.library_path(project)
        if path.is_file():return path
        root=Path(project['folder'])
        if not root.is_dir() or not (root/'project.json').is_file():
            raise ValueError('動画フォルダが見つかりません。')
        library=Library();library.active.name=project['title']
        library.active.width,library.active.height=1920,1080
        library.active.fps_num,library.active.fps_den=50,1
        library.active.video_tracks=library.active.audio_tracks=1
        path.parent.mkdir(parents=True,exist_ok=True)
        save_library(library,str(path))
        return path

    def save_steps(self, project, steps):
        if len(steps) != 4 or any(type(v) is not bool for v in steps):
            raise ValueError('進捗の形式が正しくありません。')
        previous = project['steps']
        project['steps'] = steps
        try:
            self.save()
        except Exception:
            project['steps'] = previous
            raise
        # settings.json is authoritative; project.json is an exportable per-project copy.
        atomic_text(Path(project['folder']) / 'project.json', json.dumps(project, ensure_ascii=False, indent=2))

    def remember(self, project):
        self.data['last_project'] = project['id']
        self.save()

    def rename(self, project, title):
        title = title.strip()
        if not title or len(title)>100 or any(ord(c)<32 for c in title):
            raise ValueError('タイトルは1〜100文字で入力してください。')
        if any(p is not project and title_key(p['title'])==title_key(title) for p in self.available_projects()):
            raise ValueError('同じ名前の動画がすでにあります。')
        previous=project['title']; project['title']=title
        try:
            atomic_text(Path(project['folder'])/'project.json',json.dumps(project,ensure_ascii=False,indent=2))
            self.save()
        except Exception:
            project['title']=previous
            raise

    def attach(self, folder):
        folder=Path(folder).expanduser().resolve()
        if folder.is_file() and folder.name=='project.json': folder=folder.parent
        metadata=folder/'project.json'
        if not metadata.is_file(): raise ValueError('このフォルダには動画情報（project.json）がありません。')
        project=json.loads(metadata.read_text(encoding='utf-8'))
        if not isinstance(project,dict) or not all(k in project for k in ('id','title')):
            raise ValueError('動画情報を読み込めません。')
        existing=next((p for p in self.data['projects'] if Path(p['folder']).resolve()==folder),None)
        if existing:return existing
        if not (folder/FOLDERS['edit']/'main.cutraft').is_file():
            raise ValueError('編集データが見つかりません。元のフォルダを確認してください。')
        # Recover a moved project and rebase its own legacy absolute asset paths.
        old=Path(project.get('folder',str(folder))).expanduser().resolve()
        if old!=folder:
            from cutraft.storage import load_library
            library=load_library(str(folder/FOLDERS['edit']/'main.cutraft'))
            changed=False
            for asset in library.assets.values():
                if asset.kind not in ('media','image'):continue
                try: candidate=folder/Path(asset.path).relative_to(old)
                except ValueError:continue
                if candidate.is_file():asset.path=str(candidate.resolve());changed=True
            if changed:save_library(library,str(folder/FOLDERS['edit']/'main.cutraft'))
        project['folder']=str(folder)
        project.setdefault('steps',[False]*4)
        # A relocated project replaces its stale index entry, rather than duplicating it.
        self.data['projects']=[p for p in self.data['projects'] if p['id']!=project['id']]
        self.data['projects'].insert(0,project)
        self.save()
        atomic_text(metadata,json.dumps(project,ensure_ascii=False,indent=2))
        return project
