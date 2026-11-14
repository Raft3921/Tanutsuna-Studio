import * as THREE from './vendor/three.module.js';

// A single silhouette from the final alpha, so adjoining body parts do not
// acquire separate borders. Hair's internal lines remain in the source image.
export function createWholeOutline(renderer) {
  const target=new THREE.WebGLRenderTarget(1,1,{stencilBuffer:true,depthBuffer:true});
  target.samples=2;
  const uniforms={uImage:{value:target.texture},uStep:{value:new THREE.Vector2()},uInk:{value:new THREE.Color('#100c0a')}};
  const material=new THREE.ShaderMaterial({uniforms,depthTest:false,depthWrite:false,transparent:true,
    vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position.xy,0.,1.);}',
    fragmentShader:`uniform sampler2D uImage;uniform vec2 uStep;uniform vec3 uInk;varying vec2 vUv;
      void main(){vec4 source=texture2D(uImage,vUv);float silhouette=source.a;
        for(int i=0;i<12;i++){
          float a=float(i)*6.2831853/12.;vec2 d=vec2(cos(a),sin(a))*uStep;
          silhouette=max(silhouette,texture2D(uImage,vUv+d).a);
          silhouette=max(silhouette,texture2D(uImage,vUv+d*.5).a);
        }
        float border=max(0.,silhouette-source.a);
        vec3 color=(source.rgb+uInk*border)/max(silhouette,.0001);
        gl_FragColor=vec4(color,silhouette);
        #include <colorspace_fragment>
      }`
  });
  const scene=new THREE.Scene();scene.add(new THREE.Mesh(new THREE.PlaneGeometry(2,2),material));
  const camera=new THREE.Camera(),size=new THREE.Vector2();
  function resize(){renderer.getDrawingBufferSize(size);target.setSize(size.x,size.y);const radius=2.8*renderer.getPixelRatio();uniforms.uStep.value.set(radius/size.x,radius/size.y);}
  resize();
  return {resize,render(sourceScene,sourceCamera){
    renderer.setRenderTarget(target);renderer.clear();renderer.render(sourceScene,sourceCamera);
    renderer.setRenderTarget(null);renderer.clear();renderer.render(scene,camera);
  }};
}
