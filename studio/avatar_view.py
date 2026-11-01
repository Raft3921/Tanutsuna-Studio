from PySide6.QtCore import Qt, QByteArray, QTimer, Signal
from PySide6.QtGui import QColor, QMatrix4x4, QPainter, QPolygonF, QVector3D, QImage
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QWidget, QVBoxLayout, QApplication
import numpy as np
from .avatar_model import AvatarModel, rotation
from .tracking import Pose


class SoftwareViewport(QWidget):
    """Compatibility renderer: depth-sorted actual mesh triangles, never a flat sprite."""
    def __init__(self,model,parent=None):
        super().__init__(parent)
        self.model=model;self.pose=Pose();self.now=0;self.background=QColor('#4b535c');self.orbit=0
        self.setMinimumSize(280,280)

    def update_pose(self,pose,now):
        self.pose,self.now=pose,now;self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(),self.background)
        scale=min(self.width(),self.height())/2.90
        view=rotation(y=self.orbit)
        triangles=[]
        light=np.array([-.45,.65,.8]);light/=np.linalg.norm(light)
        for mesh,vertices,normals in self.model.posed_meshes(self.pose,self.now):
            vertices=(vertices-np.array([0,1.18,0]))@view.T+np.array([0,1.18,0])
            normals=normals@view.T
            tri=vertices.reshape(-1,3,3);normal=normals.reshape(-1,3,3).mean(axis=1)
            visible=normal[:,2]>.015
            tri=tri[visible];normal=normal[visible]
            shade=np.where(normal@light<.12,.78,np.where(normal@light<.42,.90,1.0))
            if mesh.get("outline") or mesh["name"].startswith(("mouth_lip","mouth_curve","mouth_cavity","muzzle","ear_fold","goggle_frame")):shade=np.ones(len(normal))
            base=mesh.get('triangle_colors')
            base=base[visible] if base is not None else np.asarray(mesh['color'])[None,:]
            colors=np.clip(base*shade[:,None]*255,0,255).astype(int)
            if mesh['name']=='goggle_lens':
                toward_camera=np.array([0.,1.22,5.35])-tri.mean(axis=1)
                toward_camera/=np.maximum(np.linalg.norm(toward_camera,axis=1,keepdims=True),.0001)
                half=light+toward_camera;half/=np.maximum(np.linalg.norm(half,axis=1,keepdims=True),.0001)
                shine=np.maximum(normal@half,0.)
                spec=(.68*shine**72+.12*shine**12)*np.maximum(np.sum(normal*toward_camera,axis=1),0.)**.55
                colors=np.clip(colors+spec[:,None]*np.array([.68,.82,.96])[None,:]*255,0,255).astype(int)
            for points,c in zip(tri,colors):
                xy=[QPointF(self.width()/2+p[0]*scale,self.height()*.53-(p[1]-1.18)*scale) for p in points]
                triangles.append((0 if mesh.get('outline') else 1,points[:,2].mean(),xy,tuple(c)))
        # Close coplanar mesh seams by drawing with the same-color pen.
        for layer,depth,points,color in sorted(triangles,key=lambda v:(v[0],v[1])):
            c=QColor(*map(int,color));painter.setPen(c);painter.setBrush(c);painter.drawPolygon(QPolygonF(points))
        painter.end()


from .avatar_gl import GLViewport as GPUViewport


class AvatarViewport(QWidget):
    backendChanged=Signal(bool,str)
    def __init__(self,parent=None,software=False):
        super().__init__(parent)
        self.model=AvatarModel();self.pose=Pose();self.now=0;self.backend=None;self._switching=False;self.last_render_error='';self._released=False
        self.background=QColor('#4b535c');self.orbit=0
        self.software=software or QApplication.platformName() in ('offscreen','minimal')
        self.layout_=QVBoxLayout(self);self.layout_.setContentsMargins(0,0,0,0)
        self.setMinimumSize(280,280)
        # Install the child before the containing dialog is shown. Lazy creation in showEvent
        # can re-enter showEvent while Qt recreates the top-level native surface.
        self.set_software(self.software)

    def showEvent(self,event):
        super().showEvent(event)
        if self.backend is None and not self._switching:self.set_software(self.software)

    def set_software(self,value):
        if self._switching or self._released:return
        self._switching=True
        try:
            self.software=bool(value)
            old=self.backend
            if old:
                if hasattr(old,'release'):old.release()
                self.layout_.removeWidget(old);old.hide();old.deleteLater()
            backend=SoftwareViewport(self.model,self) if value else GPUViewport(self.model,self)
            self.backend=backend
            if not value:
                backend.renderFailed.connect(lambda reason,b=backend:QTimer.singleShot(0,lambda:self._fallback(b,reason)))
            self.layout_.addWidget(backend);backend.show()
            self.update_pose(self.pose,self.now)
            self.backendChanged.emit(bool(value),'互換表示' if value else 'GPU描画を準備中')
            if not value:QTimer.singleShot(1000,lambda:self._verify_backend(backend,0))
        finally:self._switching=False

    def _fallback(self,backend,reason):
        if self._released or backend is not self.backend:return
        self.last_render_error=reason
        self.set_software(True)
        self.backendChanged.emit(True,'GPU描画を開始できないため互換表示に切り替えました。')

    def _verify_backend(self,backend,attempt):
        if self._released or backend is not self.backend or self.software:return
        if not self.isVisible():
            QTimer.singleShot(1000,lambda:self._verify_backend(backend,attempt));return
        if getattr(backend,'error',''):return self._fallback(backend,backend.error)
        if not backend.isValid() or not backend.ready or backend.frame_count==0:
            if attempt<2:
                backend.update();QTimer.singleShot(700,lambda:self._verify_backend(backend,attempt+1));return
            return self._fallback(backend,'OpenGLの初期描画が完了しませんでした。')
        # Check the actual framebuffer once; a linked shader alone does not prove visibility.
        image=backend.grabFramebuffer().convertToFormat(QImage.Format.Format_RGBA8888)
        if image.isNull():return self._fallback(backend,'3Dの描画画像を取得できませんでした。')
        pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine()//4,4)[:,:image.width(),:3]
        bg=np.array([self.background.red(),self.background.green(),self.background.blue()])
        if np.count_nonzero(np.max(np.abs(pixels.astype(int)-bg),axis=2)>12)<64:
            return self._fallback(backend,'3Dモデルが描画されませんでした。')
        self.backendChanged.emit(False,'GPU描画')

    def update_pose(self,pose,now):
        self.pose,self.now=pose,now
        if self.backend:
            self.backend.background=self.background;self.backend.orbit=self.orbit
            self.backend.update_pose(pose,now)

    def release(self):
        self._released=True
        if self.backend and hasattr(self.backend,'release'):self.backend.release()
