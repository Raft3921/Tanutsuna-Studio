import {FaceLandmarker, FilesetResolver} from '/tracker/vision_bundle.mjs';
const $=id=>document.getElementById(id), token=new URLSearchParams(location.hash.slice(1)).get('token');
let preferredLabel=new URLSearchParams(location.hash.slice(1)).get('camera')||'';
let detector, stream, running=false, generation=0, baseline=null, busy=false, lastVideo=-1;
function poseFromResult(result) {
  const c=Object.fromEntries((result.faceBlendshapes?.[0]?.categories||[]).map(x=>[x.categoryName,x.score]));
  const m=result.facialTransformationMatrixes?.[0]?.data;
  if(!m || m.length!==16) return null;
  // MediaPipe returns a column-major 4x4 transform. Normalize rotation axes to discard scale.
  const length=Math.hypot(m[0],m[1],m[2])||1;
  const pitch=Math.atan2(m[6],m[10])*180/Math.PI;
  const yaw=Math.atan2(-m[2]/length,Math.hypot(m[6],m[10])/length)*180/Math.PI;
  const roll=Math.atan2(m[1],m[0])*180/Math.PI;
  if(!baseline) baseline={yaw,pitch,roll,x:m[12],y:m[13],z:m[14]};
  return {yaw:yaw-baseline.yaw,pitch:pitch-baseline.pitch,roll:roll-baseline.roll,
    headX:(m[12]-baseline.x)*.045,headY:(m[13]-baseline.y)*.045,headZ:(m[14]-baseline.z)*.035,
    jawOpen:c.jawOpen||0,browUp:c.browInnerUp||0,
    // Visual vowel estimates from lip shape; no speech recognition or audio upload.
    mouthA:Math.max(0,(c.jawOpen||0)-(c.mouthFunnel||0)-(c.mouthPucker||0)),
    mouthI:Math.max(c.mouthStretchLeft||0,c.mouthStretchRight||0)*(1-(c.jawOpen||0)),
    mouthU:c.mouthPucker||0,
    mouthE:Math.max(c.mouthSmileLeft||0,c.mouthSmileRight||0)*(c.jawOpen||0),
    mouthO:c.mouthFunnel||0};
}
async function send(detected,pose={},status='') {
  const response=await fetch('/pose',{method:'POST',headers:{'Content-Type':'application/json','X-Tracking-Token':token},
    body:JSON.stringify({detected,pose,status,cameraLabel:running?($('camera-device').selectedOptions[0]?.textContent||''):''}),keepalive:true});
  if(!response.ok) throw new Error('制作デスクとの接続が切れました。制作デスクからカメラ画面を開き直してください。');
}
async function stop(message='カメラを停止しました。') {
  running=false;generation++;
  if(stream) stream.getTracks().forEach(t=>t.stop());
  stream=null;$('camera').srcObject=null;
  $('start').disabled=false;$('stop').disabled=true;$('calibrate').disabled=true;
  $('status').textContent=message;
  try{await send(false,{},message);}catch{}
}
async function tick(version) {
  if(!running || generation!==version) return;
  if(!busy && $('camera').readyState>=2 && $('camera').currentTime!==lastVideo){
    busy=true;lastVideo=$('camera').currentTime;
    try{
      const result=detector.detectForVideo($('camera'),performance.now());
      const pose=poseFromResult(result), detected=!!pose;
      $('status').textContent=detected?'追跡中。正面を向いて「正面を基準にする」を押すと中心を合わせられます。':'顔を探しています。明るさとカメラの向きを確認してください。';
      await send(detected,pose||{},detected?'カメラ追跡中':'顔を探しています');
    }catch(error){await stop(error.message||String(error));return;}finally{busy=false;}
  }
  setTimeout(()=>tick(version),50);
}
function cameraKey(label){return label.toLowerCase().replaceAll('カメラ','camera').replaceAll('の','').replace(/\(built.in\)|（内蔵）|[\s\p{P}\p{Cf}]/gu,'');}
async function cameraList(){
  const previous=$('camera-device').value;
  const devices=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='videoinput');
  $('camera-device').replaceChildren(new Option('標準のカメラ',''));
  for(const [index,device] of devices.entries())$('camera-device').add(new Option(device.label||`カメラ ${index+1}`,device.deviceId));
  if(previous && devices.some(d=>d.deviceId===previous))$('camera-device').value=previous;
  else if(preferredLabel){
    const desired=cameraKey(preferredLabel),match=devices.find(d=>cameraKey(d.label)===desired)||devices.find(d=>desired.length>5&&cameraKey(d.label).length>5&&(cameraKey(d.label).includes(desired)||desired.includes(cameraKey(d.label))));
    if(match)$('camera-device').value=match.deviceId;
  }
  return devices;
}
$('refresh-cameras').onclick=()=>cameraList().catch(()=>{$('status').textContent='カメラ一覧を取得できません。OSのカメラ許可を確認してください。';});
$('camera-device').onchange=async()=>{
  preferredLabel=$('camera-device').value?$('camera-device').selectedOptions[0].textContent:'';
  if(running){await stop('カメラを切り替えています…');$('start').click();}
};
navigator.mediaDevices.addEventListener('devicechange',()=>cameraList().catch(()=>{}));
cameraList().catch(()=>{});
$('start').onclick=async()=>{
  if(!token){$('status').textContent='制作デスクの「カメラ追跡を開始」から開いてください。';return;}
  $('start').disabled=true;
  $('stop').disabled=false;
  const version=++generation;
  try{
    $('status').textContent='追跡モデルを準備中…';
    if(!detector){
      const wasm=await FilesetResolver.forVisionTasks('/tracker/wasm');
      detector=await FaceLandmarker.createFromOptions(wasm,{baseOptions:{modelAssetPath:'/tracker/face_landmarker.task',delegate:'CPU'},runningMode:'VIDEO',numFaces:1,outputFaceBlendshapes:true,outputFacialTransformationMatrixes:true});
    }
    if(generation!==version)return;
    let devices=await cameraList();
    if(preferredLabel && !devices.some(d=>d.label)){
      const permissionStream=await navigator.mediaDevices.getUserMedia({video:true,audio:false});
      permissionStream.getTracks().forEach(t=>t.stop());
      if(generation!==version)return;
      devices=await cameraList();
    }
    if(preferredLabel && !$('camera-device').value)throw new Error('選択したカメラが見つかりません。接続を確認するかカメラ一覧で選び直してください。');
    const selected=$('camera-device').value;
    const candidate=await navigator.mediaDevices.getUserMedia({video:{...(selected?{deviceId:{exact:selected}}:{}),width:{ideal:480},height:{ideal:360},frameRate:{ideal:30,max:30}},audio:false});
    if(generation!==version){candidate.getTracks().forEach(t=>t.stop());return;}
    stream=candidate;
    $('camera').srcObject=stream;await $('camera').play();
    await cameraList();
    const actual=stream.getVideoTracks()[0].getSettings().deviceId;
    if(actual)$('camera-device').value=actual;
    baseline=null;lastVideo=-1;running=true;
    $('stop').disabled=false;$('calibrate').disabled=false;
    tick(version);
  }catch(error){if(generation===version)await stop('開始できません：'+(error.message||String(error)));}
};
$('stop').onclick=()=>stop();
$('calibrate').onclick=()=>{baseline=null;$('status').textContent='次に検出する正面の顔を基準にします。';};
$('show-preview').onchange=()=>{$('camera').hidden=!$('show-preview').checked;};
window.addEventListener('pagehide',()=>{running=false;if(stream)stream.getTracks().forEach(t=>t.stop());send(false,{},'カメラ画面を終了').catch(()=>{});});

// Recording requests camera preparation without a second start button.
if(new URLSearchParams(location.hash.slice(1)).get('autostart')==='1') $('start').click();
