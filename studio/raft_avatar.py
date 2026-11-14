"""Embedded view for the face-following FBX avatar supplied with the project."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mimetypes
import threading
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineWidgets import QWebEngineView


ASSETS = Path(__file__).parent / 'assets' / 'avatar'


class AvatarAssetServer:
    """Serve only the packaged avatar files from a loopback origin."""
    _server = None
    _thread = None

    @classmethod
    def start(cls):
        if cls._server:
            return f'http://127.0.0.1:{cls._server.server_port}/avatar/index.html'
        root = ASSETS.resolve()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.headers.get('Host') != f'127.0.0.1:{AvatarAssetServer._server.server_port}':
                    self.send_error(403); return
                route = unquote(urlsplit(self.path).path)
                if not route.startswith('/avatar/'):
                    self.send_error(404); return
                file = (root / route[len('/avatar/'):]).resolve()
                if not file.is_relative_to(root) or not file.is_file():
                    self.send_error(404); return
                content = file.read_bytes()
                mime = {'.mjs': 'text/javascript', '.js': 'text/javascript'}.get(file.suffix, mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
                self.end_headers()
                try: self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError): pass

        cls._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls._server.daemon_threads = True
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        return f'http://127.0.0.1:{cls._server.server_port}/avatar/index.html'

    @classmethod
    def stop(cls):
        if cls._server:
            cls._server.shutdown(); cls._server.server_close(); cls._server = None
            cls._thread.join(timeout=2); cls._thread = None


class RaftAvatarView(QWebEngineView):
    """Transparent Three.js viewport driven by the studio's Pose values."""
    backendChanged = Signal(bool, str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pose = {}
        self._now = 0
        self.orbit = 0
        self.background = 'transparent'
        self.software = False
        self._loaded = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet('background: transparent; border: none;')
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.loadFinished.connect(self._on_loaded)
        self.setUrl(QUrl(AvatarAssetServer.start()))

    def _on_loaded(self, ok):
        self._loaded = bool(ok)
        if ok:
            self.backendChanged.emit(False, 'Raft 3Dモデル')
            self.update_pose(self._pose, self._now)
            self.set_background(self.background)
        else:
            self.backendChanged.emit(False, '3Dモデルを読み込めません')

    def update_pose(self, pose, now=0):
        self._pose = pose.__dict__.copy() if hasattr(pose, '__dict__') else dict(pose)
        self._now = now
        if self._loaded:
            import json
            payload = json.dumps(self._pose, separators=(',', ':'), allow_nan=False)
            self.page().runJavaScript(f'window.updateAvatarPose && window.updateAvatarPose({payload}); window.setAvatarOrbit && window.setAvatarOrbit({float(self.orbit)});')

    def set_background(self, color):
        self.background = str(color)
        if self._loaded:
            import json
            self.page().runJavaScript(f'window.setAvatarBackground && window.setAvatarBackground({json.dumps(self.background)});')

    def set_software(self, _value):
        # WebEngine handles the graphics fallback; the legacy checkbox remains
        # harmless for existing saved UI state.
        pass

    def release(self):
        self._loaded = False
        self.setUrl(QUrl('about:blank'))
