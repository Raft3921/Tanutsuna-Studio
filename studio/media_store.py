"""Copy external media into its video project once, with content deduplication."""
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import os
from .workspace import atomic_text


def collect_media(project, paths, cancel, preserve_order=False):
    from cutraft.importer import MEDIA_EXTENSIONS
    root=Path(project['folder']).resolve()
    owned=root/'02-assets';owned.mkdir(parents=True,exist_ok=True)
    index_file=owned/'media-index.json'
    index=json.loads(index_file.read_text(encoding='utf-8')) if index_file.is_file() else {}
    results=[]
    candidates=[]
    for path in paths:
        path=Path(path).expanduser().resolve()
        if path.is_dir():candidates.extend(p for p in path.rglob('*') if p.is_file() and '.tanutsuna-trash' not in p.parts and p.suffix.lower() in MEDIA_EXTENSIONS)
        elif path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:candidates.append(path)
    if not candidates:raise ValueError('追加できる動画・音声・画像が見つかりませんでした。')
    for source in dict.fromkeys(candidates):
        if cancel.is_set():raise RuntimeError('素材の追加を中断しました。コピー済みの素材は保持しています。')
        if source.is_relative_to(root):results.append(str(source));continue
        temporary=owned/('.copying-'+uuid4().hex)
        digest=hashlib.sha256()
        try:
            with source.open('rb') as reader,temporary.open('xb') as writer:
                while True:
                    if cancel.is_set():raise RuntimeError('素材の追加を中断しました。')
                    chunk=reader.read(1024*1024)
                    if not chunk:break
                    digest.update(chunk);writer.write(chunk)
                writer.flush();os.fsync(writer.fileno())
            key=digest.hexdigest()
            existing=owned/index[key] if key in index else None
            if existing and existing.is_file():
                results.append(str(existing.resolve()));continue
            target=owned/(source.stem[:80]+'-'+key[:12]+source.suffix.lower())
            os.replace(temporary,target)
            index[key]=target.name
            atomic_text(index_file,json.dumps(index,ensure_ascii=False,indent=2))
            results.append(str(target.resolve()))
        finally:temporary.unlink(missing_ok=True)
    return results if preserve_order else list(dict.fromkeys(results))
