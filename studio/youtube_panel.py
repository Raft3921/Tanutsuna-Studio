"""In-app YouTube live chat and lightweight viewer-count polling."""
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import json
import re

from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QStackedWidget, QTextBrowser, QCheckBox)


VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')


def video_id_from_url(value):
    value = value.strip()
    if VIDEO_ID.fullmatch(value):
        return value
    parsed = urlsplit(value if '://' in value else 'https://' + value)
    host = parsed.hostname or ''
    if host.endswith('youtu.be'):
        candidate = parsed.path.strip('/').split('/')[0]
    elif host.endswith('youtube.com') or host.endswith('youtube-nocookie.com'):
        query = parse_qs(parsed.query)
        candidate = query.get('v', [''])[0]
        if not candidate:
            pieces = [part for part in parsed.path.split('/') if part]
            candidate = pieces[1] if len(pieces) > 1 and pieces[0] in ('live', 'shorts', 'embed') else ''
    else:
        candidate = ''
    return candidate if VIDEO_ID.fullmatch(candidate) else ''


class ViewerCountWorker(QThread):
    completed = Signal(str, str)

    def __init__(self, video_id, api_key, parent=None):
        super().__init__(parent)
        self.video_id = video_id
        self.api_key = api_key

    def run(self):
        query = urlencode({'part': 'liveStreamingDetails', 'id': self.video_id, 'key': self.api_key})
        request = Request('https://www.googleapis.com/youtube/v3/videos?' + query,
                          headers={'Accept': 'application/json', 'User-Agent': 'Tanutsuna-Studio'})
        try:
            with urlopen(request, timeout=7) as response:
                data = json.loads(response.read(1024 * 1024).decode('utf-8'))
            items = data.get('items') or []
            details = items[0].get('liveStreamingDetails', {}) if items else {}
            count = details.get('concurrentViewers')
            if count is None:
                result = '視聴者数: 非公開または取得できません'
            else:
                result = f'視聴者数: {int(count):,} 人'
        except Exception:
            result = '視聴者数: APIキーまたは配信URLを確認してください'
        self.completed.emit(self.video_id, result)


class YouTubePanel(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.setObjectName('YouTubePanel')
        self.setStyleSheet('QWidget#YouTubePanel{background:#e8f6f8;border:1px solid #c6e1e7;border-radius:12px} QLabel#LiveViewerCount{font-size:19px;font-weight:700;color:#126d82}')
        self.workspace = workspace
        self.video_id = ''
        self.chat_view = None
        self.worker = None
        saved = workspace.data.get('live', {})
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        title = QLabel('YouTube LIVE')
        title.setObjectName('PageTitle')
        layout.addWidget(title)
        self.viewer_count = QLabel('視聴者数: 配信URLを接続')
        self.viewer_count.setObjectName('LiveViewerCount')
        layout.addWidget(self.viewer_count)
        row = QHBoxLayout()
        self.video_url = QLineEdit(saved.get('video_url', ''))
        self.video_url.setPlaceholderText('YouTubeライブ配信URL')
        self.video_url.editingFinished.connect(self.save_video_url)
        self.connect_button = QPushButton('接続')
        self.connect_button.clicked.connect(self.connect_chat)
        row.addWidget(self.video_url, 1)
        row.addWidget(self.connect_button)
        layout.addLayout(row)
        self.api_key = QLineEdit()
        self.api_key.setPlaceholderText('視聴者数用 YouTube Data APIキー')
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.textChanged.connect(self.update_viewer_count)
        layout.addWidget(self.api_key)
        self.remember_api_key = QCheckBox('APIキーをこのPCに保存')
        self.secret_api_loaded = False
        try:
            from .credentials import youtube_api_key
            saved_api_key = youtube_api_key('get')
            if saved_api_key:
                self.api_key.setText(saved_api_key);self.remember_api_key.setChecked(True);self.secret_api_loaded=True
        except OSError:
            pass
        layout.addWidget(self.remember_api_key)
        self.api_key.textEdited.connect(self.save_api_key)
        self.remember_api_key.toggled.connect(self.save_api_key)
        self.chat_stack = QStackedWidget()
        self.placeholder = QTextBrowser()
        self.placeholder.setOpenExternalLinks(False)
        self.placeholder.setHtml('<div style="color:#72919d;text-align:center;margin-top:50px">YouTubeの配信URLを入力して<br>チャットに接続します</div>')
        self.chat_stack.addWidget(self.placeholder)
        layout.addWidget(self.chat_stack, 1)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(45000)
        self.refresh_timer.timeout.connect(self.update_viewer_count)
        self.refresh_timer.start()
        if saved.get('video_url'):
            QTimer.singleShot(150,self.connect_chat)

    def save_video_url(self):
        live=self.workspace.data.setdefault('live',{})
        live['video_url']=self.video_url.text().strip()
        self.workspace.save()

    def save_api_key(self,*_):
        from .credentials import youtube_api_key
        value=self.api_key.text().strip()
        try:
            if self.remember_api_key.isChecked() and value:
                youtube_api_key('set',value);self.secret_api_loaded=True
            elif self.remember_api_key.isChecked() and self.secret_api_loaded:
                youtube_api_key('delete');self.secret_api_loaded=False
            elif not self.remember_api_key.isChecked() and self.secret_api_loaded:
                youtube_api_key('delete');self.secret_api_loaded=False
        except OSError:
            self.viewer_count.setText('APIキーを安全に保存できませんでした。OSのキーチェーン設定を確認してください。')
            self.remember_api_key.blockSignals(True);self.remember_api_key.setChecked(False);self.remember_api_key.blockSignals(False)

    def connect_chat(self):
        video_id = video_id_from_url(self.video_url.text())
        if not video_id:
            self.viewer_count.setText('YouTube配信URLを確認してください')
            return
        self.video_id = video_id
        try:
            from .credentials import youtube_api_key
            if self.remember_api_key.isChecked() and self.api_key.text().strip():
                youtube_api_key('set',self.api_key.text().strip());self.secret_api_loaded=True
            elif self.secret_api_loaded:
                youtube_api_key('delete');self.secret_api_loaded=False
        except OSError:
            self.viewer_count.setText('APIキーを安全に保存できないため、今回は保存せずに使います')
            self.remember_api_key.setChecked(False);self.secret_api_loaded=False
        live = self.workspace.data.setdefault('live', {})
        live['video_url'] = self.video_url.text().strip()
        self.workspace.save()
        if self.chat_view is None:
            try:
                from PySide6.QtWebEngineWidgets import QWebEngineView
                self.chat_view = QWebEngineView(self.chat_stack)
                self.chat_stack.addWidget(self.chat_view)
            except Exception:
                self.viewer_count.setText('アプリ内チャットを準備できません。アプリを更新してください。')
                return
        self.chat_view.setUrl(QUrl('https://www.youtube.com/live_chat?' + urlencode({'is_popout': '1', 'v': video_id})))
        self.chat_stack.setCurrentWidget(self.chat_view)
        self.update_viewer_count()

    def update_viewer_count(self,*_):
        if not self.video_id:
            return
        api_key = self.api_key.text().strip()
        if not api_key:
            self.viewer_count.setText('視聴者数: APIキーを設定してください')
            return
        if self.worker is not None and self.worker.isRunning():
            return
        self.worker = ViewerCountWorker(self.video_id, api_key, self)
        self.worker.completed.connect(self.viewer_count_updated)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def viewer_count_updated(self,video_id,result):
        if video_id==self.video_id:self.viewer_count.setText(result)
        self.worker=None
