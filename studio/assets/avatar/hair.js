import * as THREE from './vendor/three.module.js';

// Low polygon volumetric cap and swept elliptical locks, in head coordinates.
export function createHairstyle(width, headDepth, browColor = new THREE.Color('#2b1a11')) {
  const group=new THREE.Group();group.name='立体ヘア・頭皮と曲線の房';
  const tipOffsets=Array.from({length:32},()=>new THREE.Vector3());
  const physics=[];
  const vertex=`uniform vec3 uTips[32];uniform float uOutline;uniform vec3 uHeadSize;
    attribute float aLockId;
    varying vec3 vNormal;varying vec3 vLocal;varying float vLockT;
    void main(){vec3 p=position;vec3 n=normal;float t=uv.y;float w=t*t*(3.-2.*t);
      if(aLockId>=0.){
        vec3 bend=uTips[int(aLockId)];p+=bend*w;
        // Keep moving locks outside their rest clearance around the skull.
        vec3 center=vec3(0.,-.10*uHeadSize.x,0.);
        vec3 radii=vec3(.57*uHeadSize.x,.58*uHeadSize.x,.54*uHeadSize.z);
        vec3 restHead=(position-center)/radii,head=(p-center)/radii;
        float limit=min(length(restHead),1.02),distance=length(head);
        if(t>.28 && distance<limit && distance>.001)p=center+head*(limit/distance)*radii;
        vec3 direction=normalize(position+vec3(.001,.001,.001));
        n=normalize(normal-direction*dot(normal,bend)*w/max(uHeadSize.x*.35,.001));
      }
      p+=n*uOutline*smoothstep(.10,.40,t);vLockT=t;vLocal=p;vNormal=normalize(normalMatrix*n);
      gl_Position=projectionMatrix*modelViewMatrix*vec4(p,1.);}`;
  const shared={uTips:{value:tipOffsets},uHeadSize:{value:new THREE.Vector3(width,width,headDepth)}};
  const material=new THREE.ShaderMaterial({
    uniforms:{...shared,uOutline:{value:0},uHairColor:{value:browColor}},vertexShader:vertex,
    fragmentShader:`uniform vec3 uHairColor;varying vec3 vNormal;varying vec3 vLocal;varying float vLockT;
    void main(){vec3 n=normalize(vNormal);float l=max(0.,dot(n,normalize(vec3(-.55,.75,1.))));
      float spec=pow(max(0.,dot(n,normalize(vec3(-.25,.5,1.)))),14.);
      vec3 c=uHairColor*mix(.72,1.16,smoothstep(.05,.95,l));
      c+=uHairColor*vec3(.23,.18,.13)*spec;
      c*=.94+.06*smoothstep(.0,.30,vLockT);
      gl_FragColor=vec4(c,1.);
      #include <colorspace_fragment>
    }`
  });
  const ink=new THREE.ShaderMaterial({uniforms:{...shared,uOutline:{value:width*.0055}},vertexShader:vertex,
    fragmentShader:`void main(){gl_FragColor=vec4(.003,.001,.0003,1.);
      #include <colorspace_fragment>
    }`,side:THREE.BackSide});
  const pieces=[];
  const add=(geometry,id=-1)=>{
    geometry.computeVertexNormals();
    geometry.setAttribute('aLockId',new THREE.Float32BufferAttribute(Array(geometry.attributes.position.count).fill(id),1));
    pieces.push(geometry);
  };
  // A full continuous crown covers the fringe roots; only the tips emerge.
  const vertices=[],uvs=[],indices=[];const around=48,rows=20;
  for(let j=0;j<=rows;j++)for(let i=0;i<=around;i++){
    const phi=i/around*Math.PI*2;
    const front=(Math.cos(phi)+1)*.5;
    const theta=j/rows*(THREE.MathUtils.lerp(2.13,1.26,front)+.34*Math.pow(Math.sin(phi),2)+front*.035*Math.sin(phi*7));
    vertices.push(Math.sin(phi)*Math.sin(theta)*width*.66,
      Math.cos(theta)*width*THREE.MathUtils.lerp(.58,.66,front)-width*.10,
      Math.cos(phi)*Math.sin(theta)*headDepth*THREE.MathUtils.lerp(.58,.70,front));
    uvs.push(i/around,0);
    if(j<rows&&i<around){const a=j*(around+1)+i,b=a+around+1;indices.push(a,b,a+1,b,b+1,a+1);}
  }
  const cap=new THREE.BufferGeometry();cap.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));cap.setAttribute('uv',new THREE.Float32BufferAttribute(uvs,2));cap.setIndex(indices);add(cap);
  const V=p=>new THREE.Vector3(p[0]*width,p[1]*width,p[2]*headDepth);
  function lock(root,bend,end,w,thickness,blendRoot=true){
    const curve=new THREE.QuadraticBezierCurve3(V(root),V(bend),V(end));
    const pos=[],uv=[],idx=[];const rings=16,sides=12;
    const axis=new THREE.Vector3(0,0,1);
    for(let j=0;j<=rings;j++){
      const t=(1-Math.cos(j/rings*Math.PI))*.5,center=curve.getPoint(t),tangent=curve.getTangent(t).normalize();
      const side=new THREE.Vector3().crossVectors(tangent,axis).normalize();
      const deep=new THREE.Vector3().crossVectors(side,tangent).normalize();
      const rootScale=blendRoot ? Math.sin(Math.min(1,t/.30)*Math.PI*.5) : 1;
      const r=Math.max(.001,rootScale*(.94+.20*Math.sin(t*Math.PI))*Math.pow(1-t,.66));
      for(let k=0;k<=sides;k++){
        const a=k/sides*Math.PI*2;
        const p=center.clone().addScaledVector(side,Math.cos(a)*w*width*.5*r).addScaledVector(deep,Math.sin(a)*thickness*width*.5*r);
        pos.push(p.x,p.y,p.z);uv.push(k/sides,t);
        if(j<rings&&k<sides){const q=j*(sides+1)+k,b=q+sides+1;idx.push(q,b,q+1,b,b+1,q+1);}
      }
    }
    const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));g.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));g.setIndex(idx);const id=physics.length;add(g,id);
    // Join the duplicate UV seam normals to avoid a hard line along each lock.
    const normals=g.attributes.normal;
    for(let row=0;row<=rings;row++){
      const a=row*(sides+1),b=a+sides,n=new THREE.Vector3().fromBufferAttribute(normals,a).add(new THREE.Vector3().fromBufferAttribute(normals,b)).normalize();
      normals.setXYZ(a,n.x,n.y,n.z);normals.setXYZ(b,n.x,n.y,n.z);
    }
    physics.push({tip:V(end),length:curve.getLength(),last:null,offset:new THREE.Vector3(),velocity:new THREE.Vector3(),stiffness:85+id%4*12});
  }
  // Front-view silhouette follows the reference: long left temple lock,
  // two diagonal central blades and a short right temple blade.
  lock([-.29,.35,.19],[-.43,.12,.65],[-.40,-.23,.57],.21,.16,true);
  lock([.03,.39,.15],[-.09,.17,.67],[-.23,-.20,.68],.39,.18);
  lock([.27,.36,.13],[.23,.15,.68],[.065,-.14,.69],.34,.18);
  lock([.42,.26,.13],[.45,.08,.63],[.33,-.23,.63],.27,.17);
  // Crown masses sweep right, with broad shoulders and long tapered tips.
  // Bury full-width roots in the cap so the crown has no pinched cutouts.
  lock([-.34,.06,-.12],[-.56,.39,-.02],[-.43,.72,.01],.48,.26,false);
  lock([-.23,.15,-.16],[.03,.63,-.10],[.43,.88,-.04],.60,.27,false);
  lock([.12,.13,-.23],[.46,.51,-.14],[.78,.67,-.06],.52,.27,false);
  // Stepped side tufts continue around the back of the skull.
  lock([-.43,.12,-.13],[-.65,.15,-.16],[-.78,.34,-.13],.27,.23,false);
  lock([-.46,-.05,-.10],[-.59,-.18,-.09],[-.75,-.23,-.03],.21,.21);
  lock([-.44,-.15,-.17],[-.54,-.31,-.20],[-.67,-.41,-.12],.23,.21);
  // Two broad swept masses replace the three radial, needle-like side spikes.
  lock([.35,.14,-.20],[.62,.14,-.17],[.84,.27,-.14],.40,.25,false);
  lock([.37,-.08,-.23],[.57,-.20,-.27],[.75,-.36,-.20],.42,.26,false);
  // Continuous temple locks follow the sides of the skull and end just
  // above the ear; their roots overlap the front and rear layers in depth.
  lock([-.35,.24,.15],[-.55,-.06,.38],[-.51,-.36,.26],.31,.22,true);
  lock([.41,.26,.24],[.54,-.05,.37],[.49,-.37,.28],.28,.20);
  // Embedded connecting locks merge crown and temples.
  lock([-.23,.34,.03],[-.39,.26,.34],[-.39,.04,.49],.28,.22,true);
  lock([.27,.29,-.02],[.48,.16,.25],[.52,-.075,.35],.36,.24);
  // One surface draw and one ink draw for the entire hairstyle.
  const merged=new THREE.BufferGeometry(),combined={position:[],normal:[],uv:[],aLockId:[]},index=[];
  let vertexOffset=0;
  for(const g of pieces){
    for(const key of Object.keys(combined))combined[key].push(...g.attributes[key].array);
    for(const i of g.index.array)index.push(i+vertexOffset);
    vertexOffset+=g.attributes.position.count;g.dispose();
  }
  for(const [key,data] of Object.entries(combined))merged.setAttribute(key,new THREE.Float32BufferAttribute(data,key==='uv'?2:key==='aLockId'?1:3));
  merged.setIndex(index);merged.computeBoundingSphere();
  group.add(new THREE.Mesh(merged,ink),new THREE.Mesh(merged,material));
  const point=new THREE.Vector3(),movement=new THREE.Vector3(),rotation=new THREE.Quaternion(),inverseRotation=new THREE.Quaternion();
  group.userData.step=(dt)=>{
    group.updateWorldMatrix(true,false);group.getWorldQuaternion(rotation);inverseRotation.copy(rotation).invert();
    const steps=Math.max(1,Math.ceil(dt*120)),h=dt/steps;
    for(let i=0;i<physics.length;i++){
      const p=physics[i];point.copy(p.tip).applyMatrix4(group.matrixWorld);
      // Rest-tip motion includes both translation and head rotation.
      if(p.last)p.offset.addScaledVector(movement.copy(p.last).sub(point),.60);
      else p.last=new THREE.Vector3();p.last.copy(point);
      for(let j=0;j<steps;j++){
        p.velocity.addScaledVector(p.offset,-p.stiffness*h);
        p.velocity.y-=width*.025*h;
        p.velocity.multiplyScalar(Math.exp(-8.5*h));
        p.offset.addScaledVector(p.velocity,h).clampLength(0,Math.min(width*.045,p.length*.11));
      }
      tipOffsets[i].copy(p.offset).applyQuaternion(inverseRotation);
    }
  };
  group.userData.geometryStats={vertices:group.children.filter(m=>m.material===material).reduce((n,m)=>n+m.geometry.attributes.position.count,0)};
  return group;
}
