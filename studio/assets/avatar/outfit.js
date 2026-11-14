import * as THREE from './vendor/three.module.js';

function fabric(color, folds=false){
  return new THREE.ShaderMaterial({uniforms:{uColor:{value:new THREE.Color(color)},uFolds:{value:folds?1:0},uShadeSoftness:{value:.006},uOutline:{value:0},uInk:{value:false},uLightPosition:{value:new THREE.Vector3(-2.8,3.2,3.6)}},side:THREE.DoubleSide,
    vertexShader:`uniform float uOutline;varying vec3 vFabricWorld;varying vec3 vNormal;varying vec2 vUv;void main(){vFabricWorld=(modelMatrix*vec4(position,1.)).xyz;vNormal=normalize(mat3(modelMatrix)*normal);vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position+normal*uOutline,1.);}`,
    fragmentShader:`uniform vec3 uColor;uniform float uShadeSoftness;uniform float uFolds;uniform bool uInk;uniform vec3 uLightPosition;varying vec3 vFabricWorld;varying vec3 vNormal;varying vec2 vUv;
    void main(){float l=max(0.,dot(normalize(vNormal),normalize(uLightPosition-vFabricWorld)));
      float edge=max(fwidth(l)*1.25,uShadeSoftness);
      float band=smoothstep(.48-edge,.48+edge,l);
      float gradient=smoothstep(.02,.98,l);
      vec3 shade=mix(vec3(.72,.62,.66),vec3(1.),band)*(.88+.12*gradient);
      float fold=1.-uFolds*.07*pow(.5+.5*cos(vUv.x*37.),3.);
      gl_FragColor=vec4(uColor*shade*fold,1.);
      if(uInk)gl_FragColor=vec4(uColor,1.);
      #include <colorspace_fragment>
    }`
  });
}
export function outlineCloth(mesh,width){
  const ink=fabric('#190d10');ink.side=THREE.BackSide;
  ink.uniforms.uOutline.value=width*.0035;ink.uniforms.uInk.value=true;
  const shell=new THREE.Mesh(mesh.geometry,ink);shell.name='服の輪郭';shell.renderOrder=-1;mesh.add(shell);
}
export function shirtMaterial(bounds,headBounds){
  const material=fabric('#fffafa',true);
  // Keep the original hands below the white sleeves; the central torso is clothed.
  material.uniforms.uCenter={value:bounds.getCenter(new THREE.Vector3())};
  material.uniforms.uSize={value:bounds.getSize(new THREE.Vector3())};
  material.uniforms.uNeckBottom={value:headBounds.min.y-headBounds.getSize(new THREE.Vector3()).y*.035-bounds.getSize(new THREE.Vector3()).y*.12};
  material.uniforms.uSkin={value:new THREE.Color('#F5E2CD')};
  material.uniforms.uCape={value:new THREE.Color('#db061a')};
  material.uniforms.uNeckColor={value:new THREE.Color('#D59E8D')};
  material.uniforms.uClothingInverse={value:new THREE.Matrix4()};
  material.vertexShader=material.vertexShader.replace('varying vec3 vNormal;','uniform mat4 uClothingInverse;varying vec3 vWorld;varying vec3 vNormal;').replace('vNormal=','vWorld=(uClothingInverse*modelMatrix*vec4(position,1.)).xyz;vNormal=');
  material.fragmentShader=material.fragmentShader.replace('uniform vec3 uColor;','uniform float uNeckBottom;uniform vec3 uNeckColor;uniform vec3 uCape;uniform vec3 uSkin;uniform vec3 uCenter;uniform vec3 uSize;varying vec3 vWorld;uniform vec3 uColor;').replace('gl_FragColor=vec4(uColor*','vec3 base=uColor;if(abs(vWorld.x-uCenter.x)>uSize.x*.31 && vWorld.y<uCenter.y-uSize.y*.23)base=uSkin;float neckX=abs(vWorld.x-uCenter.x)/uSize.x;if(neckX>.105&&vWorld.y>uNeckBottom+uSize.y*.09)base=uCape;float neckEdge=uNeckBottom+neckX*uSize.y*.90;float neckMask=(1.-smoothstep(.085,.12,neckX))*smoothstep(neckEdge,neckEdge+uSize.y*.018,vWorld.y);base=mix(base,uNeckColor,neckMask);float neckLineWidth=max(fwidth(neckMask),.012);float neckOutline=1.-smoothstep(neckLineWidth*.8,neckLineWidth*1.8,abs(neckMask-.5));base=mix(base,vec3(0.),neckOutline);gl_FragColor=vec4(base*');
  material.fragmentShader=material.fragmentShader.replace('if(uInk)', 'gl_FragColor.rgb=mix(gl_FragColor.rgb,uNeckColor,neckMask);gl_FragColor.rgb=mix(gl_FragColor.rgb,vec3(0.),neckOutline);if(uInk)');
  return material;
}
export function createOutfit(bodyBounds,headBounds){
  const group=new THREE.Group();group.name='3D白シャツと赤い立ち襟マント';
  const size=bodyBounds.getSize(new THREE.Vector3()),center=bodyBounds.getCenter(new THREE.Vector3());
  const headSize=headBounds.getSize(new THREE.Vector3());
  const shoulder=headBounds.min.y-headSize.y*.035;
  const bottom=bodyBounds.min.y-size.y*.025;
  const red=fabric('#db061a',true),lining=fabric('#800d19');
  const edgeMaterial=new THREE.LineBasicMaterial({color:'#23090b'});
  const z=center.z;
  const trimMaterial=new THREE.MeshBasicMaterial({color:'#211115'});
  function trim(points,radius=size.x*.0055){
    const curve=new THREE.CurvePath();
    for(let i=1;i<points.length;i++)curve.add(new THREE.LineCurve3(points[i-1],points[i]));
    const mesh=new THREE.Mesh(new THREE.TubeGeometry(curve,Math.max(8,points.length*3),radius,5,false),trimMaterial);
    mesh.name='布の縁取り';group.add(mesh);
  }


  function shell(profiles,start,end,segments,material,name){
    if(name.includes('マント')){
      const source=profiles;
      const curve=new THREE.CatmullRomCurve3(source.map(p=>new THREE.Vector3(p.rx,p.y,p.rz)),false,'centripetal');
      profiles=Array.from({length:25},(_,i)=>{
        const t=i/24,p=curve.getPoint(t),index=Math.min(source.length-2,Math.floor(t*(source.length-1))),f=t*(source.length-1)-index;
        return {rx:p.x,y:p.y,rz:p.z,open:THREE.MathUtils.lerp(source[index].open??start,source[index+1].open??start,f)};
      });
    }
    const pos=[],uv=[],index=[];
    for(let j=0;j<profiles.length;j++)for(let i=0;i<=segments;i++){
      const p=profiles[j],phi=THREE.MathUtils.lerp(p.open ?? start,p.open === undefined ? end : Math.PI*2-p.open,i/segments);
      const ripple=j/(profiles.length-1)*Math.sin(phi*7)*size.x*.026;
      // Pull the opening against the shirt instead of leaving an air gap in front of the arms.
      const fit=THREE.MathUtils.smoothstep(shoulder-p.y,size.y*.035,size.y*.18)*Math.max(0,Math.cos(phi));
      const depth=THREE.MathUtils.lerp(p.rz,size.z*.56,fit);
      pos.push(center.x+Math.sin(phi)*(p.rx+ripple),p.y,z+(p.zShift||0)+Math.cos(phi)*(depth+ripple*.4));uv.push(i/segments,j/(profiles.length-1));
      if(j<profiles.length-1&&i<segments){const a=j*(segments+1)+i,b=a+segments+1;index.push(a,b,a+1,b,b+1,a+1);}
    }
    const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));g.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));g.setIndex(index);g.computeVertexNormals();
    const mesh=new THREE.Mesh(g,material);mesh.name=name;group.add(mesh);
    // Ink only on the hems and opening, with no triangle wireframe.
    const trackedLines=[];
    for(const side of [0,segments]){const points=profiles.map((_,j)=>new THREE.Vector3().fromBufferAttribute(g.attributes.position,j*(segments+1)+side));const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),edgeMaterial);group.add(line);trackedLines.push({line,ids:profiles.map((_,j)=>j*(segments+1)+side)});}
    const hem=[];for(let i=0;i<=segments;i++)hem.push(new THREE.Vector3().fromBufferAttribute(g.attributes.position,(profiles.length-1)*(segments+1)+i));const hemLine=new THREE.Line(new THREE.BufferGeometry().setFromPoints(hem),edgeMaterial);group.add(hemLine);trackedLines.push({line:hemLine,ids:Array.from({length:segments+1},(_,i)=>(profiles.length-1)*(segments+1)+i)});
    if(!name.includes('立ち襟'))for(const {line,ids} of trackedLines){group.remove(line);line.geometry.dispose();trim((ids.length===profiles.length?ids.slice(4):ids).map(i=>new THREE.Vector3().fromBufferAttribute(g.attributes.position,i)));}
  }
  shell([
    {y:shoulder+size.y*.010,rx:size.x*.22,rz:size.z*.85,open:.70},
    {y:shoulder-size.y*.035,rx:size.x*.42,rz:size.z*.85,open:.65},
    {y:shoulder-size.y*.070,rx:size.x*.53,rz:size.z*.82,open:.47},
    {y:shoulder-size.y*.16,rx:size.x*.53,rz:size.z*.84,open:.38},
    {y:bottom+size.y*.25,rx:size.x*.55,rz:size.z*.88,open:.33},
    {y:bottom,rx:size.x*.58,rz:size.z*.96,open:.30}
  ],.64,Math.PI*2-.64,32,red,'肩から背中を包むマント');
  // Continuous mantle collar; its front rows stay separated even under the jaw.
  const around=80,rows=16,stride=around+1,layer=stride*(rows+1),positions=[],uvs=[],faces=[];
  const collarCenterZ=z-size.z*.14,thickness=size.x*.022;
  for(let side=0;side<2;side++)for(let row=0;row<=rows;row++)for(let i=0;i<=around;i++){
    const v=row/rows,phi=THREE.MathUtils.lerp(1.0,Math.PI*2-1.0,i/around);
    const flare=v*v*(3-2*v);
    const rx=size.x*THREE.MathUtils.lerp(.27,.52,flare)-side*thickness;
    const front=Math.max(0,Math.cos(phi));
    const rz=size.z*THREE.MathUtils.lerp(.54,.82,flare)-side*thickness;
    // The rear stands up; the front rolls forward BELOW the jaw, never across the cheeks.
    const height=size.y*(.20-.12*Math.cos(phi));
    const baseY=shoulder-size.y*.045;
    const frontWeight=THREE.MathUtils.smoothstep(front,0,.6);
    const heightLimit=headBounds.min.y-size.y*.045-baseY;
    const effectiveHeight=THREE.MathUtils.lerp(height,Math.min(height,heightLimit),frontWeight);
    const topY=baseY+Math.max(size.y*.012,effectiveHeight)*v;
    positions.push(center.x+Math.sin(phi)*rx,topY,collarCenterZ+Math.cos(phi)*rz+front*flare*size.z*.13);
    uvs.push(i/around,v);
    if(row<rows&&i<around){const a=side*layer+row*stride+i,b=a+stride;if(side===0)faces.push(a,b,a+1,b,b+1,a+1);else faces.push(a,a+1,b,b,a+1,b+1);}
  }
  const rimStart=faces.length;
  for(const row of [0,rows])for(let i=0;i<around;i++){const a=row*stride+i,b=a+1;faces.push(a,b,a+layer,b,b+layer,a+layer);}
  for(const col of [0,around])for(let row=0;row<rows;row++){const a=row*stride+col,b=a+stride;faces.push(a,a+layer,b,b,a+layer,b+layer);}
  const collarGeometry=new THREE.BufferGeometry();collarGeometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));collarGeometry.setAttribute('uv',new THREE.Float32BufferAttribute(uvs,2));collarGeometry.setIndex(faces);collarGeometry.computeVertexNormals();
  // Contiguous material ranges avoid one draw call per quad.
  for(const side of [0,1])collarGeometry.addGroup(side*rows*around*6,rows*around*6,side);
  collarGeometry.addGroup(rimStart,faces.length-rimStart,0);
  const collarMaterials=[red.clone(),lining.clone()];
  for(const material of collarMaterials){
    material.uniforms.uShadeSoftness.value=.065;material.uniforms.uFolds.value=0;
    // Hide the collar skirt inside the shoulder cloth rather than intersecting it.
    material.uniforms.uShoulderY={value:shoulder-size.y*.025};
    material.vertexShader=material.vertexShader.replace('void main(){', 'varying float vCollarY;void main(){vCollarY=position.y;');
    material.fragmentShader=material.fragmentShader.replace('uniform vec3 uColor;', 'varying float vCollarY;uniform float uShoulderY;uniform vec3 uColor;').replace('void main(){', 'void main(){if(vCollarY<uShoulderY)discard;');
  }
  const collar=new THREE.Mesh(collarGeometry,collarMaterials);collar.renderOrder=5;collar.name='首を囲む一体型立ち襟';group.add(collar);
  const rest=Float32Array.from(positions),shift=new Float32Array(layer),speed=new Float32Array(layer);
  const borderIds=[];for(let i=0;i<=around;i++)borderIds.push(rows*stride+i);
  const border=new THREE.Line(new THREE.BufferGeometry().setFromPoints(borderIds.map(i=>new THREE.Vector3().fromBufferAttribute(collarGeometry.attributes.position,i))),edgeMaterial);
  const inverse=new THREE.Matrix4(),point=new THREE.Vector3();
  group.userData.step=(dt,headCollider)=>{
    headCollider.updateWorldMatrix(true,false);inverse.copy(headCollider.matrixWorld).invert();group.updateWorldMatrix(true,false);
    const limit=Math.pow(1.025,4),h=Math.min(dt,1/30);
    const attr=collarGeometry.attributes.position;
    for(let row=1;row<=rows;row++)for(let i=0;i<=around;i++){
      const vertex=row*stride+i,j=vertex*3;
      const inside=offset=>{
        point.set(rest[j],rest[j+1]-offset,(rest[j+2]+rest[(vertex+layer)*3+2])*.5).applyMatrix4(group.matrixWorld).applyMatrix4(inverse);
        return Math.pow(Math.abs(point.x),4)+Math.pow(Math.abs(point.y),4)+Math.pow(Math.abs(point.z),4)<limit;
      };
      let target=0;
      // Contact compresses the soft upper edge down toward the shoulders, not toward the camera.
      if(inside(0)){
        let lo=0,hi=size.y*.28;
        for(let n=0;n<9;n++){const mid=(lo+hi)*.5;if(inside(mid))lo=mid;else hi=mid;}target=Math.min(hi,Math.max(0,rest[j+1]-(shoulder-size.y*.045)-size.y*.008*row/rows));
      }
      speed[vertex]+=(target-shift[vertex])*90*h;speed[vertex]*=Math.exp(-12*h);shift[vertex]+=speed[vertex]*h;
      if(shift[vertex]<target){shift[vertex]=target;speed[vertex]=Math.max(0,speed[vertex]);}
      shift[vertex]=THREE.MathUtils.clamp(shift[vertex],0,size.y*.28);
      for(const side of [0,1]){const index=vertex+side*layer,k=index*3;attr.setXYZ(index,rest[k],rest[k+1]-shift[vertex],rest[k+2]);}
    }
    attr.needsUpdate=true;collarGeometry.computeVertexNormals();
    const bp=border.geometry.attributes.position;for(let i=0;i<borderIds.length;i++){point.fromBufferAttribute(attr,borderIds[i]);bp.setXYZ(i,point.x,point.y,point.z);}bp.needsUpdate=true;
  };
  return group;
}
