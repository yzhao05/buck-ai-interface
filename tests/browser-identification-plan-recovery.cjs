// One real integral fit; PANN job responses are deliberately mocked to test refresh/recovery.
const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1550,height:1100}}),errors=[],fits=[];
 let original=null,pannOptions=null,complete=false;
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',async response=>{
  if(/\/api\/jobs\/[^/]+$/.test(response.url())&&!response.url().endsWith('/mock-pann')){
   try{const body=await response.json();if(body.status==='complete')original=body.result;}catch{}
  }
 });
 await page.route('**/api/identify',async route=>{
  const body=route.request().postDataJSON();fits.push(body);
  if(body.options.method!=='pann')return route.continue();
  pannOptions=body.options;return route.fulfill({json:{job_id:'mock-pann'}});
 });
 await page.route('**/api/jobs/mock-pann',route=>route.fulfill({json:complete
  ? {status:'complete',result:{...original,method:'pann',options:pannOptions}}
  : {status:'running',stage:'pann_training'}}));
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
 try{
  await page.goto('http://127.0.0.1:8001');await ready();await page.locator('#load-demo').click();await ready();
  await page.locator('#chat-input').fill('固定 R=6，运行两种方法');await page.locator('#send-chat').click();
  await page.waitForFunction(()=>currentIdentificationPlan()?.done.length===1&&state.job==='mock-pann');
  assert(original);assert.equal(fits.length,2);
  await page.reload();await page.waitForFunction(()=>state.connected&&state.job==='mock-pann');
  assert.equal(fits.length,2);assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'paused');
  complete=true;await ready();
  await page.locator('#plan-continue').click();
  await page.waitForFunction(()=>['complete','paused'].includes(currentIdentificationPlan()?.status)&&!historyLocked());
  assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'complete',await page.locator('#plan-note').innerText());
  assert.equal(fits.length,2,'Refreshing an in-flight plan must not resubmit either job');
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).experiments.length),2);
  assert.match(await page.locator('.message.assistant').last().innerText(),/L = .*μH/);
  assert.match(await page.locator('#plan-runs').innerText(),/\.json/);
  await page.locator('#language-toggle').click();
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#identification-plan').innerText()));
  assert.deepEqual(errors,[]);console.log('PASS: real integral snapshot, simulated running second job, refresh resumes polling, explicit plan continuation, no resubmission, two saved results and readable completion.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
