const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1050}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const ready=()=>page.waitForFunction(()=>state.connected && activeConversation && !historyLocked() && !archiveRunning);
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.evaluate(()=>{archiveEnabled=false;clearTimeout(archiveTimer);});
  await page.locator('#load-demo').click();await page.waitForFunction(()=>state.mapping&&!historyLocked());
  await page.locator('#enter-identification').click();await page.locator('#fit-method').selectOption('weighted_integral');
  await page.locator('#identify').click();await page.waitForFunction(()=>state.result&&!state.job);await ready();
  const template=await page.evaluate(()=>structuredClone(state.result));
  const dataset=await page.evaluate(()=>({filename:state.dataset.filename,sha256:state.dataset.sha256}));
  let mode='transient',jobId='recover-transient',reads=0,posts=0,active=false,release=false;
  await page.route('**/api/identify',async route=>{
   posts++;active=true;
   if(mode==='lost')return route.abort('failed');
   if(mode==='conflict')return route.fulfill({status:409,json:{detail:'当前会话已有辨识任务，请等待完成。'}});
   return route.fulfill({status:202,json:{job_id:jobId,status:'queued'}});
  });
  await page.route('**/api/jobs/**',async route=>{
   if(route.request().url().endsWith('/active'))return route.fulfill({json:{job:active?{job_id:jobId,status:'running'}:null}});
   reads++;
   if(mode==='expired')return route.fulfill({status:404,json:{detail:'任务不存在或已过期。'}});
   if(mode==='failed')return route.fulfill({json:{job_id:jobId,status:'failed',error:'test numerical failure'}});
   if(mode==='transient' && reads===1)return route.fulfill({status:503,json:{detail:'temporary gateway error'}});
   if(mode==='transient' && reads===2)return route.abort('failed');
   if(release){active=false;return route.fulfill({json:{job_id:jobId,status:'complete',stage:'complete',result:template}});}
   return route.fulfill({json:{job_id:jobId,status:'running',stage:'pann_training'}});
  });
  const begin=async()=>page.evaluate(()=>{invalidateResult();controls();void startFit();});
  await begin();await page.waitForFunction(()=>state.job==='recover-transient');
  await page.waitForFunction(()=>$('job-status').textContent.includes('自动重连'));
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).attachment.jobId),jobId);
  assert(await page.locator('#identify').isDisabled());
  await page.locator('#language-toggle').click();
  assert.match(await page.locator('#job-status').innerText(),/Reconnecting/);
  await page.locator('#language-toggle').click();
  await page.waitForTimeout(4000);assert(reads>=3);assert.equal(posts,1);
  release=true;await page.waitForFunction(()=>state.resultId==='recover-transient'&&!state.job);await ready();

  mode='lost';jobId='recover-lost-response';reads=0;release=false;
  await begin();await page.waitForFunction(()=>state.job==='recover-lost-response');
  assert.equal(posts,2);assert.match(await page.locator('#notice-text').innerText(),/已找回/);
  assert(await page.locator('#identify').isDisabled());
  release=true;await page.waitForFunction(()=>state.resultId==='recover-lost-response'&&!state.job);await ready();

  mode='conflict';jobId='recover-old-config';reads=0;release=false;active=true;
  let historyStarted,finishHistory;
  const historyPending=new Promise(resolve=>{historyStarted=resolve;});
  await page.route('**/api/experiments',async route=>{
   if(mode==='conflict')await new Promise(resolve=>{finishHistory=resolve;historyStarted();});
   return route.fulfill({json:{experiments:[{id:'recover-old-config',created:Date.now(),updated:0,dataset,result:template,ground_truth:null}]}});
  });
  await page.locator('#vin').fill('30');await begin();await page.waitForFunction(()=>state.job==='recover-old-config');
  assert(await page.locator('#identify').isDisabled());assert.equal(posts,3);
  release=true;await historyPending;
  assert.equal(await page.evaluate(()=>state.job),'recover-old-config');
  assert(await page.locator('#identify').isDisabled(),'Keep the job locked until history recovery finishes');
  finishHistory();await page.waitForFunction(()=>!state.job&&!state.busy);
  await page.waitForFunction(()=>activeConversation.experiments.some(r=>r.id==='recover-old-config'));
  assert.equal(await page.locator('#vin').inputValue(),'30');assert.equal(await page.evaluate(()=>state.result),null);
  assert.match(await page.locator('#job-status').innerText(),/当前配置尚未辨识/);
  assert(!(await page.locator('#identify').isDisabled()));

  // No persisted task ID: restore must discover the backend's live task.
  mode='running';jobId='recover-after-reload';reads=0;release=false;active=true;
  assert.equal(await page.evaluate(()=>{persistConversation();return readConversation(activeConversation.id).attachment.jobId;}),null);
  await page.reload();await page.waitForFunction(()=>state.connected && state.job==='recover-after-reload');
  assert.equal(posts,3,'Refresh must not submit another identification request');
  assert(await page.locator('#identify').isDisabled());
  mode='expired';active=false;
  await page.waitForFunction(()=>!state.job&&!state.busy);
  assert.match(await page.locator('#notice-text').innerText(),/不存在或已过期/);
  const readsAtExpiry=reads;await page.waitForTimeout(1500);assert.equal(reads,readsAtExpiry);
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).attachment.jobId),null);
  mode='failed';jobId='failed-job';
  await page.evaluate(()=>watchJob('failed-job'));
  assert.equal(await page.evaluate(()=>state.job),null);
  assert.match(await page.locator('#notice-text').innerText(),/test numerical failure/);
  assert.deepEqual(errors,[]);
  console.log('PASS: transient polling retry, persistent job lock/ID, bilingual status, lost POST recovery, 409 recovery without config overwrite, reload discovery, terminal 404/failure');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
