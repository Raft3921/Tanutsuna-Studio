"""On-demand, in-app camera tracker; no external browser or network service."""
import os
# Keep local tracking timers responsive while the settings window is hidden.
for flag in ('--disable-background-timer-throttling','--disable-renderer-backgrounding'):
    if flag not in os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS',''):
        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS','')+' '+flag).strip()
from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEnginePermission
from PySide6.QtWebEngineWidgets import QWebEngineView


class CameraWindow(QDialog):
    stopped=Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle('カメラ追跡 — たぬつな')
        self.resize(700,650)
        self.view=QWebEngineView(self)
        self.origin=''
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.addWidget(self.view)
        self.view.page().permissionRequested.connect(self.permission)
        self.view.page().newWindowRequested.connect(lambda request:None)

    def permission(self,request):
        origin=request.origin().toString().rstrip('/')
        if origin==self.origin and request.permissionType()==QWebEnginePermission.PermissionType.MediaVideoCapture:
            request.grant()
        else:request.deny()

    def open_tracker(self,url,automatic=False):
        from PySide6.QtCore import QSettings
        from urllib.parse import quote
        label=QSettings('TanutsunaStudio','Avatar').value('cameraLabel','')
        if label:url+='&camera='+quote(label,safe='')
        self.origin=url.split('/#')[0]
        if automatic:url+='&autostart=1'
        current=self.view.url().toString()
        # Opening settings must not reload and tear down an automatic live stream.
        from urllib.parse import urlsplit,parse_qs
        from .camera_choice import camera_key
        current_parts,target_parts=urlsplit(current),urlsplit(url)
        current_query,target_query=parse_qs(current_parts.fragment),parse_qs(target_parts.fragment)
        same_tracker=(not automatic and current_parts.netloc==target_parts.netloc and current_query.get('token')==target_query.get('token') and camera_key(current_query.get('camera',[''])[0])==camera_key(label))
        if not same_tracker and current!=url:self.view.setUrl(QUrl(url))
        if automatic:
            self.view.page().setVisible(True)
        else:
            self.show();self.raise_();self.activateWindow()

    def stop(self):
        # Navigating away tears down getUserMedia tracks, including pending startup.
        self.view.setUrl(QUrl('about:blank'));self.hide();self.origin=''

    def closeEvent(self,event):
        self.stop();self.stopped.emit();event.accept()
