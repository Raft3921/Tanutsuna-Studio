"""Native Qt OpenGL renderer: textured, banded cel shading and real inverted-hull outlines."""
import numpy as np
import sys
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QColor,QMatrix4x4,QVector3D,QImage,QSurfaceFormat)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import (QOpenGLFunctions_3_3_Core,QOpenGLShader,QOpenGLShaderProgram,
 QOpenGLBuffer,QOpenGLVertexArrayObject,QOpenGLTexture)
from .avatar_model import rotation
from .tracking import Pose

def surface_format():
 fmt=QSurfaceFormat()
 # macOS exposes 3.2/4.1 core profiles rather than a native 3.3 profile.
 fmt.setVersion(4,1) if sys.platform=='darwin' else fmt.setVersion(3,3)
 fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
 fmt.setDepthBufferSize(24);fmt.setStencilBufferSize(8);fmt.setAlphaBufferSize(8);fmt.setSamples(4)
 return fmt

def configure_opengl():
 from PySide6.QtWidgets import QApplication
 if QApplication.instance() is None:
  QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts,True)
  QSurfaceFormat.setDefaultFormat(surface_format())

VERTEX='''#version 330 core
layout(location=0) in vec3 position;
layout(location=1) in vec3 normal;
layout(location=2) in vec2 uv;
layout(location=3) in vec3 jawDelta;
uniform float jawOpen;
uniform int headSurface;
uniform mat4 model;
uniform mat4 viewProjection;
out vec3 worldNormal;
out vec3 worldPosition;
out vec2 texCoord;
void main(){
 vec3 p=position+jawDelta*jawOpen;
 vec3 n=normal;
 if(headSurface==1)n=normalize(vec3(p.x/(1.10*1.10),(p.y-1.18)/(1.05*1.05),p.z/(0.79*0.79)));
 vec4 world=model*vec4(p,1.0);
 gl_Position=viewProjection*world;worldNormal=mat3(model)*n;worldPosition=world.xyz;texCoord=uv;
}
'''
FRAGMENT='''#version 330 core
in vec3 worldNormal;
in vec3 worldPosition;
in vec2 texCoord;
uniform vec3 baseColor;
uniform sampler2D baseTexture;
uniform int textured;
uniform int ink;
uniform int glossy;
out vec4 fragColor;
void main(){
 vec3 color=textured==1?texture(baseTexture,texCoord).rgb:baseColor;
 vec3 N=normalize(worldNormal),L=normalize(vec3(-0.45,0.65,0.8));
 float light=dot(N,L);
 float band=ink==1?1.0:(light<0.12?0.78:(light<0.42?0.90:1.0));
 if(glossy==1){
  vec3 V=normalize(vec3(0.0,1.22,5.35)-worldPosition),H=normalize(L+V);
  float ndh=max(dot(N,H),0.0),facing=pow(max(dot(N,V),0.0),0.55);
  color+=vec3(0.68,0.82,0.96)*(0.68*pow(ndh,72.0)+0.12*pow(ndh,12.0))*facing;
 }
 fragColor=vec4(color*band,1.0);
}
'''

class GLViewport(QOpenGLWidget):
 renderFailed=Signal(str)
 def __init__(self,model,parent=None):
  super().__init__(parent)
  self.setFormat(surface_format());self.setMinimumSize(280,280)
  self.model=model;self.pose=Pose();self.now=0;self.background=QColor('#4b535c');self.orbit=0;self.parts=[];self.ready=False;self.error='';self.frame_count=0;self.white_texture=None
 def initializeGL(self):
  try:
   self.gl=QOpenGLFunctions_3_3_Core()
   if not self.gl.initializeOpenGLFunctions():raise RuntimeError('OpenGL 3.3の描画機能を初期化できません。')
   self.program=QOpenGLShaderProgram(self)
   if not self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,VERTEX):raise RuntimeError(self.program.log())
   if not self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,FRAGMENT):raise RuntimeError(self.program.log())
   if not self.program.link():raise RuntimeError(self.program.log())
   self.program.bind()
   white=QImage(1,1,QImage.Format.Format_RGBA8888);white.fill(QColor('white'))
   self.white_texture=QOpenGLTexture(white)
   for mesh in self.model.meshes:
    delta=mesh['morphs'].get('jawOpen',np.zeros_like(mesh['positions']))
    array=np.concatenate([mesh['positions'],mesh['normals'],mesh['uv'],delta],axis=1).astype(np.float32)
    vao=QOpenGLVertexArrayObject(self);vao.create();vao.bind()
    buffer=QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer);buffer.create();buffer.bind();buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw);buffer.allocate(array.tobytes(),array.nbytes)
    for index,offset,size in [(0,0,3),(1,12,3),(2,24,2),(3,32,3)]:
     self.program.enableAttributeArray(index);self.program.setAttributeBuffer(index,0x1406,offset,size,44)
    texture=None
    if mesh.get('texture'):
     image=QImage(str(Path(__file__).parent/'assets/tanuki'/mesh['texture']))
     if image.isNull():raise RuntimeError('モデルのテクスチャがありません。')
     texture=QOpenGLTexture(image.mirrored());texture.setMinificationFilter(QOpenGLTexture.Filter.LinearMipMapLinear);texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear);texture.setWrapMode(QOpenGLTexture.WrapMode.Repeat)
    vao.release();buffer.release();self.parts.append((mesh,array,vao,buffer,texture))
   self.program.release();self.ready=True
   self.context().aboutToBeDestroyed.connect(self.release)
  except Exception as exc:
   self.error=str(exc);self.ready=False;self.renderFailed.emit(self.error)
 def update_pose(self,pose,now):self.pose,self.now=pose,now;self.update()
 def paintGL(self):
  if not hasattr(self,'gl'):return
  gl=self.gl;gl.glStencilMask(0xff);gl.glClearStencil(0);gl.glClearColor(self.background.redF(),self.background.greenF(),self.background.blueF(),self.background.alphaF());gl.glClear(0x4000|0x0100|0x0400)
  if not self.ready:return
  gl.glEnable(0x0B71);gl.glEnable(0x0B44);gl.glCullFace(0x0405);gl.glFrontFace(0x0901)
  self.program.bind()
  view=QMatrix4x4();view.lookAt(QVector3D(0,1.22,5.35),QVector3D(0,1.18,0),QVector3D(0,1,0))
  # Leave room for the full head and its camera-tracked translation. At the
  # previous 32° FOV, the allowed headX/headY movement could push ears or the
  # forehead outside the transparent frame.
  projection=QMatrix4x4();projection.perspective(44,max(1,self.width())/max(1,self.height()),.05,100)
  self.program.setUniformValue('viewProjection',projection*view)
  transforms=self.model.transforms(self.pose,self.now)
  orbit=np.eye(4);orbit[:3,:3]=rotation(y=self.orbit);pivot=np.array([0,1.18,0]);orbit[:3,3]=pivot-orbit[:3,:3]@pivot
  # Mark visible colored geometry, then draw hulls only outside that silhouette.
  # A complete head hull cannot bleed through the ear bowl or require cut-out holes.
  gl.glEnable(0x0B90);gl.glStencilFunc(0x0207,1,0xff);gl.glStencilOp(0x1E00,0x1E00,0x1E01)
  gl.glUniform1f(self.program.uniformLocation('jawOpen'),float(self.pose.jawOpen))
  for mesh,array,vao,buffer,texture in sorted(self.parts,key=lambda part:bool(part[0].get('outline'))):
   if mesh.get('outline'):
    gl.glStencilMask(0);gl.glStencilFunc(0x0205,1,0xff)
   self.program.setUniformValue('headSurface',1 if mesh['name']=='round_tanuki' else 0)
   m=orbit@transforms[mesh['bone']];self.program.setUniformValue('model',QMatrix4x4(*map(float,m.ravel())))
   self.program.setUniformValue('baseColor',QVector3D(*mesh['color']))
   self.program.setUniformValue('textured',1 if texture else 0)
   self.program.setUniformValue('ink',1 if mesh.get('outline') or mesh['name'].startswith(('mouth_lip','mouth_curve','mouth_tip','mouth_cavity','muzzle','ear_fold','goggle_frame')) else 0)
   self.program.setUniformValue('glossy',1 if mesh['name']=='goggle_lens' else 0)
   vao.bind()
   if mesh.get('procedural_indices') is not None or mesh['name'].startswith(('mouth_curve','mouth_tip')):
    positions=self.model.vertices(mesh,self.pose)
    array[:,0:3]=positions
    array[:,3:6]=self.model.normals(mesh,positions)
    array[:,8:11]=0
    buffer.bind();buffer.write(0,array.tobytes(),array.nbytes);buffer.release()
   bound=texture or self.white_texture
   bound.bind(0);self.program.setUniformValue('baseTexture',0)
   gl.glDrawArrays(0x0004,0,len(array))
   bound.release()
   vao.release()
  self.program.release()
  gl.glDisable(0x0B90);gl.glStencilMask(0xff)
  self.frame_count+=1
 def release(self):
  if not self.parts:return
  self.makeCurrent()
  for mesh,array,vao,buffer,texture in self.parts:
   if texture:texture.destroy()
   buffer.destroy();vao.destroy()
  if self.white_texture:self.white_texture.destroy();self.white_texture=None
  self.parts=[];self.ready=False;self.doneCurrent()
