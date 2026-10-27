// Real first integral fit; other numerical jobs and the parsed model reply are fixtures.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const requestText='根据我的CASE16A_noise03_seed13.csv，完成rebuild pwm和实际pwm；pann和加权积分四个仿真';
const scenario=process.env.MATRIX_SCENARIO || 'complete';
assert(['complete','failure','cancel'].includes(scenario));
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1550,height:1100}}),fits=[],errors=[];
 let original=null,releaseThird=false,chatBody=null;
 const runs=['measured','controller'].flatMap(pwm_source=>['weighted_integral','pann'].map(method=>({pwm_source,method})));
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',async response=>{
  if(/\/api\/jobs\/[^/]+$/.test(response.url())&&!response.url().includes('/matrix-')){
   const body=await response.json();if(body.status==='complete')original=body.result;
  }
 });
 await page.route('**/api/chat',route=>{
  chatBody=route.request().postDataJSON();assert.equal(chatBody.message,requestText);
  return route.fulfill({json:{mode:'deepseek',answer:'四组计划已解析。',job_id:null,sources:[],tool_calls:[],
   route:{task:'configuration',label:'辨识配置',mode:'rules',secondary_tasks:[]},
   ui_action:{id:chatBody.ui_context.request_id,type:'identification_plan',parser:'deepseek',version:3,
    filename:'CASE16A_noise03_seed13.csv',methods:['weighted_integral','pann'],changes:{mode:'LC'},runs}}});
 });
 await page.route('**/api/identify',route=>{
  fits.push(route.request().postDataJSON());
  if(fits.length===1)return route.continue();
  assert(original);return route.fulfill({json:{job_id:`matrix-${fits.length}`}});
 });
 await page.route('**/api/jobs/matrix-*',route=>{
  const index=Number(route.request().url().split('-').at(-1)),options=fits[index-1].options;
  return route.fulfill({json:index===3&&!releaseThird ? {status:'running',stage:'initialization'}
   : index===3&&scenario==='failure' ? {status:'failed',error:'Four-run test failure'}
   : {status:'complete',result:{...original,method:options.method,options}}});
 });
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
 const paused=()=>page.waitForFunction(()=>currentIdentificationPlan()?.status==='paused'&&!historyLocked());
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.locator('#csv-file').setInputFiles({name:'CASE16A_noise03_seed13.csv',mimeType:'text/csv',buffer:fs.readFileSync('examples/plecs_buck_case16a.csv')});await ready();
  assert.equal(await page.evaluate(()=>requestsIdentificationExecution('根据我的CASE16A_noise03_seed13.csv，完成rebuild pwm和实际pwm；pann和加权积分四个仿真')),true);
  assert.deepEqual(await page.evaluate(()=>[
   'Please run four experiments with actual and reconstructed PWM',
   'Complete 4 simulations using actual and rebuilt PWM',
   'Compare four experiments','Do not run four experiments',
  ].map(requestsIdentificationExecution)),[true,true,false,false]);
  const pwm=await page.evaluate(()=>{const pwm=state.mapping.pwm;state.mapping={...state.mapping,pwm:null};return pwm;});
  await page.locator('#chat-input').fill(requestText);await page.locator('#send-chat').click();await paused();
  assert.equal(chatBody.identification_enabled,false,'Closed panel must still permit a parsed plan');
  assert.match(await page.locator('#plan-note').innerText(),/补齐实际 PWM 列/);assert.equal(fits.length,0);
  await page.evaluate(pwm=>{state.mapping={...state.mapping,pwm};},pwm);
  await page.locator('#plan-continue').click();await paused();
  assert.match(await page.locator('#plan-note').innerText(),/核对并确认控制电路/);assert.equal(fits.length,0);
  assert.equal(await page.locator('#pwm-source').inputValue(),'controller');
  await page.locator('#controller-confirmed').selectOption('yes');
  await page.locator('#plan-continue').click();
  await page.waitForFunction(()=>currentIdentificationPlan()?.done.length===2&&state.job==='matrix-3');
  assert.equal(fits.length,3);
  await page.reload();await page.waitForFunction(()=>state.connected&&state.job==='matrix-3');
  assert.equal(fits.length,3,'Refreshing at the PWM-source transition must not resubmit jobs');
  if(scenario==='cancel'){
   await page.locator('#plan-cancel').click();releaseThird=true;await ready();
   assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'cancelled');
   assert.equal(fits.length,3,'Cancelling must not submit the fourth job');
   assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).experiments.length),3);
   assert.deepEqual(errors,[]);
   console.log('PASS: cancel during third job preserves its completed result and prevents the fourth submission.');return;
  }
  releaseThird=true;await ready();
  await page.locator('#plan-continue').click();
  await page.waitForFunction(()=>['complete','paused'].includes(currentIdentificationPlan()?.status)&&!historyLocked());
  if(scenario==='failure'){
   assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'paused');
   assert.match(await page.locator('#plan-note').innerText(),/Four-run test failure/);
   assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).experiments.length),2);
   await page.locator('#plan-continue').click();await paused();
   assert.equal(fits.length,3,'A failed third job must not resubmit or skip to the fourth');
   await page.locator('#fixed-r').fill('8');await page.locator('#plan-continue').click();await paused();
   assert.match(await page.locator('#plan-note').innerText(),/数据或参数已更改/);
   assert.equal(fits.length,3);assert.deepEqual(errors,[]);
   console.log('PASS: failure at the PWM-source transition preserves two results, prevents retries, and rejects changed inputs.');return;
  }
  const result=await page.evaluate(()=>({plan:currentIdentificationPlan(),experiments:readConversation(activeConversation.id).experiments}));
  assert.equal(result.plan.status,'complete',result.plan.note);
  assert.equal(fits.length,4);assert.deepEqual(fits.map(f=>({pwm_source:f.options.pwm_source,method:f.options.method})),runs);
  assert(fits.every(f=>f.options.mode==='LC'&&f.options.resistance_ohm===6));
  assert(fits.slice(2).every(f=>f.options.controller_confirmed&&f.options.controller));
  assert.equal(result.experiments.length,4);
  assert.equal(new Set(result.plan.done.map(r=>r.report_name)).size,4);
  assert(result.plan.done.slice(0,2).every(r=>r.report_name.includes('_measured_')));
  assert(result.plan.done.slice(2).every(r=>r.report_name.includes('_rebuilt_')));
  assert.match(await page.locator('#plan-progress').innerText(),/4 \/ 4/);
  await page.locator('#language-toggle').click();
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#identification-plan').innerText()));
  await page.reload();await ready();assert.equal(fits.length,4);
  assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'complete');
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).experiments.length),4);
  assert.deepEqual(errors,[]);
  console.log('PASS: exact four-run request, closed panel, all prerequisites checked, distinct PWM/method payloads, refresh during third job, four saved names, English and no duplicate submissions.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
