"""Five adjustable mouth shapes and constant-radius procedural lip geometry."""
import math
import numpy as np

VOWELS = ('A','I','U','E','O')
LABELS = ('あ','い','う','え','お')
DEFAULT_SHAPES = {'A': (1.30,.65), 'I': (1.65,.18), 'U': (.85,.25),
                  'E': (1.60,.40), 'O': (1.05,.62)}


def dimensions(pose, shapes=None):
    shapes = shapes or DEFAULT_SHAPES
    weights = np.array([getattr(pose,'mouth'+v) for v in VOWELS],dtype=float)
    total = weights.sum()
    if total < .001: return shapes['A']
    weights /= total
    return tuple(sum(weights[i]*shapes[v][axis] for i,v in enumerate(VOWELS)) for axis in (0,1))


def surface(x,y):
    return .79*np.sqrt(np.maximum(0,1-(x/1.10)**2-((y-1.18)/1.05)**2))


def opening(t,jaw,width,height):
    x=np.sin(t)*.14
    a=(np.sqrt(x*x+.035**2)-.035)/(math.sqrt(.14**2+.035**2)-.035)
    y=1.30+.13*(1-np.minimum(1,a))
    curve=np.maximum(0,1-(np.abs(x)/.14)**1.6)**.7
    y=np.where(np.cos(t)<0,(1-jaw)*y+jaw*(1.30-.49*curve*height),y)
    return x*width,y


def geometry(mesh,pose):
    """Rebuild tubes at the current shape rather than interpolating tube normals."""
    idx=mesh.get('procedural_indices')
    if idx is None:return None
    idx=np.asarray(idx,dtype=np.int32)
    jaw=float(np.clip(pose.jawOpen,0,1));width=float(np.clip(pose.mouthWidth,.55,1.9))
    height=float(np.clip(pose.mouthHeight,.15,1.3));radius=.021*float(np.clip(pose.mouthStroke,.8,1.8))
    name=mesh['name'];n=64
    if name=='mouth_lip':
        t=(idx//12)*2*math.pi/n;phi=(idx%12)*2*math.pi/12
        x,y=opening(t,jaw,width,height)
        xa,ya=opening(t-.0001,jaw,width,height);xb,yb=opening(t+.0001,jaw,width,height)
        dx,dy=xb-xa,yb-ya
        # A closed mouth doubles back at its corners; use the outward normal there.
        degenerate=np.hypot(dx,dy)<1e-8
        dx=np.where(degenerate,0,dx);dy=np.where(degenerate,-np.sign(np.sin(t)),dy)
        length=np.maximum(1e-12,np.hypot(dx,dy))
        return np.column_stack((x-dy/length*radius*np.cos(phi),
            y+dx/length*radius*np.cos(phi),surface(x,y)+.009-radius*np.sin(phi))).astype(np.float32)
    if name=='mouth_cavity':
        ring=idx//n;t=(idx%n)*2*math.pi/n;a=ring/9
        x,y=opening(t,jaw,width,height)
        center=(1.43+1.30-.49*jaw*height)/2
        xx=x*(1-a*.87);yy=center+(y-center)*(1-a*.87)
        z=surface(x,y)-.014-.30*np.sin(a*math.pi/2)
        center_mask=idx==9*n
        xx=np.where(center_mask,0,xx);yy=np.where(center_mask,center,yy)
        z=np.where(center_mask,surface(0,center)-.33,z)
        return np.column_stack((xx,yy,z)).astype(np.float32)
    if name=='round_tanuki':
        # Preserve the authored topology and back. Only the front 24 rings follow the hole.
        result=mesh['positions'].copy();mask=idx<25*n
        k=idx[mask];a=k//n/24;t=k%n*2*math.pi/n
        x,y=opening(t,jaw,width,height);x+=np.sin(t)*.004;y+=np.cos(t)*.004
        xx=(1-a)*x+a*1.10*np.sin(t);yy=(1-a)*y+a*(1.18+1.05*np.cos(t))
        result[mask]=np.column_stack((xx,yy,surface(xx,yy)))
        return result
    return None
