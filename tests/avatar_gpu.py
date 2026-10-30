from pathlib import Path
import sys,json,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from studio.avatar_gl import configure_opengl
configure_opengl()
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QPainter,QColor
from studio.avatar_view import AvatarViewport
from studio.tracking import Pose
app=QApplication([]);v=AvatarViewport();v.resize(540,540);v.show()
for _ in range(20):app.processEvents();time.sleep(.02)
canvas=QImage(1620,1080,QImage.Format.Format_RGB32);canvas.fill(QColor('#4b535c'));p=QPainter(canvas)
for i,(name,pose,orbit) in enumerate([('front',Pose(jawOpen=.6),0),('down',Pose(pitch=40,jawOpen=1),0),('up',Pose(pitch=-40),0),('turn',Pose(yaw=65,roll=30),0),('side',Pose(),90),('back',Pose(),180)]):
 v.orbit=orbit;v.update_pose(pose,0)
 app.processEvents();frame=v.backend.grabFramebuffer();small=frame.scaled(540,540);small.setDevicePixelRatio(1);p.drawImage((i%3)*540,(i//3)*540,small)
 frame.save(str(ROOT/('build/v5-'+name+'.png')))
p.end();canvas.save(str(ROOT/'docs/tanuki-v5-poses.png'))
red=[]
for jaw in [0,1]:
 v.orbit=0;v.update_pose(Pose(jawOpen=jaw),0);app.processEvents()
 im=v.backend.grabFramebuffer().convertToFormat(QImage.Format.Format_RGBA8888)
 rgb=np.frombuffer(im.bits(),dtype=np.uint8).reshape(im.height(),im.bytesPerLine()//4,4)[:,:im.width(),:3].astype(int)
 red.append(int(np.count_nonzero((rgb[:,:,0]>90)&(rgb[:,:,0]>rgb[:,:,1]*1.8)&(rgb[:,:,0]>rgb[:,:,2]*1.5))))
assert red[1]>red[0]+200,red
v.backend.makeCurrent();error=v.backend.gl.glGetError();v.backend.doneCurrent()
assert error==0,error
print(json.dumps({'jawRedPixels':red,'glError':error,'triangles':sum(len(m['positions'])//3 for m in v.model.meshes)}))
v.release();v.close()
