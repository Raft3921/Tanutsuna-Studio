import * as THREE from './vendor/three.module.js';
import { FBXLoader } from './vendor/FBXLoader.js';


// Refresh dependent modules together with app.js on every page reload.
const moduleRevision = new URL(import.meta.url).searchParams.get('revision') || String(Date.now());
const [{ createOutfit, shirtMaterial, outlineCloth }, { createWholeOutline }, { createHairstyle }] = await Promise.all([
  import(`./outfit.js?revision=${moduleRevision}`),
  import(`./outline.js?revision=${moduleRevision}`),
  import(`./hair.js?revision=${moduleRevision}`)
]);

const host = document.querySelector('#canvas-host');
const statusText = document.querySelector('#status-text');
const indicator = document.querySelector('#status-indicator');
const errorBox = document.querySelector('#error');

const scene = new THREE.Scene();
const initialWidth = Math.max(1, host.clientWidth, innerWidth);
const initialHeight = Math.max(1, host.clientHeight, innerHeight);
const camera = new THREE.PerspectiveCamera(32, initialWidth / initialHeight, 0.01, 1000);
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, stencil: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(initialWidth, initialHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.NoToneMapping;
renderer.toneMappingExposure = 1;
host.appendChild(renderer.domElement);
const wholeOutline = createWholeOutline(renderer);

const skinBase = new THREE.Color('#F5E2CD');
const skinShadow = new THREE.Color('#D59E8D');
const shadowRatio = new THREE.Vector3(
  skinShadow.r / skinBase.r,
  skinShadow.g / skinBase.g,
  skinShadow.b / skinBase.b
);
const spotPosition = new THREE.Vector3(-2.8, 3.2, 3.6);
const spotTarget = new THREE.Vector3(0, 0.25, 0);
const spotDirection = spotTarget.clone().sub(spotPosition).normalize();
const spotAngle = Math.PI / 4;
const spotPenumbra = 0.22;

const avatar = new THREE.Group();
scene.add(avatar);
function createInkOutline(geometry, width) {
  if (!geometry.boundingBox) geometry.computeBoundingBox();
  if (!geometry.attributes.normal) geometry.computeVertexNormals();
  const size = geometry.boundingBox.getSize(new THREE.Vector3());
  const frequency = new THREE.Vector3(
    Math.PI * 5 / Math.max(size.x, width),
    Math.PI * 5 / Math.max(size.y, width),
    Math.PI * 5 / Math.max(size.z, width)
  );
  return new THREE.ShaderMaterial({
    uniforms: {
      uInkWidth: { value: width },
      uInkFrequency: { value: frequency },
      uInkColor: { value: new THREE.Color(0x17141a) }
    },
    vertexShader: `
      uniform float uInkWidth;
      uniform vec3 uInkFrequency;
      void main() {
        float waveA = sin(dot(position, uInkFrequency));
        float waveB = sin(dot(position, uInkFrequency.yzx) * 0.73 + 1.7);
        float inkVariation = 1.0 + waveA * 0.18 + waveB * 0.08;
        vec3 inkPosition = position + normalize(normal) * uInkWidth * inkVariation;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(inkPosition, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 uInkColor;
      void main() { gl_FragColor = vec4(uInkColor, 1.0); }
    `,
    side: THREE.BackSide,
    depthTest: true,
    depthWrite: true
  });
}
let model = null;
window.avatarError = '';
let headPivot = null;
let outfit3D = null;
let bodySwayPivot = null;
let bodySwayStarted = 0;
let bodyShirt = null;
const bodyRestMatrix = new THREE.Matrix4();
const bodyInverseMatrix = new THREE.Matrix4();
let outfitPhysicsTime = 0;
let hairAnchor = null;
let hairCollider = null;
let hairPhysicsTime = 0;
const hairCollisionInverse = new THREE.Matrix4();
const hairVertex = new THREE.Vector3();
const hairWorldVertex = new THREE.Vector3();
const hairMeshInverse = new THREE.Matrix4();
let hairSprite = null;
const hairLayers = [];
const hairCenter = new THREE.Vector3();
const hairMotion = { time: { value: 0 }, sway: { value: 0 } };
let hairPreviousYaw = 0;
let hairWidth = 0;
const hairRestQuaternion = new THREE.Quaternion();
const hairRestInverse = new THREE.Quaternion();
const hairCurrentQuaternion = new THREE.Quaternion();
const hairRelativeQuaternion = new THREE.Quaternion();
const hairBlendQuaternion = new THREE.Quaternion();
const hairIdentityQuaternion = new THREE.Quaternion();
let modelRadius = 1.5;
let landmarker = null;
let active = false;
let lastVideoTime = -1;
let targetZoom = 3.8;
let targetHead = new THREE.Euler(-0.10, 0, 0, 'ZYX');
let keyboardBodyYaw = null;
addEventListener('keydown', event => {
  if (event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable="true"]')) return;
  if (event.key !== ';' && event.key !== ':') return;
  event.preventDefault();
  const yaw = event.key === ';' ? .45 : -.45;
  // Pressing the selected direction again returns the whole avatar to the front.
  keyboardBodyYaw = keyboardBodyYaw === yaw ? null : yaw;
});
let targetGaze = { x: 0, y: 0 };
let gazeCalibration = { started: 0, count: 0, x: 0, y: 0, ready: false };
const faceMotion = { blink: 0, mouthState: 0, pupilX: 0, pupilY: 0 };
const faceUniforms = [];
const eyeAnimation = { state: 0, from: 0, to: 0, started: 0, duration: 160 };
let smoothedBlink = 0;
let halfEyeSince = 0;
const mouthAnimation = { state: 0, from: 0, to: 0, started: 0, duration: 75 };
let mouthQuietSince = 0;

function classifyMouth(jaw, lipGap, pucker, stretch, smile, wasOpen) {
  const opening = Math.max(jaw * 2.5, lipGap * 1.8);
  if (opening <= (wasOpen ? 0.04 : 0.065)) return 0;
  if (pucker > 0.18) return opening > 0.32 ? 5 : 3;
  if (stretch > 0.20 || smile > 0.28) return opening > 0.28 ? 4 : 2;
  return 1;
}

function advanceExpression(animation, desired, now, stepwise = false) {
  if (animation.from !== animation.to && now - animation.started >= animation.duration) {
    animation.state = animation.to;
    animation.from = animation.to;
  }
  if (animation.from === animation.to && animation.state !== desired) {
    animation.from = animation.state;
    animation.to = stepwise ? animation.state + Math.sign(desired - animation.state) : desired;
    animation.started = now;
  }
  const t = animation.from === animation.to ? 0 : THREE.MathUtils.clamp((now - animation.started) / animation.duration, 0, 1);
  return { from: animation.from, to: animation.to, progress: t * t * (3 - 2 * t) };
}

function advanceMouthExpression(desired, now) {
  // Keep the last open shape through brief gaps in camera tracking, and blend
  // directly between vowel shapes. Only close after a sustained quiet period.
  if (desired !== 0) {
    mouthQuietSince = 0;
    if (mouthAnimation.to === 0 && mouthAnimation.from !== mouthAnimation.to) {
      mouthAnimation.from = mouthAnimation.state;
      mouthAnimation.to = desired;
      mouthAnimation.started = now;
    }
    return advanceExpression(mouthAnimation, desired, now);
  }
  if (!mouthQuietSince) mouthQuietSince = now;
  const held = mouthAnimation.from !== mouthAnimation.to ? mouthAnimation.to : mouthAnimation.state;
  if (held !== 0 && now - mouthQuietSince < 120) {
    return advanceExpression(mouthAnimation, held, now);
  }
  return advanceExpression(mouthAnimation, 0, now);
}

function textureBounds(texture, left, right, top, bottom) {
  const image = texture.image;
  const fullTexture = () => ({
    center: new THREE.Vector2(.5, .5),
    size: new THREE.Vector2(1, 1)
  });
  // The avatar is displayed before its face art finishes loading. During that
  // interval the 1x1 placeholder textures cannot contain the atlas regions
  // requested below. Keep valid UV bounds so the first render frames can run.
  if (!image || image.width < right || image.height < bottom) return fullTexture();
  const canvas = document.createElement('canvas');
  canvas.width = image.width; canvas.height = image.height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(image, 0, 0);
  const pixels = ctx.getImageData(left, top, right - left, bottom - top).data;
  let minX = right, minY = bottom, maxX = -1, maxY = -1;
  for (let y = 0; y < bottom - top; y++) {
    for (let x = 0; x < right - left; x++) {
      if (pixels[(y * (right - left) + x) * 4 + 3] < 40) continue;
      minX = Math.min(minX, x + left); maxX = Math.max(maxX, x + left);
      minY = Math.min(minY, y + top); maxY = Math.max(maxY, y + top);
    }
  }
  // A transparent/missing expression still needs a valid source rectangle;
  // otherwise the render loop tries to read `.center` from null.
  if (maxX < 0) return fullTexture();
  return { center: new THREE.Vector2((minX + maxX + 1) / (2 * image.width), 1 - (minY + maxY + 1) / (2 * image.height)), size: new THREE.Vector2((maxX - minX + 1) / image.width, (maxY - minY + 1) / image.height) };
}

function morphBounds(from, to, progress, sourceCenter, displayCenter, scale) {
  sourceCenter.copy(from.center);
  displayCenter.copy(from.center).lerp(to.center, progress);
  scale.set(from.size.x / THREE.MathUtils.lerp(from.size.x, to.size.x, progress), from.size.y / THREE.MathUtils.lerp(from.size.y, to.size.y, progress));
}

let nextAutoBlink = performance.now() + 2400 + Math.random() * 2200;
let autoBlinkUntil = 0;
let mouthCandidate = 0;
let mouthCandidateSince = 0;
let mouthLastChange = 0;

function setStatus(label, mode = '') {
  if (statusText) statusText.textContent = label;
  if (indicator) indicator.className = `status-indicator ${mode}`;
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.add('is-visible');
}

function makeHeadPivot(root, part) {
  if (!part || part === root || part.isSkinnedMesh) return null;
  root.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(part);
  if (bounds.isEmpty()) return null;
  const size = bounds.getSize(new THREE.Vector3());
  const pivotWorld = bounds.getCenter(new THREE.Vector3());
  // Raise the tracked head slightly above its original resting placement.
  pivotWorld.y += size.y * 0.045;
  // Place the hinge near the neck: low in the head and slightly behind its center.
  pivotWorld.y = bounds.min.y + size.y * 0.13;
  pivotWorld.z -= size.z * 0.12;
  const parent = part.parent;
  const pivotParent = parent.worldToLocal(pivotWorld.clone());
  const pivot = new THREE.Group();
  pivot.name = 'Head Tracking Pivot';
  pivot.rotation.order = 'ZYX';
  pivot.position.copy(pivotParent);
  parent.add(pivot);
  pivot.attach(part);
  return pivot;
}

function skinLightingUniforms() {
  return {
    uShadowRatio: { value: shadowRatio.clone() },
    uLightPosition: { value: spotPosition.clone() },
    uSpotDirection: { value: spotDirection.clone() },
    uSpotOuter: { value: Math.cos(spotAngle) },
    uSpotInner: { value: Math.cos(spotAngle * (1 - spotPenumbra)) }
  };
}

const skinVertexShader = `
  varying vec3 vWorldPosition;
  varying vec3 vWorldNormal;
  varying vec2 vFaceUv;
  void main() {
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    vWorldPosition = worldPosition.xyz;
    vWorldNormal = normalize(mat3(modelMatrix) * normal);
    vFaceUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

function skinFragmentHeader() {
  return `
    #include <common>
    uniform vec3 uShadowRatio;
    uniform vec3 uLightPosition;
    uniform vec3 uSpotDirection;
    uniform float uSpotOuter;
    uniform float uSpotInner;
    varying vec3 vWorldPosition;
    varying vec3 vWorldNormal;
    varying vec2 vFaceUv;
    float skinLightAmount() {
      vec3 normalDirection = normalize(vWorldNormal);
      vec3 toLight = normalize(uLightPosition - vWorldPosition);
      float diffuse = max(dot(normalDirection, toLight), 0.0);
      return diffuse;
    }
    vec3 shadeSkin(vec3 color) {
      float light = skinLightAmount();
      float edge = max(fwidth(light) * 1.25, 0.006);
      float litBand = smoothstep(0.48 - edge, 0.48 + edge, light);
      float gradient = smoothstep(0.02, 0.98, light);
      // A distinct shadow boundary with soft modelling inside both regions.
      float shadowStrength = (1.0 - litBand) * 0.72 + (1.0 - gradient) * 0.24;
      return color * mix(vec3(1.0), uShadowRatio, shadowStrength) * (0.985 + gradient * 0.015);
    }
  `;
}

function createBodyMaterial() {
  return new THREE.ShaderMaterial({
    uniforms: { ...skinLightingUniforms(), uBaseColor: { value: skinBase.clone() } },
    vertexShader: skinVertexShader,
    fragmentShader: `${skinFragmentHeader()}
      uniform vec3 uBaseColor;
      void main() {
        gl_FragColor = vec4(shadeSkin(uBaseColor), 1.0);
        #include <colorspace_fragment>
      }
    `,
    side: THREE.DoubleSide
  });
}

function createFaceMaterial(layers) {
  const uniforms = {
    ...skinLightingUniforms(),
    uBaseMap: { value: layers.base },
    uEyesOpenMap: { value: layers.eyesOpen },
    uPupilsMap: { value: layers.pupils },
    uEyeMaskMap: { value: layers.eyeMask },
    uEyebrowsMap: { value: layers.eyebrows },
    uEyesHalfMap: { value: layers.eyesHalf },
    uEyesClosedMap: { value: layers.eyesClosed },
    uMouthMaps: { value: [layers.mouthClosed, layers.mouthA, layers.mouthI, layers.mouthU, layers.mouthE, layers.mouthO] },
    uEyeUvScale: { value: new THREE.Vector2(1.32, 1.1) },
    uEyeUvOffset: { value: new THREE.Vector2(0, -0.035) },
    uBrowUvScale: { value: new THREE.Vector2(1.12, 1.0) },
    uMouthUvOffsetV: { value: -0.22 },
    uMouthInk: { value: new THREE.Color('#1b100e') },
    uMouthInterior: { value: new THREE.Color('#76242b') },
    uMouthTongue: { value: new THREE.Color('#a8494b') },
    uPupilOffset: { value: new THREE.Vector2() },
    uIrisPulse: { value: 0 },
    uEyeState: { value: 0 },
    uEyeSourceLeft: { value: new THREE.Vector2() }, uEyeSourceRight: { value: new THREE.Vector2() },
    uEyeDisplayLeft: { value: new THREE.Vector2() }, uEyeDisplayRight: { value: new THREE.Vector2() },
    uEyeScaleLeft: { value: new THREE.Vector2(1, 1) }, uEyeScaleRight: { value: new THREE.Vector2(1, 1) },
    uMouthState: { value: 0 },
    uMouthStateTo: { value: 0 },
    uMouthProgress: { value: 0 },
    uMouthCenterV: { value: 1 - 1545.5 / 2048 },
    uMouthUvScale: { value: 2.7 }
  };
  const material = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: skinVertexShader,
    fragmentShader: `${skinFragmentHeader()}
      uniform sampler2D uBaseMap;
      uniform sampler2D uEyesOpenMap;
      uniform sampler2D uPupilsMap;
      uniform sampler2D uEyeMaskMap;
      uniform sampler2D uEyebrowsMap;
      uniform sampler2D uEyesHalfMap;
      uniform sampler2D uEyesClosedMap;
      uniform sampler2D uMouthMaps[6];
      uniform vec2 uEyeUvScale;
      uniform vec2 uEyeUvOffset;
      uniform vec2 uBrowUvScale;
      uniform float uMouthUvOffsetV;
      uniform vec3 uMouthInk;
      uniform vec3 uMouthInterior;
      uniform vec3 uMouthTongue;
      uniform vec2 uPupilOffset;
      uniform float uIrisPulse;
      uniform float uEyeState;
      uniform vec2 uEyeSourceLeft; uniform vec2 uEyeSourceRight;
      uniform vec2 uEyeDisplayLeft; uniform vec2 uEyeDisplayRight;
      uniform vec2 uEyeScaleLeft; uniform vec2 uEyeScaleRight;
      uniform float uMouthState;
      uniform float uMouthStateTo;
      uniform float uMouthProgress;
      uniform float uMouthCenterV;
      uniform float uMouthUvScale;
      void main() {
        vec3 color = texture2D(uBaseMap, vFaceUv).rgb;
        vec2 eyeUv = (vFaceUv - 0.5) * uEyeUvScale + 0.5 + uEyeUvOffset;
        vec2 browUv = (vFaceUv - 0.5) * uBrowUvScale + 0.5 + vec2(0.0, 0.02);
        bool leftEye = eyeUv.x < 0.5;
        vec2 sourceCenter = leftEye ? uEyeSourceLeft : uEyeSourceRight;
        vec2 displayCenter = leftEye ? uEyeDisplayLeft : uEyeDisplayRight;
        vec2 eyeScale = leftEye ? uEyeScaleLeft : uEyeScaleRight;
        vec2 eyeSampleUv = sourceCenter + (eyeUv - displayCenter) * eyeScale;
        vec4 eyeImage = texture2D(uEyesOpenMap, eyeSampleUv);
        if (uEyeState > 0.5) eyeImage = texture2D(uEyesHalfMap, eyeSampleUv);
        if (uEyeState > 1.5) eyeImage = texture2D(uEyesClosedMap, eyeSampleUv);
        vec2 irisAlignment = vec2(eyeUv.x < 0.5 ? 0.021 : -0.022, 0.033);
        vec2 irisCenter = vec2(leftEye ? 0.259 : 0.740, 0.460);
        vec2 irisUv = eyeUv + irisAlignment - uPupilOffset;
        irisUv = irisCenter + (irisUv - irisCenter) / vec2(1.0 + uIrisPulse * 0.06, 1.0 + uIrisPulse * 0.14);
        vec4 iris = texture2D(uPupilsMap, irisUv);
        // The eye artwork is editable independently of the old mask. Its white
        // fill defines the visible pupil area and keeps the liner in front.
        float eyeClip = eyeImage.a * smoothstep(0.65, 0.85, min(eyeImage.r, min(eyeImage.g, eyeImage.b)));
        color = mix(color, eyeImage.rgb, eyeImage.a);
        float irisVisible = iris.a * eyeClip * (1.0 - step(1.5, uEyeState));
        color = mix(color, iris.rgb, irisVisible);
        float irisCatchlight = irisVisible * smoothstep(0.58, 0.73, min(iris.r, min(iris.g, iris.b)));
        vec4 brows = texture2D(uEyebrowsMap, browUv);
        color = mix(color, brows.rgb, brows.a);
        vec2 mouthUv = vec2(vFaceUv.x, uMouthCenterV + (vFaceUv.y - uMouthCenterV) * uMouthUvScale + uMouthUvOffsetV);
        float stateFrom = floor(clamp(uMouthState, 0.0, 5.0) + 0.5);
        float stateTo = floor(clamp(uMouthStateTo, 0.0, 5.0) + 0.5);
        vec4 mouthFrom = texture2D(uMouthMaps[0], mouthUv);
        if (stateFrom > 0.5) mouthFrom = texture2D(uMouthMaps[1], mouthUv);
        if (stateFrom > 1.5) mouthFrom = texture2D(uMouthMaps[2], mouthUv);
        if (stateFrom > 2.5) mouthFrom = texture2D(uMouthMaps[3], mouthUv);
        if (stateFrom > 3.5) mouthFrom = texture2D(uMouthMaps[4], mouthUv);
        if (stateFrom > 4.5) mouthFrom = texture2D(uMouthMaps[5], mouthUv);
        vec4 mouthTo = texture2D(uMouthMaps[0], mouthUv);
        if (stateTo > 0.5) mouthTo = texture2D(uMouthMaps[1], mouthUv);
        if (stateTo > 1.5) mouthTo = texture2D(uMouthMaps[2], mouthUv);
        if (stateTo > 2.5) mouthTo = texture2D(uMouthMaps[3], mouthUv);
        if (stateTo > 3.5) mouthTo = texture2D(uMouthMaps[4], mouthUv);
        if (stateTo > 4.5) mouthTo = texture2D(uMouthMaps[5], mouthUv);
        float mouthFromLight = dot(mouthFrom.rgb, vec3(0.2126, 0.7152, 0.0722));
        float mouthToLight = dot(mouthTo.rgb, vec3(0.2126, 0.7152, 0.0722));
        mouthFrom.rgb = mouthFromLight < 0.035 ? uMouthInk : (mouthFromLight < 0.22 ? uMouthInterior : uMouthTongue);
        mouthTo.rgb = mouthToLight < 0.035 ? uMouthInk : (mouthToLight < 0.22 ? uMouthInterior : uMouthTongue);
        vec4 mouth = mix(mouthFrom, mouthTo, clamp(uMouthProgress, 0.0, 1.0));
        color = mix(color, mouth.rgb, mouth.a);
        vec3 shadedColor = shadeSkin(color);
        vec3 brightCatchlight = mix(iris.rgb, vec3(1.0), 0.42);
        gl_FragColor = vec4(mix(shadedColor, brightCatchlight, irisCatchlight), 1.0);
        #include <colorspace_fragment>
      }
    `,
    side: THREE.DoubleSide
  });
  material.stencilWrite = true;
  material.stencilRef = 1;
  material.stencilFunc = THREE.AlwaysStencilFunc;
  material.stencilZPass = THREE.ReplaceStencilOp;
  const eyeMaps = [layers.eyesOpen, layers.eyesHalf, layers.eyesClosed];
  const eyeBounds = eyeMaps.map(texture => texture.userData.missingBlink ? null : [textureBounds(texture, 0, 1024, 650, 1500), textureBounds(texture, 1024, 2048, 650, 1500)]);
  if (!eyeBounds[2]) eyeBounds[2] = eyeBounds[1];
  for (let i = 0; i < eyeBounds.length; i++) {
    for (let side = 0; side < 2; side++) eyeBounds[i][side] ||= eyeBounds[0][side];
  }
  faceUniforms.push({ uniforms, eyeBounds, hasClosedEyes: !layers.eyesClosed.userData.missingBlink });
  return material;
}

// A fresh URL on each page load makes locally edited images visible after reload,
// even when the browser or the local server caches the previous file.
const assetRevision = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
function imageAssetUrl(file) {
  return `./assets/${encodeURIComponent(file)}?revision=${assetRevision}`;
}

const FACE_TEXTURE_FILES = {
  base: 'face-base.png', eyesOpen: 'face-eyes-open.png', pupils: 'face-pupils.png',
  eyeMask: 'face-eye-mask.png', eyebrows: 'face-eyebrows.png', eyesHalf: 'face-eyes-half.png', eyesClosed: 'face-eyes-closed.png',
  mouthClosed: 'face-mouth-closed.png', mouthA: 'face-mouth-a.png', mouthI: 'face-mouth-i.png',
  mouthU: 'face-mouth-u.png', mouthE: 'face-mouth-e.png', mouthO: 'face-mouth-o.png'
};

function makeFallbackFaceTexture(key) {
  const fallback = key === 'base' ? [245, 226, 205, 255] : [0, 0, 0, 0];
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 1;
  const context = canvas.getContext('2d');
  context.fillStyle = `rgba(${fallback[0]},${fallback[1]},${fallback[2]},${fallback[3] / 255})`;
  context.fillRect(0, 0, 1, 1);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = key === 'eyeMask' ? THREE.NoColorSpace : THREE.SRGBColorSpace;
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.generateMipmaps = true;
  texture.needsUpdate = true;
  if (key === 'eyesClosed') texture.userData.missingBlink = true;
  return texture;
}

function makeFallbackFaceTextures() {
  return Object.fromEntries(Object.keys(FACE_TEXTURE_FILES).map(key => [key, makeFallbackFaceTexture(key)]));
}

async function loadFaceTextures() {
  const loader = new THREE.TextureLoader();
  const warnings = [];
  let forceFallbackResolver;
  const forceFallbackSignal = new Promise(resolve => { forceFallbackResolver = resolve; });
  const makeFallback = (key, file, error) => {
    warnings.push(`${file}: ${error && error.message || error}`);
    return makeFallbackFaceTexture(key);
  };
  window.forceAvatarTextureFallback = () => {
    window.avatarStatus = '顔画像を代替してモデルを表示しています';
    forceFallbackResolver?.(true);
    return true;
  };
  const textureLoads = Promise.all(Object.entries(FACE_TEXTURE_FILES).map(async ([key, file]) => {
    let texture;
    let timeout;
    try {
      texture = await Promise.race([
        loader.loadAsync(imageAssetUrl(file)),
        new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('画像読み込みがタイムアウトしました')), 8000); })
      ]);
    } catch (error) {
      texture = makeFallback(key, file, error);
      console.warn('顔テクスチャの代替表示を使います:', file, error);
    } finally {
      clearTimeout(timeout);
    }
    texture.colorSpace = key === 'eyeMask' ? THREE.NoColorSpace : THREE.SRGBColorSpace;
    texture.wrapS = THREE.ClampToEdgeWrapping;
    texture.wrapT = THREE.ClampToEdgeWrapping;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.magFilter = THREE.LinearFilter;
    texture.generateMipmaps = true;
    texture.needsUpdate = true;
    return [key, texture];
  }));
  const result = await Promise.race([
    textureLoads.then(entries => ({ entries })),
    forceFallbackSignal.then(() => ({ forced: true }))
  ]);
  const entries = result.forced
    ? Object.entries(FACE_TEXTURE_FILES).map(([key, file]) => [key, makeFallback(key, file, new Error('画像読み込みを省略しました'))])
    : result.entries;
  window.avatarTextureWarnings = warnings;
  return Object.fromEntries(entries);
}

function showModelError(error) {
  console.error(error);
  window.avatarError = String(error && error.message || error);
  showError(`モデルを読み込めませんでした: ${error.message || error}`);
  setStatus('モデルを読み込めません');
}

window.avatarStatus = 'FBXモデルを読み込み中';
new FBXLoader().load('./assets/Sitting_Chibi.fbx', object => {
  window.avatarStatus = 'モデルのテクスチャを読み込み中';
  initializeAvatar(object).catch(showModelError);
}, event => {
  if (event.lengthComputable) {
    window.avatarStatus = `FBXモデルを読み込み中 ${Math.round(event.loaded / event.total * 100)}%`;
  }
}, showModelError);

async function initializeAvatar(object) {
  const faceTextures = makeFallbackFaceTextures();
  model = object;
  let faceMaterial = createFaceMaterial(faceTextures);
  const bodyMaterial = createBodyMaterial();
  object.traverse(node => {
    if (node.isMesh) {
      node.frustumCulled = false;
      const isHead = node.name.toLowerCase().includes('head');
      node.visible = true;
      const material = isHead ? faceMaterial : bodyMaterial;
      node.material = Array.isArray(node.material)
        ? node.material.map(() => material)
        : material;
    }
  });
  object.updateMatrixWorld(true);

  // Find the independently exported head mesh. Its center becomes the tracking pivot.
  let headPart = null;
  let namedHeadGroup = null;
  object.traverse(node => {
    // This FBX has a separate ear-only branch; the head mesh already includes its ears.
    if (node.name.toLowerCase().includes('ear')) {
      node.visible = false;
      return;
    }
    if (node.name.toLowerCase().includes('head')) {
      if (node.isMesh) headPart = node;
      else namedHeadGroup = node;
    }
  });
  headPart ||= namedHeadGroup;
  // The anchor follows the head, while the sprite itself always faces the camera.
  const headBounds = headPart ? new THREE.Box3().setFromObject(headPart) : null;
  const headSize = headBounds && !headBounds.isEmpty() ? headBounds.getSize(new THREE.Vector3()) : null;
  if (headSize) {
    const anchorWorld = headBounds.getCenter(new THREE.Vector3());
    anchorWorld.y += headSize.y * 0.14;
    // Anchoring at the center avoids the large sideways swing of a point on
    // the front of the head when the head turns.
    hairAnchor = new THREE.Object3D();
    hairAnchor.position.copy(headPart.worldToLocal(anchorWorld));
    headPart.add(hairAnchor);
  }
  if (headSize) {
    // Ellipsoid enclosing the cranium; the ears are excluded from its width.
    hairCollider = new THREE.Object3D();
    hairCollider.position.copy(headBounds.getCenter(new THREE.Vector3()));
    hairCollider.scale.set(headSize.x * .43, headSize.y * .51, headSize.z * .53);
    scene.add(hairCollider);
    headPart.attach(hairCollider);
  }
  headPivot = makeHeadPivot(object, headPart);

  const bounds = new THREE.Box3().setFromObject(object);
  const size = bounds.getSize(new THREE.Vector3());
  const center = bounds.getCenter(new THREE.Vector3());
  const scale = 2.2 / Math.max(size.y, size.x, size.z);
  modelRadius = Math.max(size.x, size.y, size.z) * scale / 2;
  object.scale.setScalar(scale);
  object.position.sub(center.multiplyScalar(scale));
  avatar.add(object);
  targetZoom = Math.max(3.4, size.y * scale / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))) * 1.35);
  targetZoom *= 1.10;
  camera.position.set(0, modelRadius * 0.16 + 0.32, targetZoom);
  camera.lookAt(0, 0.32, 0);
  object.updateMatrixWorld(true);
  const dressedBodyBounds=new THREE.Box3();
  const bodyMeshes=[];
  object.traverse(node=>{if(node.isMesh && node.visible && !node.name.toLowerCase().includes('head')){dressedBodyBounds.expandByObject(node);bodyMeshes.push(node);}});
  if(!dressedBodyBounds.isEmpty()){
    const shirt=shirtMaterial(dressedBodyBounds,new THREE.Box3().setFromObject(headPart));
    bodyShirt=shirt;
    for(const mesh of bodyMeshes)mesh.material=shirt;
    outfit3D=createOutfit(dressedBodyBounds,new THREE.Box3().setFromObject(headPart));
    scene.add(outfit3D);
  }
  if (hairAnchor && headSize) {
    // Use the dominant opaque ink in the editable eyebrow texture.
    const browCanvas = document.createElement('canvas');
    browCanvas.width=faceTextures.eyebrows.image.width; browCanvas.height=faceTextures.eyebrows.image.height;
    const browContext=browCanvas.getContext('2d',{willReadFrequently:true});
    browContext.drawImage(faceTextures.eyebrows.image,0,0);
    const pixels=browContext.getImageData(0,0,browCanvas.width,browCanvas.height).data;
    const palette=new Map();let dominant=0x2b1a11,most=0;
    for(let i=0;i<pixels.length;i+=16){if(pixels[i+3]<240)continue;const key=(pixels[i]<<16)|(pixels[i+1]<<8)|pixels[i+2];const count=(palette.get(key)||0)+1;palette.set(key,count);if(count>most){most=count;dominant=key;}}
    const hair = createHairstyle(headSize.x * scale * .76, headSize.z * scale, new THREE.Color(dominant));
    hairWidth = headSize.x * scale * .76;
    hairLayers.push({mesh:hair,turn:1,shift:0,offset:new THREE.Vector3(),localRotation:new THREE.Quaternion()});
    scene.add(hair);hairSprite=hair;
    hairAnchor.getWorldQuaternion(hairRestQuaternion);
    hairRestInverse.copy(hairRestQuaternion).invert();
  }
  // Keep the lower edge stationary while the dressed body gently rocks.
  bodySwayPivot = new THREE.Group();
  bodySwayPivot.name = '胴体底辺を支点にした左右の揺れ';
  const swayBounds = outfit3D ? dressedBodyBounds : new THREE.Box3().setFromObject(avatar);
  bodySwayPivot.position.copy(swayBounds.getCenter(new THREE.Vector3()));
  bodySwayPivot.position.y = swayBounds.min.y - (outfit3D ? swayBounds.getSize(new THREE.Vector3()).y * .025 : 0);
  scene.add(bodySwayPivot);
  bodySwayPivot.attach(avatar);
  if (outfit3D) bodySwayPivot.attach(outfit3D);
  bodySwayPivot.updateWorldMatrix(true,true);
  bodyRestMatrix.copy(bodySwayPivot.matrixWorld);
  bodySwayStarted = performance.now();
  const readyStatus = headPivot ? 'モデルを表示中' : 'モデルを表示中（頭部パーツ未検出）';
  setStatus(`${readyStatus}（顔画像を読み込み中）`);
  window.avatarStatus = `${readyStatus}（顔画像を読み込み中）`;
  try {
    const loadedTextures = await loadFaceTextures();
    const loadedFaceMaterial = createFaceMaterial(loadedTextures);
    object.traverse(node => {
      if (node.isMesh && node.name.toLowerCase().includes('head')) node.material = loadedFaceMaterial;
    });
    const oldUniformIndex = faceUniforms.findIndex(item => item.uniforms === faceMaterial.uniforms);
    if (oldUniformIndex >= 0) faceUniforms.splice(oldUniformIndex, 1);
    faceMaterial.dispose();
    faceMaterial = loadedFaceMaterial;
    const textureWarning = window.avatarTextureWarnings && window.avatarTextureWarnings.length;
    setStatus(textureWarning ? `${readyStatus}（一部顔画像の代替表示）` : 'モデルを読み込みました');
    window.avatarStatus = textureWarning ? `${readyStatus}（一部顔画像の代替表示）` : 'モデルを読み込みました';
  } catch (error) {
    console.warn('顔画像の読込に失敗しました。代替表示を続けます。', error);
    setStatus(`${readyStatus}（顔画像は代替表示）`);
    window.avatarStatus = `${readyStatus}（顔画像は代替表示）`;
  }
}

window.setAvatarOrbit = degrees => { avatar.rotation.y = THREE.MathUtils.degToRad(Number(degrees) || 0); };
window.resizeAvatarCanvas = (width, height) => {
  const w = Math.max(1, Math.round(Number(width) || 0));
  const h = Math.max(1, Math.round(Number(height) || 0));
  const current = renderer.getSize(new THREE.Vector2());
  if (current.x === w && current.y === h) return false;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
  wholeOutline.resize();
  return true;
};
window.setAvatarBackground = color => {
  for (const node of [document.documentElement, document.body, document.querySelector('.stage')]) node.style.background = color || 'transparent';
};
window.updateAvatarPose = pose => {
  const radians = Math.PI / 180;
  const yaw = Number(pose.yaw) || 0, pitch = Number(pose.pitch) || 0, roll = Number(pose.roll) || 0;
  targetHead.set(pitch * .40 * radians, roll * .30 * radians, -yaw * .44 * radians, 'ZYX');
  const mouthStates = [pose.mouthA, pose.mouthI, pose.mouthU, pose.mouthE, pose.mouthO].map(Number);
  const strongest = Math.max(...mouthStates), open = Math.max(0, Math.min(1, Number(pose.jawOpen) || 0));
  faceMotion.mouthState = open < .08 ? 0 : (strongest > .08 ? mouthStates.indexOf(strongest) + 1 : (open > .62 ? 5 : open > .38 ? 1 : 2));
  faceMotion.blink = Math.max(Number(pose.eyeBlinkLeft) || 0, Number(pose.eyeBlinkRight) || 0);
  faceMotion.pupilX = THREE.MathUtils.clamp(Number(pose.gazeX) || 0, -.35, .35) * .055;
  faceMotion.pupilY = -THREE.MathUtils.clamp(Number(pose.gazeY) || 0, -.35, .35) * .045;
};
window.updateAvatarPose({});
window.captureAvatarFrame = () => {
  if (window.avatarError) return `error:${window.avatarError}`;
  if (!model) return `loading:${window.avatarStatus || 'モデルを準備しています'}`;
  try {
    return renderer.domElement.toDataURL('image/png');
  } catch (error) {
    window.avatarError = `プレビュー画像を取得できません: ${error && error.message || error}`;
    return `error:${window.avatarError}`;
  }
};

let previousRenderTime = performance.now();
function render() {
  requestAnimationFrame(render);
  const now = performance.now();
  const frameDt = Math.min(.05, Math.max(.001, (now - previousRenderTime) / 1000));
  previousRenderTime = now;
  if (bodySwayPivot) {
    const elapsed = (now - bodySwayStarted) / 1000;
    const easeIn = THREE.MathUtils.smoothstep(elapsed, 0, 2);
    bodySwayPivot.rotation.y = THREE.MathUtils.damp(bodySwayPivot.rotation.y, keyboardBodyYaw ?? 0, 5, frameDt);
    bodySwayPivot.rotation.z = Math.sin(elapsed * Math.PI * 2 / 6.5) * THREE.MathUtils.degToRad(.9) * easeIn;
    bodySwayPivot.updateWorldMatrix(true,false);
    if(bodyShirt){
      bodyInverseMatrix.copy(bodySwayPivot.matrixWorld).invert();
      bodyShirt.uniforms.uClothingInverse.value.multiplyMatrices(bodyRestMatrix,bodyInverseMatrix);
    }
  }
  // FaceLandmarker blink scores often peak below 0.93, so use a lower
  // threshold to reliably select the supplied fully closed-eye artwork.
  const rawEye = faceMotion.blink >= 0.68 ? 2 : faceMotion.blink >= 0.25 ? 1 : 0;
  if (rawEye === 1 && !halfEyeSince) halfEyeSince = now;
  if (rawEye !== 1) halfEyeSince = 0;
  // Brief partial closures are treated as a normal blink. Only a sustained
  // half-lid pose selects the half-open artwork, preventing it from lingering.
  const desiredEye = rawEye === 1 && now - halfEyeSince < 320 ? 0 : rawEye;
  if (desiredEye === 1 && eyeAnimation.state === 0 && eyeAnimation.from === 0 && eyeAnimation.to === 0) {
    eyeAnimation.state = eyeAnimation.from = eyeAnimation.to = 1;
  }
  const eyeFrame = advanceExpression(eyeAnimation, desiredEye, now, desiredEye !== 2);
  const mouthFrame = advanceMouthExpression(faceMotion.mouthState, now);
  for (const { uniforms, eyeBounds, hasClosedEyes } of faceUniforms) {
    let eyeFrom = !hasClosedEyes && eyeFrame.from === 2 ? 1 : eyeFrame.from;
    const eyeTo = !hasClosedEyes && eyeFrame.to === 2 ? 1 : eyeFrame.to;
    const openingFromClosed = hasClosedEyes && eyeFrame.from === 2 && eyeFrame.to === 1;
    if (openingFromClosed) eyeFrom = 1;
    uniforms.uEyeState.value = eyeFrom;
    morphBounds(eyeBounds[eyeFrom][0], eyeBounds[eyeTo][0], eyeFrame.progress, uniforms.uEyeSourceLeft.value, uniforms.uEyeDisplayLeft.value, uniforms.uEyeScaleLeft.value);
    morphBounds(eyeBounds[eyeFrom][1], eyeBounds[eyeTo][1], eyeFrame.progress, uniforms.uEyeSourceRight.value, uniforms.uEyeDisplayRight.value, uniforms.uEyeScaleRight.value);
    if (hasClosedEyes && eyeFrame.to === 2 && eyeFrame.from !== 2) {
      const remaining = Math.max(0.02, 1 - eyeFrame.progress);
      uniforms.uEyeScaleLeft.value.y /= remaining;
      uniforms.uEyeScaleRight.value.y /= remaining;
    } else if (openingFromClosed) {
      const opening = Math.max(0.02, eyeFrame.progress);
      uniforms.uEyeScaleLeft.value.y /= opening;
      uniforms.uEyeScaleRight.value.y /= opening;
    } else if (rawEye === 1 && desiredEye === 0 && eyeFrom === 0) {
      // While a blink is still closing, deform the open image; only a held
      // half-open expression changes artwork before the eye is fully closed.
      const closing = THREE.MathUtils.smoothstep(faceMotion.blink, 0.25, 0.93);
      const remaining = Math.max(0.08, 1 - closing * 0.92);
      uniforms.uEyeScaleLeft.value.y /= remaining;
      uniforms.uEyeScaleRight.value.y /= remaining;
    }
    uniforms.uIrisPulse.value = eyeFrame.from === 1 && eyeFrame.to === 0 ? Math.sin(eyeFrame.progress * Math.PI) : 0;
    uniforms.uMouthState.value = mouthFrame.from;
    uniforms.uMouthStateTo.value = mouthFrame.to;
    uniforms.uMouthProgress.value = mouthFrame.progress;
    uniforms.uPupilOffset.value.x = THREE.MathUtils.damp(uniforms.uPupilOffset.value.x, faceMotion.pupilX, 10, frameDt);
    uniforms.uPupilOffset.value.y = THREE.MathUtils.damp(uniforms.uPupilOffset.value.y, faceMotion.pupilY, 10, frameDt);
  }
  if (headPivot) {
    headPivot.rotation.x = THREE.MathUtils.damp(headPivot.rotation.x, targetHead.x, 5, frameDt);
    headPivot.rotation.y = THREE.MathUtils.damp(headPivot.rotation.y, targetHead.y, 5, frameDt);
    headPivot.rotation.z = THREE.MathUtils.damp(headPivot.rotation.z, targetHead.z, 5, frameDt);
  }
  if (hairAnchor && hairSprite) {
    hairAnchor.getWorldPosition(hairCenter);
    hairAnchor.getWorldQuaternion(hairCurrentQuaternion);
    hairRelativeQuaternion.copy(hairCurrentQuaternion).multiply(hairRestInverse);
    const dt = Math.min(1 / 30, Math.max(1 / 240, (now - (hairPhysicsTime || now - 16.67)) / 1000));
    hairPhysicsTime = now;
    hairCollider.updateWorldMatrix(true, false);
    hairCollisionInverse.copy(hairCollider.matrixWorld).invert();
    for (const layer of hairLayers) {
      layer.mesh.position.copy(hairCenter).add(layer.offset.clone().applyQuaternion(hairBlendQuaternion.copy(hairIdentityQuaternion).slerp(hairRelativeQuaternion, layer.turn)));
      if (headPivot) layer.mesh.position.x += headPivot.rotation.z * hairWidth * layer.shift;
      hairBlendQuaternion.copy(hairIdentityQuaternion).slerp(hairRelativeQuaternion, layer.turn);
      layer.mesh.quaternion.copy(hairBlendQuaternion).multiply(camera.quaternion).multiply(layer.localRotation);
      layer.mesh.userData.step?.(dt,layer.mesh.position);
    }
    const yaw = headPivot ? headPivot.rotation.z : 0;
    hairMotion.time.value = now / 1000;
    hairMotion.sway.value = THREE.MathUtils.damp(hairMotion.sway.value, THREE.MathUtils.clamp((yaw - hairPreviousYaw) * 35, -1, 1), 6, frameDt);
    hairPreviousYaw = yaw;
  }
  if(outfit3D && hairCollider){
    const dt=Math.min(1/30,Math.max(1/240,(now-(outfitPhysicsTime||now-16.67))/1000));
    outfitPhysicsTime=now;outfit3D.userData.step(dt,hairCollider);
  }
  wholeOutline.render(scene, camera);
}
render();

addEventListener('resize', () => {
  window.resizeAvatarCanvas(Math.max(innerWidth, host.clientWidth, 1), Math.max(innerHeight, host.clientHeight, 1));
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
});
