"""Windowless multisampled GPU frames with a transparent, supersampled edge."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor,QImage,QOffscreenSurface,QOpenGLContext
from PySide6.QtOpenGL import QOpenGLFramebufferObject,QOpenGLFramebufferObjectFormat
from .avatar_gl import GLViewport,surface_format

AVATAR_SIZE=1080
AVATAR_FILE='vtuber.mov'
AVATAR_VERSION=4


class _OfflineViewport(GLViewport):
    def __init__(self,model,context,surface,size):
        self.offline_context=context;self.offline_surface=surface
        super().__init__(model);self.resize(size,size)
        self.background=QColor(0,0,0,0)

    def context(self):return self.offline_context
    def makeCurrent(self):self.offline_context.makeCurrent(self.offline_surface)
    def doneCurrent(self):self.offline_context.doneCurrent()


class OfflineAvatar:
    def __init__(self,model,size=AVATAR_SIZE,samples=2):
        self.size=size;self.render_size=size*samples
        self.surface=QOffscreenSurface();self.surface.setFormat(surface_format());self.surface.create()
        self.context=QOpenGLContext();self.context.setFormat(surface_format())
        if not self.context.create() or not self.context.makeCurrent(self.surface):
            raise RuntimeError('オフスクリーンGPU描画を開始できません。')
        self.view=_OfflineViewport(model,self.context,self.surface,self.render_size)
        self.view.initializeGL()
        if not self.view.ready:raise RuntimeError(self.view.error)
        format=QOpenGLFramebufferObjectFormat()
        format.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil);format.setSamples(4)
        self.buffer=QOpenGLFramebufferObject(self.render_size,self.render_size,format)
        if not self.buffer.isValid():raise RuntimeError('透過描画バッファを作成できません。')

    def frame(self,pose,now,orbit=0):
        self.context.makeCurrent(self.surface);self.buffer.bind()
        self.view.gl.glViewport(0,0,self.render_size,self.render_size)
        self.view.pose,self.view.now,self.view.orbit=pose,now,orbit
        self.view.paintGL()
        image=self.buffer.toImage();self.buffer.release()
        return downsample(image,self.size)

    def close(self):
        import shiboken6
        self.context.makeCurrent(self.surface)
        self.buffer=None
        self.context.aboutToBeDestroyed.disconnect(self.view.release)
        self.view.release();self.view.close();shiboken6.delete(self.view);self.view=None
        self.context.doneCurrent();shiboken6.delete(self.context);shiboken6.delete(self.surface)


def downsample(image,size):
    # Filtering premultiplied color prevents a dark/green fringe at partial alpha.
    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied).scaled(
        size,size,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation
        ).convertToFormat(QImage.Format.Format_RGBA8888)
