import os
"""Transactional SQLite libraries. Media is referenced, never embedded."""
from pathlib import Path
import sqlite3
import json
from dataclasses import asdict
from .model import Asset, Clip, Library, Sequence, Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS extensions (kind TEXT, id TEXT, payload TEXT, PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, path TEXT NOT NULL, duration_ms INTEGER NOT NULL,
 width INTEGER, height INTEGER, fps_num INTEGER, fps_den INTEGER, has_audio INTEGER);
CREATE TABLE IF NOT EXISTS sequences (
 id TEXT PRIMARY KEY, name TEXT, fps_num INTEGER, fps_den INTEGER, width INTEGER, height INTEGER);
CREATE TABLE IF NOT EXISTS clips (
 id TEXT PRIMARY KEY, sequence_id TEXT REFERENCES sequences(id),
 asset_id TEXT REFERENCES assets(id), start INTEGER, source_in INTEGER, duration INTEGER, volume REAL);
CREATE TABLE IF NOT EXISTS volume_keys (
 clip_id TEXT REFERENCES clips(id), frame INTEGER, gain REAL,
 PRIMARY KEY (clip_id, frame));
CREATE TABLE IF NOT EXISTS clip_properties (
 clip_id TEXT PRIMARY KEY REFERENCES clips(id), track INTEGER, x REAL, y REAL,
 scale REAL, opacity REAL, muted INTEGER, compressor INTEGER, threshold REAL,
 ratio REAL, attack REAL, release REAL, makeup REAL);
CREATE TABLE IF NOT EXISTS sequence_properties (
 sequence_id TEXT PRIMARY KEY REFERENCES sequences(id), video_tracks INTEGER, audio_tracks INTEGER);
CREATE TABLE IF NOT EXISTS asset_properties (
 asset_id TEXT PRIMARY KEY REFERENCES assets(id), has_video INTEGER);
"""


def save_library(library: Library, path: str):
    library.validate()
    path = library_database(path, create=True)
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4, 5, 6):
            raise ValueError("このライブラリの形式には対応していません。")
        if version in (1,2,3):
            backup_path=Path(str(path)+'.before-v4.bak')
            if not backup_path.exists():
                backup=sqlite3.connect(str(backup_path))
                try:
                    db.backup(backup)
                finally:
                    backup.close()
        has_effects = any(c.effects for s in library.sequences for c in s.clips)
        has_animation = any(c.transform_keys for s in library.sequences for c in s.clips)
        if version in (4, 5) and has_animation:
            backup_path = Path(str(path)+'.before-v6.bak')
            if not backup_path.exists():
                backup = sqlite3.connect(str(backup_path))
                try:
                    db.backup(backup)
                finally:
                    backup.close()
        if version == 4 and has_effects:
            backup_path = Path(str(path)+'.before-v5.bak')
            if not backup_path.exists():
                backup = sqlite3.connect(str(backup_path))
                try:
                    db.backup(backup)
                finally:
                    backup.close()
        # DDL and all content are committed together; SQLite recovers interrupted writes.
        db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
        db.execute("DELETE FROM extensions")
        db.execute("DELETE FROM clip_properties")
        db.execute("DELETE FROM sequence_properties")
        db.execute("DELETE FROM asset_properties")
        db.execute("DELETE FROM volume_keys")
        db.execute("DELETE FROM clips")
        db.execute("DELETE FROM sequences")
        db.execute("DELETE FROM assets")
        library_parent = Path(path).parent
        for a in library.assets.values():
            stored_path = a.path
            try:
                stored_path = Path(a.path).relative_to(library_parent).as_posix()
            except ValueError:
                pass
            project_root = library_parent.parent
            if library_parent.name == '03-edit' and (project_root/'project.json').is_file():
                try:
                    if Path(a.path).resolve().is_relative_to(project_root.resolve()):
                        stored_path = os.path.relpath(Path(a.path).resolve(), library_parent)
                except (ValueError, OSError):
                    pass
            db.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?,?)", (
                a.id, stored_path, a.duration_ms, a.width, a.height, a.fps_num, a.fps_den, int(a.has_audio),
            ))
            db.execute("INSERT INTO asset_properties VALUES (?,?)", (a.id, int(a.has_video)))
        for s in library.sequences:
            db.execute("INSERT INTO sequences VALUES (?,?,?,?,?,?)",
                       (s.id, s.name, s.fps_num, s.fps_den, s.width, s.height))
            db.execute("INSERT INTO sequence_properties VALUES (?,?,?)", (s.id, s.video_tracks, s.audio_tracks))
            db.executemany("INSERT INTO clips VALUES (?,?,?,?,?,?,?)", [
                (c.id, s.id, c.asset_id, c.start, c.source_in, c.duration, c.volume) for c in s.clips])
            db.executemany("INSERT INTO volume_keys VALUES (?,?,?)", [
                (c.id, frame, gain) for c in s.clips for frame, gain in c.volume_keys.items()])
            db.executemany("INSERT INTO clip_properties VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                (c.id, c.track, c.x, c.y, c.scale, c.opacity, int(c.muted), int(c.compressor),
                 c.threshold, c.ratio, c.attack, c.release, c.makeup) for c in s.clips])
        db.execute("INSERT OR REPLACE INTO settings VALUES ('active', ?)", (library.active.id,))
        for a in library.assets.values():
            extra = {'kind': a.kind, 'payload': a.payload, 'label': a.label}
            db.execute('INSERT INTO extensions VALUES (?,?,?)', ('asset', a.id, json.dumps(extra, ensure_ascii=False)))
        for seq in library.sequences:
            db.execute('INSERT INTO extensions VALUES (?,?,?)', ('sequence', seq.id, json.dumps({'kind': seq.kind})))
            for c in seq.clips:
                if c.title:
                    db.execute('INSERT INTO extensions VALUES (?,?,?)', ('clip', c.id, json.dumps(c.title, ensure_ascii=False)))
                if c.effects:
                    db.execute('INSERT INTO extensions VALUES (?,?,?)', ('clip_effects', c.id, json.dumps(c.effects, ensure_ascii=False)))
                if c.transform_keys:
                    db.execute('INSERT INTO extensions VALUES (?,?,?)', ('clip_transform_keys', c.id, json.dumps(c.transform_keys)))
        db.execute('INSERT INTO extensions VALUES (?,?,?)', ('events', 'all', json.dumps(
            {'events': [asdict(e) for e in library.events], 'active': library.active_event_id}, ensure_ascii=False)))
        db.execute('PRAGMA user_version='+str(6 if has_animation or version == 6 else 5 if has_effects or version == 5 else 4))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def load_library(path: str) -> Library:
    path = library_database(path)
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (1, 2, 3, 4, 5, 6):
            raise ValueError("Cut Raft のライブラリではないか、未対応のバージョンです。")
        library = Library(sequences=[])
        for row in db.execute("SELECT * FROM assets"):
            a = Asset(*row)
            if not Path(a.path).is_absolute():
                a.path = str((Path(path).resolve().parent / a.path).resolve())
            library.assets[a.id] = a
            if version >= 3:
                props = db.execute("SELECT has_video FROM asset_properties WHERE asset_id=?", (a.id,)).fetchone()
                if props:
                    a.has_video = bool(props[0])
        for row in db.execute("SELECT * FROM sequences ORDER BY rowid"):
            sequence = Sequence(*row)
            sequence.clips = [Clip(*c) for c in db.execute(
                "SELECT id,asset_id,start,source_in,duration,volume FROM clips WHERE sequence_id=?", (sequence.id,))]
            if version >= 2:
                for clip in sequence.clips:
                    clip.volume_keys = dict(db.execute("SELECT frame,gain FROM volume_keys WHERE clip_id=?", (clip.id,)))
            if version >= 3:
                props = db.execute("SELECT video_tracks,audio_tracks FROM sequence_properties WHERE sequence_id=?", (sequence.id,)).fetchone()
                if props:
                    sequence.video_tracks, sequence.audio_tracks = props
                names = ("track", "x", "y", "scale", "opacity", "muted", "compressor", "threshold", "ratio", "attack", "release", "makeup")
                for clip in sequence.clips:
                    props = db.execute("SELECT " + ",".join(names) + " FROM clip_properties WHERE clip_id=?", (clip.id,)).fetchone()
                    if props:
                        for name, value in zip(names, props):
                            setattr(clip, name, value)
            library.sequences.append(sequence)
        active = db.execute("SELECT value FROM settings WHERE key='active'").fetchone()
        library.active_id = active[0] if active else ""
        if version >= 4:
            for kind, key, payload in db.execute('SELECT kind,id,payload FROM extensions'):
                data = json.loads(payload)
                if kind == 'asset' and key in library.assets:
                    a = library.assets[key]
                    a.kind, a.payload, a.label = data['kind'], data['payload'], data['label']
                    if a.kind in ('title', 'compound'):
                        a.path = ''
                elif kind == 'sequence':
                    next(s for s in library.sequences if s.id == key).kind = data['kind']
                elif kind == 'clip':
                    next(c for s in library.sequences for c in s.clips if c.id == key).title = data
                elif kind == 'clip_effects':
                    next(c for s in library.sequences for c in s.clips if c.id == key).effects = data
                elif kind == 'clip_transform_keys':
                    next(c for s in library.sequences for c in s.clips if c.id == key).transform_keys = {
                        name: {int(f): v for f, v in keys.items()} for name, keys in data.items()}
                elif kind == 'events':
                    library.events = [Event(**e) for e in data['events']]
                    library.active_event_id = data['active']
        library.validate()
        return library
    finally:
        db.close()


def library_database(path, create=False):
    path = Path(path).expanduser().resolve()
    if path.is_dir():
        return str(path / 'library.sqlite3')
    return str(path)


def create_library_folder(parent, name, library):
    folder = Path(parent) / name
    folder.mkdir(parents=True, exist_ok=False)
    try:
        save_library(library, str(folder))
        return str(folder)
    except Exception:
        import shutil
        shutil.rmtree(folder)
        raise
