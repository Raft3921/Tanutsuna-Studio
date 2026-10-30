"""Local-only camera bridge and bounded, time-smoothed face controls."""
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import math
import mimetypes
import secrets
import threading
import time
from urllib.parse import unquote, urlsplit

# PyInstaller's macOS bundle exposes assets through directory symlinks.
# Keep both the containment check and requested files in the same canonical tree.
ASSETS = (Path(__file__).parent / 'assets').resolve()


@dataclass
class Pose:
    yaw: float = 0
    pitch: float = 0
    roll: float = 0
    headX: float = 0
    headY: float = 0
    headZ: float = 0
    jawOpen: float = .60
    eyeBlinkLeft: float = 0
    eyeBlinkRight: float = 0
    eyeWideLeft: float = 0
    eyeWideRight: float = 0
    mouthSmile: float = 0
    browUp: float = 0
    mouthA: float = 0
    mouthI: float = 0
    mouthU: float = 0
    mouthE: float = 0
    mouthO: float = 0
    mouthWidth: float = 1
    mouthHeight: float = 1
    mouthStroke: float = 1.15


LIMITS = {'yaw': (-65,65), 'pitch': (-40,40), 'roll': (-40,40), 'headX':(-1,1), 'headY':(-1,1), 'headZ':(-1,1), 'mouthWidth':(.55,1.9), 'mouthHeight':(.15,1.3), 'mouthStroke':(.8,1.8)}


def parse_pose(data):
    if not isinstance(data, dict):
        raise ValueError('Tracking data must be an object')
    values = {}
    for name in Pose.__dataclass_fields__:
        value = data.get(name, Pose.__dataclass_fields__[name].default)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Invalid tracking value: ' + name)
        low, high = LIMITS.get(name, (0,1))
        values[name] = min(high, max(low, float(value)))
    return Pose(**values)


class TrackingState:
    def __init__(self):
        self.lock = threading.Lock()
        self.pose = Pose()
        self.last_seen = 0
        self.message = '未接続'
        self.camera_label = ''
        self.microphone_label = ''
        self.voice_gain = 2.5
        self.detected = False
        self.generation = 0

    def update(self, data):
        if not isinstance(data, dict) or type(data.get('detected')) is not bool:
            raise ValueError('Face detection status is required')
        pose = parse_pose(data.get('pose', {}))
        with self.lock:
            self.detected = data['detected']
            if self.detected:
                self.pose = pose
            self.last_seen = time.monotonic()
            self.message = str(data.get('status', '追跡中' if self.detected else '顔を探しています'))[:160]
            label=data.get('cameraLabel','')
            if isinstance(label,str) and label:self.camera_label=label[:200]
            microphone=data.get('microphoneLabel','')
            if isinstance(microphone,str) and microphone:self.microphone_label=microphone[:200]
            voice_gain=data.get('voiceGain',self.voice_gain)
            if type(voice_gain) in (int,float) and math.isfinite(voice_gain):self.voice_gain=min(5,max(.5,float(voice_gain)))
            self.generation += 1

    def snapshot(self):
        with self.lock:
            return self.pose, self.detected and time.monotonic()-self.last_seen < .8, self.message, self.last_seen


class TrackerServer:
    def __init__(self, state=None):
        self.state = state or TrackingState()
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.thread = None

    def start(self):
        if self.server:
            return self.url
        for name in ('camera/index.html', 'camera/camera.js', 'camera/camera.css',
                     'tracker/face_landmarker.task', 'tracker/vision_bundle.mjs'):
            if not (ASSETS / name).is_file():
                raise ValueError('カメラ追跡のファイルが見つかりません：' + name)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body=b'', mime='text/plain; charset=utf-8'):
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def valid_host(self):
                return self.headers.get('Host') == f'127.0.0.1:{owner.server.server_port}'

            def do_GET(self):
                if not self.valid_host():
                    return self.reply(403)
                route = unquote(urlsplit(self.path).path)
                if route == '/':
                    file = (ASSETS / 'camera/index.html').resolve()
                elif route.startswith('/camera/') or route.startswith('/tracker/'):
                    file = (ASSETS / route.lstrip('/')).resolve()
                else:
                    return self.reply(404)
                if not file.is_relative_to(ASSETS.resolve()) or not file.is_file():
                    return self.reply(404)
                mime = {'.mjs': 'text/javascript', '.js': 'text/javascript', '.wasm': 'application/wasm', '.task': 'application/octet-stream'}.get(file.suffix, mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                try:
                    self.reply(200, file.read_bytes(), mime)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self):
                if not self.valid_host() or urlsplit(self.path).path != '/pose':
                    return self.reply(403)
                expected = f'http://127.0.0.1:{owner.server.server_port}'
                if self.headers.get('Origin') != expected or not secrets.compare_digest(self.headers.get('X-Tracking-Token',''), owner.token):
                    return self.reply(403)
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 16384:
                        return self.reply(413)
                    if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        return self.reply(415)
                    owner.state.update(json.loads(self.rfile.read(length)))
                    self.reply(204)
                except (ValueError, TypeError):
                    self.reply(400)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.url

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}/#token={self.token}'

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.thread.join(timeout=2)


def smooth_pose(previous, target, dt, speed=14):
    alpha = 1-math.exp(-max(0,min(dt,.15))*speed)
    return Pose(**{k: getattr(previous,k)+(getattr(target,k)-getattr(previous,k))*alpha for k in Pose.__dataclass_fields__})
