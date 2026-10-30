"""Atomic local workspace snapshots, including projects without a chosen save path."""
from pathlib import Path
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from PySide6.QtCore import QStandardPaths
from .storage import load_library, save_library


class SessionStore:
    def __init__(self, root=None):
        root = root or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / 'workspace.cutraft'

    def write(self, library, state):
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.workspace-', suffix='.cutraft', dir=self.root)
        os.close(fd)
        try:
            save_library(library, temporary)
            with closing(sqlite3.connect(temporary)) as db:
                db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)',
                           ('workspace_session', json.dumps(state, ensure_ascii=False)))
                db.commit()
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def read(self):
        if not self.path.is_file():
            return None
        library = load_library(str(self.path))
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', ('workspace_session',)).fetchone()
        return library, json.loads(row[0]) if row else {}
