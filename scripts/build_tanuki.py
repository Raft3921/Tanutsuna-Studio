"""Blender source generator: limbless tanuki, cupped ears, real mouth cavity and UV sunglasses."""
from pathlib import Path
import bpy,bmesh,gzip,json,math,struct
import numpy as np
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'studio/assets/tanuki';OUT.mkdir(parents=True,exist_ok=True)
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
PARTS=[];RX,RY,RZ,CZ=1.10,.79,1.05,1.18
N=64
STROKE=.042  # Shared visible width: shell offset, frame face, and twice the mouth/ear tube radius.

def linear(v):return v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4

def mat(name,color):
 m=bpy.data.materials.new(name);m['srgb']=list(color);m.diffuse_color=(*map(linear,color),1);m.use_nodes=True
 b=m.node_tree.nodes.get('Principled BSDF') or m.node_tree.nodes.new('ShaderNodeBsdfPrincipled');b.name='Principled BSDF';b.inputs['Base Color'].default_value=m.diffuse_color;b.inputs['Roughness'].default_value=1
 output=next((node for node in m.node_tree.nodes if node.type=='OUTPUT_MATERIAL'),None) or m.node_tree.nodes.new('ShaderNodeOutputMaterial')
 output.name='Material Output';m.node_tree.links.new(b.outputs[0],output.inputs['Surface'])
 return m
brown=mat('brown',(.55,.355,.235))
ink=mat('ink',(.105,.090,.095))
inside=mat('mouth_inside',(.67,.16,.20))
inner=mat('ear_inner',(.37,.225,.18))
lensmat=mat('smoke_lens',(.115,.145,.18))
framemat=mat('graphite_frame',(.105,.090,.095))
# The coat and lens pigments are UV textures. The frame is a separate, thin solid with a constant-width section.
size=1024
colors=np.zeros((size,size,4),dtype=np.float32);colors[:,:,:3]=brown['srgb'];colors[:,:,3]=1
image=bpy.data.images.new('tanuki_coat',width=size,height=size,alpha=True)
image.pixels.foreach_set(colors.ravel());image.filepath_raw=str(OUT/'tanuki_basecolor.png');image.file_format='PNG';image.save()
tex=brown.node_tree.nodes.new('ShaderNodeTexImage');tex.image=image
brown.node_tree.links.new(tex.outputs['Color'],brown.node_tree.nodes.get('Principled BSDF').inputs['Base Color']);brown['texture']='tanuki_basecolor.png'
ls=512;u,v=np.meshgrid((np.arange(ls)+.5)/ls,(np.arange(ls)+.5)/ls)
rgba=np.zeros((ls,ls,4),dtype=np.float32);rgba[:,:,:3]=lensmat['srgb'];rgba[:,:,3]=1
slant=u-.22*v
for lo,hi in [(.22,.32),(.54,.58),(.73,.79)]:
 band=(slant>lo)&(slant<hi);rgba[band,:3]=[.245,.295,.345]
# A soft, narrow reflection along the upper lens edge; broad glare is kept restrained.
glow=np.clip((v-.77)/.23,0,1)[...,None]
rgba[:,:,:3]+=glow*.025
lens_image=bpy.data.images.new('goggle_lens',width=ls,height=ls,alpha=True)
lens_image.pixels.foreach_set(rgba.ravel());lens_image.filepath_raw=str(OUT/'goggle_lens.png');lens_image.file_format='PNG';lens_image.save()
tex=lensmat.node_tree.nodes.new('ShaderNodeTexImage');tex.image=lens_image
lensmat.node_tree.links.new(tex.outputs['Color'],lensmat.node_tree.nodes.get('Principled BSDF').inputs['Base Color']);lensmat['texture']='goggle_lens.png'

def surface(x,z):return -RY*math.sqrt(max(0,1-(x/RX)**2-((z-CZ)/RZ)**2))
def uv(co):
 x,y,z=co;return (math.atan2(x,-y)/(2*math.pi)+.5,math.asin(max(-1,min(1,(z-CZ)/RZ)))/math.pi+.5)
def create(name,points,faces,material,bone='head',morph=None,textured=False):
 mesh=bpy.data.meshes.new(name);mesh.from_pydata(points,[],faces);mesh.update()
 obj=bpy.data.objects.new(name,mesh);bpy.context.collection.objects.link(obj);mesh.materials.append(material);obj['bone']=bone
 for face in mesh.polygons:face.use_smooth=True
 if textured:
  layer=mesh.uv_layers.new(name='UVMap')
  for face in mesh.polygons:
   vals=[uv(mesh.vertices[mesh.loops[i].vertex_index].co) for i in face.loop_indices]
   # Fix the back seam; the front visor never crosses this seam.
   if max(q[0] for q in vals)-min(q[0] for q in vals)>.5:vals=[(a+1 if a<.5 else a,b) for a,b in vals]
   for i,val in zip(face.loop_indices,vals):layer.data[i].uv=val
 if morph:
  obj.shape_key_add(name='Basis')
  for key,coords in morph.items():
   target=obj.shape_key_add(name=key)
   for a,b in zip(target.data,coords):a.co=b
 PARTS.append(obj);return obj

def mouth_top(x):
 w=.14;r=.035
 a=(math.sqrt(x*x+r*r)-r)/(math.sqrt(w*w+r*r)-r)
 return 1.30+.13*(1-min(1,a))

def opening(jaw,t,extra=0):
 x=math.sin(t)*.14
 z=mouth_top(x)
 if math.cos(t)<0:
  curve=(max(0,1-(abs(x)/.14)**1.6))**.7
  z=(1-jaw)*z+jaw*(1.30-.49*curve)
 return (x+math.sin(t)*extra,z+math.cos(t)*extra)

def head_points(jaw):
 pts=[]
 # Open front hemisphere: its inner boundary is the actual mouth hole.
 for ring in range(25):
  a=ring/24
  for i in range(N):
   t=2*math.pi*i/N;ix,iz=opening(jaw,t,.004)
   x=(1-a)*ix+a*RX*math.sin(t);z=(1-a)*iz+a*(CZ+RZ*math.cos(t))
   pts.append((x,surface(x,z),z))
 # Back hemisphere closes the same outer rim with a regular disk.
 for ring in range(1,17):
  r=1-ring/17
  for i in range(N):
   t=2*math.pi*i/N;pts.append((RX*r*math.sin(t),RY*math.sqrt(1-r*r),CZ+RZ*r*math.cos(t)))
 pts.append((0,RY,CZ));return pts
faces=[]
for ring in range(40):
 for i in range(N):
  a=ring*N+i;b=ring*N+(i+1)%N;faces.append((a,b,b+N,a+N))
for i in range(N):faces.append((40*N+i,40*N+(i+1)%N,41*N))
head=create('round_tanuki',head_points(0),faces,brown,morph={'jawOpen':head_points(1)},textured=True)
# Ensure consistent outward winding on the closed portions; the front normal points -Y.
if head.data.polygons[3*N].normal.y>0:
 for f in head.data.polygons:f.flip()
head.data.update()

def mouth_points(jaw,kind):
 pts=[]
 if kind=='lip':
  for i in range(N):
   t=2*math.pi*i/N;x,z=opening(jaw,t)
   for j in range(12):
    phi=2*math.pi*j/12;r=STROKE/2
    a=np.array(opening(jaw,t-.0001));b=np.array(opening(jaw,t+.0001));tangent=b-a
    normal=np.array([-tangent[1],tangent[0]]);normal/=max(.00001,np.linalg.norm(normal))
    xx=x+normal[0]*r*math.cos(phi);zz=z+normal[1]*r*math.cos(phi)
    pts.append((xx,surface(x,z)-.009+r*math.sin(phi),zz))
 else:
  for ring in range(9):
   a=ring/9
   for i in range(N):
    t=2*math.pi*i/N;x,z=opening(jaw,t);center=1.365-(.024+.72*jaw)/2
    xx=x*(1-a*.87);zz=center+(z-center)*(1-a*.87)
    pts.append((xx,surface(x,z)+.014+.30*math.sin(a*math.pi/2),zz))
  center=1.365-(.024+.72*jaw)/2;pts.append((0,surface(0,center)+.33,center))
 return pts
lipfaces=[(i*12+j,((i+1)%N)*12+j,((i+1)%N)*12+(j+1)%12,i*12+(j+1)%12) for i in range(N//4,3*N//4) for j in range(12)]
lip=create('mouth_lip',mouth_points(0,'lip'),lipfaces,ink,morph={'jawOpen':mouth_points(1,'lip')})
cavityfaces=[]
for r in range(8):
 for i in range(N):a=r*N+i;b=r*N+(i+1)%N;cavityfaces.append((a,a+N,b+N,b))
for i in range(N):cavityfaces.append((8*N+i,9*N,8*N+(i+1)%N))
cavity=create('mouth_cavity',mouth_points(0,'cavity'),cavityfaces,inside,morph={'jawOpen':mouth_points(1,'cavity')})
# Normals on the bowl face into the opening.
if cavity.data.polygons[0].normal.y>0:
 for f in cavity.data.polygons:f.flip()
cavity.data.update()

def curve(name,coords,radius,material,bone='head'):
 bpy.ops.object.select_all(action='DESELECT')
 c=bpy.data.curves.new(name,'CURVE');c.dimensions='3D';c.bevel_depth=radius;c.bevel_resolution=2;c.resolution_u=2;c.use_fill_caps=True
 s=c.splines.new('BEZIER');s.bezier_points.add(len(coords)-1)
 for p,co in zip(s.bezier_points,coords):p.co=co;p.handle_left_type=p.handle_right_type='AUTO'
 obj=bpy.data.objects.new(name,c);bpy.context.collection.objects.link(obj);obj.select_set(True);bpy.context.view_layer.objects.active=obj
 bpy.ops.object.convert(target='MESH');obj.data.materials.append(material);obj['bone']=bone;PARTS.append(obj)
 return obj
# One continuous mouth line; no nose, crossed strokes or separate moustache pieces.
coords=[]
for i in range(41):
 x=-.29+.58*i/40
 if abs(x)<=.14:z=mouth_top(x)
 else:
  t=(abs(x)-.14)/(.29-.14)
  z=1.30-.025*math.sin(math.pi*t)+.035*t
 coords.append((x,surface(x,z)-.025,z))
curve('mouth_curve',coords,STROKE/2,ink)
for side,suffix in [(-1,'L'),(1,'R')]:
 x=side*.29;z=1.335
 bpy.ops.mesh.primitive_uv_sphere_add(segments=16,ring_count=8,radius=STROKE/2,location=(x,surface(x,z)-.025,z))
 tip=bpy.context.object;tip.name='mouth_tip_'+suffix;tip.data.materials.append(ink);tip['bone']='head';PARTS.append(tip)


# Smooth rounded visor contour. Arc-length resampling and normal offsets keep the rim width uniform.
def bezier(a,b,c,d,t):
 return (1-t)**3*np.array(a)+3*(1-t)**2*t*np.array(b)+3*(1-t)*t*t*np.array(c)+t**3*np.array(d)
segments=[
 ((0,2.045),(.20,2.045),(.43,2.005),(.61,1.95)),
 ((.61,1.95),(.82,1.90),(.91,1.78),(.915,1.64)),
 ((.915,1.64),(.925,1.53),(.88,1.49),(.76,1.49)),
 ((.76,1.49),(.53,1.45),(.20,1.63),(0,1.63)),
 ((0,1.63),(-.20,1.63),(-.53,1.45),(-.76,1.49)),
 ((-.76,1.49),(-.88,1.49),(-.925,1.53),(-.915,1.64)),
 ((-.915,1.64),(-.91,1.78),(-.82,1.90),(-.61,1.95)),
 ((-.61,1.95),(-.43,2.005),(-.20,2.045),(0,2.045))]
raw=np.array([bezier(*q,j/40) for q in segments for j in range(40)])
closed=np.vstack([raw,raw[0]]);dist=np.r_[0,np.cumsum(np.linalg.norm(np.diff(closed,axis=0),axis=1))]
count=96;sample=np.linspace(0,dist[-1],count,endpoint=False)
boundary=np.column_stack([np.interp(sample,dist,closed[:,k]) for k in range(2)])
tangent=np.roll(boundary,-1,axis=0)-np.roll(boundary,1,axis=0)
normal=np.column_stack([-tangent[:,1],tangent[:,0]]);normal/=np.linalg.norm(normal,axis=1)[:,None]
half=STROKE/2
profiles=[(half*.85,.027),(half*1.12,.040),(half*1.12,.059),(half,.067),(-half,.067),(-half*1.12,.059),(-half*1.12,.040),(-half*.85,.027)]
points=[]
for i,(x,z) in enumerate(boundary):
 for width,depth in profiles:
  xx,zz=np.array([x,z])+normal[i]*width
  points.append((float(xx),surface(float(xx),float(zz))-depth,float(zz)))
faces=[(i*8+j,((i+1)%count)*8+j,((i+1)%count)*8+(j+1)%8,i*8+(j+1)%8) for i in range(count) for j in range(8)]
frame=create('goggle_frame',points,faces,framemat)
bm=bmesh.new();bm.from_mesh(frame.data);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(frame.data);bm.free();frame.data.update()
# The lens sits behind the rim; its convex face gives depth while the eyes stay completely fixed.
points=[];center=np.array([0,1.80])
for ring in range(13):
 r=ring/12
 for i,point in enumerate(boundary-normal*.015):
  x,z=center+(point-center)*r
  points.append((float(x),surface(float(x),float(z))-.047-.030*(1-r*r),float(z)))
faces=[(r*count+i,r*count+(i+1)%count,(r+1)*count+(i+1)%count,(r+1)*count+i) for r in range(12) for i in range(count)]
lens=create('goggle_lens',points,faces,lensmat)
if lens.data.polygons[-1].normal.y>0:
 for f in lens.data.polygons:f.flip()
lens.data.update()
layer=lens.data.uv_layers.new(name='UVMap')
for loop in lens.data.loops:
 co=lens.data.vertices[loop.vertex_index].co
 layer.data[loop.index].uv=((co.x+1)/2,(co.z-1.40)/.70)

# Cupped round ears: a continuous outer shell, rolled rim and recessed concha.
for side,suffix in [(-1,'L'),(1,'R')]:
 center=Vector((side*.82,.045,2.035));points=[];segments=64
 profiles=[(.0,.105),(.38,.132),(.76,.105),(1.0,.015),(.97,-.085),(.80,-.175),(.59,-.115),(.28,-.025),(.0,.015)]
 for r,depth in profiles:
  for i in range(segments):
   angle=2*math.pi*i/segments
   xx=.305*r*math.sin(angle);zz=.305*r*math.cos(angle)
   # Slight outward rotation keeps the rounded tanuki ear silhouette.
   x=center.x+xx*math.cos(side*.18)+zz*math.sin(side*.18);z=center.z-xx*math.sin(side*.18)+zz*math.cos(side*.18)
   points.append((x,center.y+depth,z))
 f=[]
 for ring in range(len(profiles)-1):
  for i in range(segments):a=ring*segments+i;b=ring*segments+(i+1)%segments;f.append((a,b,b+segments,a+segments))
 ear=create('ear_'+suffix,points,f,brown,'ear_'+suffix)
 # Inner-ear pigment is a material on the bowl's own faces, not another floating sphere.
 ear.data.materials.append(inner)
 for polygon in ear.data.polygons:
  polygon.material_index=1 if polygon.index//segments>=5 else 0
 bm=bmesh.new();bm.from_mesh(ear.data);bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=.000001);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(ear.data);bm.free();ear.data.update()
 # Ear fold follows the recessed inner surface.
 fold=[]
 for i in range(5):
  a=-.65+i*.32;fold.append((center.x+math.sin(a)*.15,center.y-.105,center.z+math.cos(a)*.14))
 curve('ear_fold_'+suffix,fold,STROKE/2,ink,'ear_'+suffix)

# Actual skeleton; no limbs, no eye bones and no blink/wide-eye shape keys.
arm=bpy.data.armatures.new('TanukiRig');rig=bpy.data.objects.new('TanukiRig',arm);bpy.context.collection.objects.link(rig)
bpy.ops.object.select_all(action='DESELECT');rig.select_set(True);bpy.context.view_layer.objects.active=rig;bpy.ops.object.mode_set(mode='EDIT')
bones={'root':((0,0,1.18),None),'head':((0,0,1.18),'root'),'ear_L':((-.82,.045,2.035),'head'),'ear_R':((.82,.045,2.035),'head')}
for name,(pos,parent) in bones.items():
 b=arm.edit_bones.new(name);b.head=pos;b.tail=(pos[0],pos[1],pos[2]+.15)
 if parent:b.parent=arm.edit_bones[parent]
bpy.ops.object.mode_set(mode='OBJECT')
# Inverted hull outlines. They are retained in GLB/.blend, not a screen-space mockup.
for obj in list(PARTS):
 if obj.name not in ('round_tanuki','ear_L','ear_R'):continue
 if obj.name in ('round_tanuki','ear_L','ear_R'):
  # Only a clean convex hull supplies the silhouette. Extruding the recessed ear bowl
  # makes its inner walls reappear as diagonal black wedges at the attachment.
  bpy.ops.object.select_all(action='DESELECT')
  center=(0,0,CZ) if obj.name=='round_tanuki' else ((-.82 if obj.name=='ear_L' else .82),.045,2.035)
  bpy.ops.mesh.primitive_uv_sphere_add(segments=64,ring_count=32,location=center)
  outline=bpy.context.object;outline.scale=(RX,RY,RZ) if obj.name=='round_tanuki' else (.305,.15,.305)
  bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
  outline.name=obj.name+'_outline';outline['bone']=obj['bone']

 else:
  outline=obj.copy();outline.data=obj.data.copy();outline.name=obj.name+'_outline';bpy.context.collection.objects.link(outline)
 outline.data.materials.clear();outline.data.materials.append(ink);outline['outline']=True
 delta=[v.normal.normalized()*STROKE for v in outline.data.vertices]
 if outline.data.shape_keys:
  for key in outline.data.shape_keys.key_blocks:
   for v,d in zip(key.data,delta):v.co+=d
 else:
  for v,d in zip(outline.data.vertices,delta):v.co+=d
 for face in outline.data.polygons:face.flip()
 outline.data.update();PARTS.append(outline)
for obj in PARTS:
 group=obj.vertex_groups.new(name=obj['bone']);group.add(list(range(len(obj.data.vertices))),1,'REPLACE')
 mod=obj.modifiers.new('TanukiArmature','ARMATURE');mod.object=rig;obj.parent=rig
bpy.context.view_layer.update()

def convert(v):return [round(v.x,6),round(v.z,6),round(-v.y,6)]
runtime={'format':'tanutsuna-mesh-v2','version':'0.5.0','front':'+Z','up':'+Y','toon':True,'fixedEyes':True,
 'bones':{n:{'pivot':convert(Vector(p)),'parent':parent} for n,(p,parent) in bones.items()},'meshes':[]}
for obj in PARTS:
 mesh=obj.data;mesh.calc_loop_triangles();loops=[i for tri in mesh.loop_triangles for i in tri.loops]
 order=[mesh.loops[i].vertex_index for i in loops];world=obj.matrix_world;nm=world.to_3x3().inverted().transposed()
 # Separate triangle groups by material; this keeps the recessed ears one continuous mesh.
 for material_index in sorted(set(p.material_index for p in mesh.polygons)):
  chosen=[loop for tri in mesh.loop_triangles if mesh.polygons[tri.polygon_index].material_index==material_index for loop in tri.loops]
  idx=[mesh.loops[i].vertex_index for i in chosen]
  mat_=mesh.materials[material_index];morphs={}
  if mesh.shape_keys:
   for key in list(mesh.shape_keys.key_blocks)[1:]:morphs[key.name]=[convert(world.to_3x3()@(key.data[i].co-mesh.vertices[i].co)) for i in idx]
  runtime['meshes'].append({'name':obj.name+('::'+str(material_index) if material_index else ''),'bone':obj['bone'],
   'positions':[convert(world@mesh.vertices[i].co) for i in idx],'normals':[convert((nm@mesh.vertices[i].normal).normalized()) for i in idx],
   'uv':[list(mesh.uv_layers.active.data[i].uv) for i in chosen] if mesh.uv_layers else [[0,0]]*len(idx),
   'texture':mat_.get('texture',''),'outline':bool(obj.get('outline',False)),
   'color':list(mat_['srgb']),'morphs':morphs,
   **({'procedural_indices':idx} if obj.name in ('round_tanuki','mouth_lip','mouth_cavity') else {})})
with gzip.open(OUT/'tanuki.mesh.json.gz.tmp','wt',encoding='utf-8') as stream:json.dump(runtime,stream,separators=(',',':'))
(OUT/'tanuki.mesh.json.gz.tmp').replace(OUT/'tanuki.mesh.json.gz')
bpy.ops.object.select_all(action='DESELECT')
for obj in PARTS+[rig]:obj.select_set(True)
bpy.ops.export_scene.gltf(filepath=str(OUT/'tanuki.glb'),export_format='GLB',use_selection=True,export_skins=True,export_morph=True,export_animations=False)
# glTF's unlit extension preserves the graphic palette in external viewers.
glb=(OUT/'tanuki.glb').read_bytes();length,kind=struct.unpack_from('<II',glb,12);gltf=json.loads(glb[20:20+length]);binary=glb[20+length:]
for m in gltf.get('materials',[]):
 m.setdefault('extensions',{})['KHR_materials_unlit']={};m['doubleSided']=False
used=gltf.setdefault('extensionsUsed',[])
if 'KHR_materials_unlit' not in used:used.append('KHR_materials_unlit')
j=json.dumps(gltf,separators=(',',':')).encode();j+=b' '*((-len(j))%4)
(OUT/'tanuki.glb').write_bytes(struct.pack('<4sII',b'glTF',2,12+8+len(j)+len(binary))+struct.pack('<II',len(j),0x4E4F534A)+j+binary)
# Blender preview/source uses a constant-band toon material, with no glossy PBR highlights.
for m in bpy.data.materials:
 if 'srgb' not in m:continue
 nodes=m.node_tree.nodes;links=m.node_tree.links;nodes.clear()
 output=nodes.new('ShaderNodeOutputMaterial');output.name='Material Output';emission=nodes.new('ShaderNodeEmission');links.new(emission.outputs[0],output.inputs['Surface'])
 if m in (ink,inside,framemat):emission.inputs['Color'].default_value=(*map(linear,m['srgb']),1);continue
 geo=nodes.new('ShaderNodeNewGeometry');dot=nodes.new('ShaderNodeVectorMath');dot.operation='DOT_PRODUCT';dot.inputs[1].default_value=(-.5,-.7,.65);links.new(geo.outputs['Normal'],dot.inputs[0])
 ramp=nodes.new('ShaderNodeValToRGB');ramp.color_ramp.interpolation='CONSTANT'
 ramp.color_ramp.elements[0].position=.0;ramp.color_ramp.elements[0].color=(.62,.62,.62,1)
 ramp.color_ramp.elements[1].position=.42;ramp.color_ramp.elements[1].color=(1,1,1,1)
 mid=ramp.color_ramp.elements.new(.12);mid.color=(.80,.80,.80,1)
 links.new(dot.outputs['Value'],ramp.inputs[0])
 multiply=nodes.new('ShaderNodeMixRGB');multiply.blend_type='MULTIPLY';multiply.inputs[0].default_value=1;links.new(ramp.outputs['Color'],multiply.inputs[2])
 if m in (brown,lensmat):
  texture=nodes.new('ShaderNodeTexImage');texture.image=image if m==brown else lens_image;links.new(texture.outputs['Color'],multiply.inputs[1])
 else:multiply.inputs[1].default_value=(*map(linear,m['srgb']),1)
 links.new(multiply.outputs[0],emission.inputs['Color'])
# Outline back faces only: transparent front faces for the Blender source render.
nt=ink.node_tree;nodes=nt.nodes;links=nt.links;out=nodes.get('Material Output');old=out.inputs['Surface'].links[0].from_socket
geo=nodes.new('ShaderNodeNewGeometry');transparent=nodes.new('ShaderNodeBsdfTransparent');mix=nodes.new('ShaderNodeMixShader');mix.name='Mix Shader';links.new(geo.outputs['Backfacing'],mix.inputs[0]);links.new(old,mix.inputs[1]);links.new(transparent.outputs[0],mix.inputs[2]);links.new(mix.outputs[0],out.inputs['Surface'])
# Separate outline material to avoid hiding the mouth's normal front faces.
outline_mat=ink.copy();outline_mat.name='outline_backfaces';outline_mat.node_tree.nodes.get('Mix Shader').inputs[0].default_value=0
# Reverse the front/back mix for the inverted hull: front faces render, the near backfaces stay transparent.
for obj in PARTS:
 if obj.get('outline'):obj.data.materials.clear();obj.data.materials.append(ink)
# Standard ink needs no backface handling on mouth curves; shader above already keeps front faces.
for obj in PARTS:
 if obj.data.shape_keys and 'jawOpen' in obj.data.shape_keys.key_blocks:obj.data.shape_keys.key_blocks['jawOpen'].value=.60
world=bpy.context.scene.world or bpy.data.worlds.new('World');bpy.context.scene.world=world;world.use_nodes=True
next(node for node in world.node_tree.nodes if node.type=='BACKGROUND').inputs[0].default_value=(.18,.18,.18,1)
bpy.ops.object.camera_add(location=(2.5,-7,2.8));camera=bpy.context.object;camera.rotation_euler=(Vector((0,0,CZ))-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.type='ORTHO';camera.data.ortho_scale=2.85;bpy.context.scene.camera=camera
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=16;scene.render.resolution_x=1000;scene.render.resolution_y=1000;scene.render.resolution_percentage=100;scene.render.film_transparent=True;scene.view_settings.view_transform='Standard'
image.pack();lens_image.pack()
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'tanuki.blend'))
scene.render.filepath=str(ROOT/'docs/tanuki-3d.png');bpy.ops.render.render(write_still=True)
camera.location=(0,-7,CZ);camera.rotation_euler=(Vector((0,0,CZ))-camera.location).to_track_quat('-Z','Y').to_euler();scene.render.filepath=str(ROOT/'docs/tanuki-front.png');bpy.ops.render.render(write_still=True)
print('Tanuki v0.4:',sum(len(m['positions'])//3 for m in runtime['meshes']),'triangles; fixed sunglasses; mouth cavity; no limbs')
