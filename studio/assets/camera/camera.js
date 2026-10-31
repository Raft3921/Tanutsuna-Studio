import {FaceLandmarker, FilesetResolver} from '/tracker/vision_bundle.mjs';
const $=id=>document.getElementById(id), token=new URLSearchParams(location.hash.slice(1)).get('token');
let preferredLabel=new URLSearchParams(location.hash.slice(1)).get('camera')||'';
let preferredMicrophone=new URLSearchParams(location.hash.slice(1)).get('microphone')||'';
let detector, stream, audioContext, audioAnalyser, audioSamples, running=false, audioReady=false, audioNoise=.002, audioMouth=0;
let generation=0, baseline=null, busy=false, lastVideo=-1, noiseHistory=[];
// CPU face inference competes with capture and editing on Windows laptops.
// Fifteen inference frames per second at 320x240 reduces that load while
// keeping head and mouth tracking responsive.
const windowsTracking=/Windows/i.test(navigator.userAgent);
const trackingInterval=windowsTracking?66:50;
const sensitivity=$('speech-sensitivity'),queryGain=Number(new URLSearchParams(location.hash.slice(1)).get('voiceGain'));
if(sensitivity){const saved=localStorage.getItem('speech-sensitivity');if(Number.isFinite(queryGain)&&queryGain>0)sensitivity.value=String(Math.max(.5,Math.min(5,queryGain)));else if(saved)sensitivity.value=saved;}
function showSensitivity(){ $('speech-sensitivity-value').value=Number(sensitivity.value).toFixed(1);localStorage.setItem('speech-sensitivity',sensitivity.value); }
sensitivity?.addEventListener('input',showSensitivity);showSensitivity();
function speechMouth(){
  if(!audioAnalyser)return 0;
  audioAnalyser.getFloatTimeDomainData(audioSamples);
  let sum=0;for(const sample of audioSamples)sum+=sample*sample;
  const rms=Math.sqrt(sum/audioSamples.length);
  // Track the recent quiet floor, including microphones with a steady hiss.
  // The old estimator only moved upward below 1.6x its initial value, so a
  // slightly noisy mic could be interpreted as continuous speech forever.
  noiseHistory.push(rms);if(noiseHistory.length>120)noiseHistory.shift();
  if(noiseHistory.length>=12){
    const sorted=[...noiseHistory].sort((a,b)=>a-b);
    audioNoise=sorted[Math.floor((sorted.length-1)*.15)];
  }else if(rms<audioNoise)audioNoise+=(rms-audioNoise)*.12;
  const gate=Math.max(.0025,audioNoise*1.7), ceiling=Math.max(gate+.008,audioNoise*5);
  const activity=Math.max(0,Math.min(1,(rms-gate)/(ceiling-gate)));
  const target=Math.min(.28,Math.sqrt(activity)*.065*Number(sensitivity.value));
  audioMouth+=(target>audioMouth ? .42 : .18)*(target-audioMouth);
  return audioMouth;
}
async function startAudio(stream){
  const track=stream.getAudioTracks()[0];if(!track)return false;
  audioContext=new AudioContext({latencyHint:'interactive'});
  await audioContext.resume();
  const source=audioContext.createMediaStreamSource(new MediaStream([track]));
  const highpass=audioContext.createBiquadFilter();highpass.type='highpass';highpass.frequency.value=110;
  const lowpass=audioContext.createBiquadFilter();lowpass.type='lowpass';lowpass.frequency.value=3600;
  audioAnalyser=audioContext.createAnalyser();audioAnalyser.fftSize=512;audioAnalyser.smoothingTimeConstant=.15;
  source.connect(highpass);highpass.connect(lowpass);lowpass.connect(audioAnalyser);
  audioSamples=new Float32Array(audioAnalyser.fftSize);audioNoise=.002;audioMouth=0;noiseHistory=[];audioReady=true;
  return true;
}
async function stopAudio(){
  audioReady=false;audioAnalyser=null;audioSamples=null;audioMouth=0;
  if(audioContext){const context=audioContext;audioContext=null;try{await context.close();}catch{}}
}
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
  const visualJaw=c.jawOpen||0, voiceJaw=speechMouth(), jawOpen=Math.max(visualJaw,voiceJaw);
  return {yaw:yaw-baseline.yaw,pitch:pitch-baseline.pitch,roll:roll-baseline.roll,
    headX:(m[12]-baseline.x)*.045,headY:(m[13]-baseline.y)*.045,headZ:(m[14]-baseline.z)*.035,
    jawOpen,browUp:c.browInnerUp||0,
    // Visual vowel estimates from lip shape; no speech recognition or audio upload.
    mouthA:Math.max(0,visualJaw-(c.mouthFunnel||0)-(c.mouthPucker||0),voiceJaw),
    mouthI:Math.max(c.mouthStretchLeft||0,c.mouthStretchRight||0)*(1-(c.jawOpen||0)),
    mouthU:c.mouthPucker||0,
    mouthE:Math.max(c.mouthSmileLeft||0,c.mouthSmileRight||0)*(c.jawOpen||0),
    mouthO:c.mouthFunnel||0};
}
async function send(detected,pose={},status='') {
  const response=await fetch('/pose',{method:'POST',headers:{'Content-Type':'application/json','X-Tracking-Token':token},
    body:JSON.stringify({detected,pose,status,cameraLabel:running?($('camera-device').selectedOptions[0]?.textContent||''):'',microphoneLabel:audioReady?($('microphone-device').selectedOptions[0]?.textContent||''):'',voiceGain:Number(sensitivity.value)}),keepalive:true});
  if(!response.ok) throw new Error('制作デスクとの接続が切れました。制作デスクからカメラ画面を開き直してください。');
}
async function stop(message='カメラを停止しました。') {
  running=false;generation++;
  if(stream) stream.getTracks().forEach(t=>t.stop());
  await stopAudio();
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
      $('status').textContent=detected?`追跡中${audioReady?'・声の動きを反映中':'・顔の動きのみ'}。正面を向いて「正面を基準にする」を押すと中心を合わせられます。`:'顔を探しています。明るさとカメラの向きを確認してください。';
      await send(detected,pose||{},detected?'カメラ追跡中':'顔を探しています');
    }catch(error){await stop(error.message||String(error));return;}finally{busy=false;}
  }
  setTimeout(()=>tick(version),trackingInterval);
}
function cameraKey(label){return label.toLowerCase().replaceAll('カメラ','camera').replaceAll('の','').replace(/\(built.in\)|（内蔵）|[\s\p{P}\p{Cf}]/gu,'');}
function describeCameraError(error){
  const name=error?.name||'Error',message=error?.message||String(error);
  if(name==='NotAllowedError'){
    const mac=navigator.userAgent.toLowerCase().includes('mac');
    const help=mac?'macOS「システム設定 ＞ プライバシーとセキュリティ ＞ カメラ」で、このブラウザーを許可してください。':'OSのプライバシー設定で、このブラウザーのカメラ利用を許可してください。';
    return `カメラへのアクセスが拒否されました。${help} 詳細: ${message}`;
  }
  if(name==='NotFoundError')return 'カメラが見つかりません。接続と選択中のカメラを確認してください。詳細: '+message;
  if(name==='NotReadableError')return 'カメラを他のアプリが使用中か、起動できません。FaceTimeやZoomなどを終了して再試行してください。詳細: '+message;
  return `${name}: ${message}`;
}
async function cameraList(){
  const previous=$('camera-device').value,previousMic=$('microphone-device').value;
  const all=await navigator.mediaDevices.enumerateDevices();
  const devices=all.filter(d=>d.kind==='videoinput'),microphones=all.filter(d=>d.kind==='audioinput');
  $('camera-device').replaceChildren(new Option('標準のカメラ',''));
  for(const [index,device] of devices.entries())$('camera-device').add(new Option(device.label||`カメラ ${index+1}`,device.deviceId));
  $('microphone-device').replaceChildren(new Option('標準のマイク',''));
  for(const [index,device] of microphones.entries())$('microphone-device').add(new Option(device.label||`マイク ${index+1}`,device.deviceId));
  if(previous && devices.some(d=>d.deviceId===previous))$('camera-device').value=previous;
  else if(preferredLabel){
    const desired=cameraKey(preferredLabel),match=devices.find(d=>cameraKey(d.label)===desired)||devices.find(d=>desired.length>5&&cameraKey(d.label).length>5&&(cameraKey(d.label).includes(desired)||desired.includes(cameraKey(d.label))));
    if(match)$('camera-device').value=match.deviceId;
  }
  if(previousMic&&microphones.some(d=>d.deviceId===previousMic))$('microphone-device').value=previousMic;
  else if(preferredMicrophone){
    const desired=cameraKey(preferredMicrophone),match=microphones.find(d=>cameraKey(d.label)===desired)||microphones.find(d=>desired.length>5&&cameraKey(d.label).length>5&&(cameraKey(d.label).includes(desired)||desired.includes(cameraKey(d.label))));
    if(match)$('microphone-device').value=match.deviceId;
  }
  return devices;
}
$('refresh-cameras').onclick=()=>cameraList().catch(()=>{$('status').textContent='カメラ一覧を取得できません。OSのカメラ許可を確認してください。';});
$('camera-device').onchange=async()=>{
  preferredLabel=$('camera-device').value?$('camera-device').selectedOptions[0].textContent:'';
  if(running){await stop('カメラを切り替えています…');$('start').click();}
};
$('microphone-device').onchange=async()=>{
  preferredMicrophone=$('microphone-device').value?$('microphone-device').selectedOptions[0].textContent:'';
  if(running){await stop('マイクを切り替えています…');$('start').click();}
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
    const selected=$('camera-device').value,selectedMicrophone=$('microphone-device').value;
    const video={...(selected?{deviceId:{exact:selected}}:{}),width:{ideal:windowsTracking?320:480},height:{ideal:windowsTracking?240:360},frameRate:{ideal:windowsTracking?20:30,max:windowsTracking?20:30}};
    let candidate;
    try{
      candidate=await navigator.mediaDevices.getUserMedia({video,audio:{...(selectedMicrophone?{deviceId:{exact:selectedMicrophone}}:{}),channelCount:1,echoCancellation:false,noiseSuppression:true,autoGainControl:true}});
      if(generation!==version){candidate.getTracks().forEach(t=>t.stop());return;}
      try{await startAudio(candidate);}catch{candidate.getAudioTracks().forEach(t=>t.stop());await stopAudio();}
    }catch(audioError){
      if(generation!==version)return;
      try{candidate=await navigator.mediaDevices.getUserMedia({video,audio:false});audioReady=false;}
      catch(cameraFailure){throw new Error(`カメラを開けませんでした（${describeCameraError(cameraFailure)}）。マイク付き取得時の詳細: ${audioError?.name||'Error'}: ${audioError?.message||String(audioError)}`);}
    }
    if(generation!==version){candidate.getTracks().forEach(t=>t.stop());return;}
    stream=candidate;
    $('camera').srcObject=stream;await $('camera').play();
    await cameraList();
    const actual=stream.getVideoTracks()[0].getSettings().deviceId;
    if(actual)$('camera-device').value=actual;
    const actualMic=stream.getAudioTracks()[0]?.getSettings().deviceId;
    if(actualMic)$('microphone-device').value=actualMic;
    baseline=null;lastVideo=-1;running=true;
    $('stop').disabled=false;$('calibrate').disabled=false;
    tick(version);
  }catch(error){if(generation===version)await stop('開始できません：'+describeCameraError(error));}
};
$('stop').onclick=()=>stop();
$('calibrate').onclick=()=>{baseline=null;$('status').textContent='次に検出する正面の顔を基準にします。';};
$('show-preview').onchange=()=>{$('camera').hidden=!$('show-preview').checked;};
window.addEventListener('pagehide',()=>{running=false;if(stream)stream.getTracks().forEach(t=>t.stop());send(false,{},'カメラ画面を終了').catch(()=>{});});

// Recording requests camera preparation without a second start button.
if(new URLSearchParams(location.hash.slice(1)).get('autostart')==='1') $('start').click();
