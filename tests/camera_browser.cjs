// Uses a synthetic webcam, then an upstream public test portrait. Never opens the user's webcam.
const {chromium}=require('../build/browser-check/node_modules/playwright');
const fs=require('node:fs');
(async()=>{
  const url=process.argv[2],origin=new URL(url).origin;
  const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,
    args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
  try{
    const page=await browser.newPage();
    const errors=[],external=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(!r.url().startsWith(origin)&&!r.url().startsWith('blob:'))external.push(r.url());});
    await page.goto(url);
    await page.locator('#start').click();
    try {
      await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('顔を探しています'),null,{timeout:45000});
    } catch(e) { throw new Error('Camera startup: '+await page.locator('#status').textContent()+' / '+JSON.stringify(errors)); }
    const chosen=await page.locator('#camera-device').inputValue();
    if(!chosen)throw new Error('Actual camera was not selected in the device list');
    await page.evaluate(()=>{window.previousCameraTrack=document.querySelector('#camera').srcObject.getVideoTracks()[0];});
    await page.locator('#camera-device').selectOption(chosen);
    await page.waitForFunction(()=>window.previousCameraTrack.readyState==='ended');
    await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('顔を探しています'),null,{timeout:45000});
    await page.locator('#stop').click();
    await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('停止しました'));
    const fixture=Array.from(fs.readFileSync('build/browser-check/portrait.jpg'));
    const result=await page.evaluate(async bytes=>{
      const {FaceLandmarker,FilesetResolver}=await import('/tracker/vision_bundle.mjs');
      const engine=await FaceLandmarker.createFromOptions(await FilesetResolver.forVisionTasks('/tracker/wasm'),{
        baseOptions:{modelAssetPath:'/tracker/face_landmarker.task',delegate:'CPU'},runningMode:'IMAGE',numFaces:1,outputFaceBlendshapes:true,outputFacialTransformationMatrixes:true});
      const blob=URL.createObjectURL(new Blob([new Uint8Array(bytes)],{type:'image/jpeg'}));
      const img=new Image();img.src=blob;await img.decode();
      const r=engine.detect(img);URL.revokeObjectURL(blob);engine.close();
      return {faces:r.faceLandmarks.length,landmarks:r.faceLandmarks[0]?.length,blendshapes:r.faceBlendshapes[0]?.categories.length,matrix:r.facialTransformationMatrixes[0]?.data.length};
    },fixture);
    if(result.faces!==1||result.landmarks!==478||result.blendshapes!==52||result.matrix!==16)throw new Error('Inference failed: '+JSON.stringify(result));
    if(errors.length||external.length)throw new Error(JSON.stringify({errors,external}));
    console.log(JSON.stringify({syntheticCamera:'select/restart/stop OK',publicPortrait:result,externalRequests:external.length,pageErrors:errors.length}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
