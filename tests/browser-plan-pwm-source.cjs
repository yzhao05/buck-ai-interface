// Real integral fit, mocked PANN. Set TEST_LIVE_DEEPSEEK=1 to use the real chat parser.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage(),errors=[],fits=[],chatResponses=[];
 page.on('response',async response=>{
  if(response.url().endsWith('/api/chat'))chatResponses.push(await response.json());
 });
 if(process.env.TEST_LIVE_DEEPSEEK==='1')page.setDefaultTimeout(90000);
 if(process.env.TEST_LIVE_DEEPSEEK!=='1')await page.route('**/api/chat',route=>{
  const body=route.request().postDataJSON();
  assert.equal(body.message,'请对于CASE16A_noise03_seed14.csv根据已有pwm完成两种方式参数识别');
  assert.equal(body.ui_context.options.pwm_source,'controller');
  return route.fulfill({json:{mode:'deepseek',answer:'计划已解析。',job_id:null,sources:[],tool_calls:[],
   route:{task:'configuration',label:'辨识配置',mode:'rules',secondary_tasks:[]},
   ui_action:{id:body.ui_context.request_id,type:'identification_plan',parser:'deepseek',version:2,filename:'CASE16A_noise03_seed14.csv',
    methods:['weighted_integral','pann'],changes:{pwm_source:'measured'}}}});
 });
 let integral=null;
 page.on('response',async response=>{
  if(/\/api\/jobs\/[^/]+$/.test(response.url())&&!response.url().endsWith('/mock-measured-pann')){
   const body=await response.json();if(body.status==='complete')integral=body.result;
  }
 });
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/identify',async route=>{
  const body=route.request().postDataJSON();fits.push(body);
  if(body.options.method!=='pann')return route.continue();
  assert(integral,'The real integral result must be captured before the simulated PANN job');
  return route.fulfill({json:{job_id:'mock-measured-pann'}});
 });
 await page.route('**/api/jobs/mock-measured-pann',route=>route.fulfill({json:{status:'complete',
  result:{...integral,method:'pann',options:fits.at(-1).options}}}));
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
 const send=async()=>{
  await page.locator('#chat-input').fill('请对于CASE16A_noise03_seed14.csv根据已有pwm完成两种方式参数识别');
  const response=page.waitForResponse(r=>r.url().endsWith('/api/chat'));
  await page.locator('#send-chat').click();
  const parsed=await (await response).json();
  assert(parsed.ui_action,parsed.answer);
  await page.waitForFunction(()=>['complete','paused'].includes(currentIdentificationPlan()?.status)&&!historyLocked(),null,{timeout:90000});
 };
 try{
  await page.goto(process.env.TEST_BASE_URL || 'http://127.0.0.1:8001');await ready();
  await page.locator('#csv-file').setInputFiles({name:'CASE16A_noise03_seed14.csv',mimeType:'text/csv',
   buffer:fs.readFileSync('examples/plecs_buck_case16a.csv')});await ready();
  await page.locator('#enter-identification').click();
  await page.locator('#pwm-source').selectOption('controller');
  await page.locator('#controller-confirmed').selectOption('yes');
  await send();
  assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'complete',await page.locator('#plan-note').innerText());
  assert.deepEqual(fits.map(f=>f.options.method),['weighted_integral','pann']);
  assert(fits.every(f=>f.options.pwm_source==='measured'));
  assert.equal(await page.locator('#pwm-source').inputValue(),'measured');
  assert.equal(chatResponses.at(-1).mode,'deepseek');
  assert.equal(chatResponses.at(-1).ui_action.changes.pwm_source,'measured');
  assert.match(await page.locator('#plan-source').innerText(),/已知 PWM/);
  assert.match(await page.locator('.message-task').last().innerText(),/DeepSeek 计划解析/);
  assert.doesNotMatch(await page.locator('.message-task').last().innerText(),/本地规则分类/);
  assert.match(await page.locator('.message.assistant .message-label').last().innerText(),/辨识任务/);
  const names=await page.evaluate(()=>currentIdentificationPlan().done.map(r=>r.report_name));
  assert(names.every(n=>n.includes('_measured_')),JSON.stringify(names));
  // Missing measured PWM must pause, even with a previously confirmed controller.
  await page.evaluate(()=>{state.mapping={...state.mapping,pwm:null};});
  await page.locator('#pwm-source').selectOption('controller');
  await page.locator('#controller-confirmed').selectOption('yes');
  await send();
  assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'paused');
  assert.match(await page.locator('#plan-note').innerText(),/请映射实际 PWM 列/);
  assert.match(await page.locator('#plan-note').innerText(),/取消本计划并重新发送指令/);
  assert.equal(fits.length,2,'Missing measured PWM must never fall back to reconstruction');
  await page.locator('#pwm-source').selectOption('controller');
  await page.locator('#plan-continue').click();
  await page.waitForFunction(()=>currentIdentificationPlan()?.status==='paused'&&!historyLocked());
  assert.equal(await page.locator('#pwm-source').inputValue(),'measured');
  assert.equal(fits.length,2,'Continuing cannot override the explicit source in the plan');
  await page.locator('#language-toggle').click();
  assert.match(await page.locator('#plan-source').innerText(),/known PWM/);
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#identification-plan').innerText()));
  // A queued plan created by the old rules parser must not resume after upgrading.
  await page.evaluate(()=>{
   const old={...currentIdentificationPlan(),status:'queued',changes:{},done:[],jobId:null};
   delete old.parser;delete old.version;
   sessionStorage.setItem(identificationPlanKey(),JSON.stringify(old));
  });
  await page.reload();await ready();
  assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'cancelled');
  assert.match(await page.locator('#plan-note').innerText(),/old plan was stopped/);
  assert.equal(await page.locator('#plan-continue').isVisible(),false);
  assert.equal(fits.length,2);
  assert.deepEqual(errors,[]);
  console.log(`PASS (${process.env.TEST_LIVE_DEEPSEEK==='1'?'live':'mocked'} chat): measured payloads and names, parser/source labels, English, missing PWM guard and old plan migration.`);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
