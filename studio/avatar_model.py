"""The same authored 3D geometry drives native GPU rendering and compatibility previews."""
from pathlib import Path
import gzip
import json
import math
import numpy as np
from .tracking import Pose

MODEL_PATH = Path(__file__).parent / 'assets/tanuki/tanuki.mesh.json.gz'


def rotation(x=0,y=0,z=0):
    x,y,z=np.radians([x,y,z]);cx,sx=math.cos(x),math.sin(x);cy,sy=math.cos(y),math.sin(y);cz,sz=math.cos(z),math.sin(z)
    rx=np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]])
    ry=np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])
    rz=np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]])
    return rz@ry@rx


class AvatarModel:
    def __init__(self):
        with gzip.open(MODEL_PATH,'rt',encoding='utf-8') as stream:self.data=json.load(stream)
        self.meshes=[]
        for mesh in self.data['meshes']:
            self.meshes.append({**mesh,'positions':np.asarray(mesh['positions'],dtype=np.float32),
                'normals':np.asarray(mesh['normals'],dtype=np.float32),
                'uv':np.asarray(mesh.get('uv',[[0,0]]*len(mesh['positions'])),dtype=np.float32),
                'morphs':{k:np.asarray(v,dtype=np.float32) for k,v in mesh['morphs'].items()}})

        from PySide6.QtGui import QImage
        for mesh in self.meshes:
            if mesh.get('texture'):
                image=QImage(str(MODEL_PATH.parent/mesh['texture'])).convertToFormat(QImage.Format.Format_RGBA8888)
                if image.isNull():raise ValueError('モデルのテクスチャが見つかりません。')
                pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.width(),4)
                uv=mesh['uv'].reshape(-1,3,2).mean(axis=1)
                xx=(uv[:,0]%1*(image.width()-1)).astype(int)
                yy=((1-np.clip(uv[:,1],0,1))*(image.height()-1)).astype(int)
                mesh['triangle_colors']=pixels[yy,xx,:3].astype(np.float32)/255

    def transforms(self,pose,now):
        head=rotation(pose.pitch,pose.yaw,-pose.roll)
        transforms={}
        root=np.eye(4,dtype=np.float32)
        root[:3,3]=[pose.headX+pose.yaw*.0016,pose.headY+math.sin(now*1.7)*.009,pose.headZ]
        transforms['root']=root
        for name in [n for n in self.data['bones'] if n != 'root']:
            pivot=np.asarray(self.data['bones'][name]['pivot'])
            r=head if name=='head' else rotation(z=(math.sin(now*2.0)*1.5+pose.browUp*7)*(1 if name.endswith('L') else -1) if name.startswith('ear') else 0)
            local=np.eye(4,dtype=np.float32);local[:3,:3]=r;local[:3,3]=pivot-r@pivot
            transforms[name]=transforms[self.data['bones'][name]['parent']]@local
        return transforms

    def vertices(self,mesh,pose):
        from .mouth import geometry
        procedural=geometry(mesh,pose)
        if procedural is not None:return procedural
        if mesh['name'].startswith(('mouth_curve','mouth_tip')):
            positions=mesh['positions'].copy()
            positions[:,0]*=pose.mouthWidth
            return positions
        if not mesh['morphs']:return mesh['positions']
        positions=mesh['positions'].copy()
        for name,delta in mesh['morphs'].items():
            amount=getattr(pose,name,0)
            if name=='mouthSmile':amount=pose.mouthSmile
            if amount:positions+=delta*amount
        return positions

    def normals(self,mesh,positions):
        if mesh['name'].startswith('round_tanuki'):
            normal=positions.copy()
            normal[:,0]/=1.10**2
            normal[:,1]=(normal[:,1]-1.18)/1.05**2
            normal[:,2]/=.79**2
            normal/=np.maximum(np.linalg.norm(normal,axis=1,keepdims=True),.0001)
            return -normal if mesh.get('outline') else normal
        return mesh['normals']

    def posed_meshes(self,pose,now):
        transforms=self.transforms(pose,now)
        for mesh in self.meshes:
            matrix=transforms[mesh['bone']]
            vertices=self.vertices(mesh,pose)
            yield mesh,vertices@matrix[:3,:3].T+matrix[:3,3],self.normals(mesh,vertices)@matrix[:3,:3].T
