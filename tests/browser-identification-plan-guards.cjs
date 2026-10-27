const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1550,height:1100}}),errors=[],fits=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url().endsWith('/api/identify'))fits.push(r.postDataJSON());});
 await require('./plan-chat-fixtures.cjs')(page);
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
 const paused=()=>page.waitForFunction(()=>currentIdentificationPlan()?.status==='paused'&&!historyLocked());
 const send=async text=>{await page.locator('#chat-input').fill(text);await page.locator('#send-chat').click();};
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.locator('#load-demo').click();await ready();
  await page.route('**/api/identify',route=>route.request().postDataJSON().options.method==='pann'
    ? route.fulfill({json:{job_id:'failed-pann'}}):route.continue());
  await page.route('**/api/jobs/failed-pann',route=>route.fulfill({json:{status:'failed',error:'PANN test failure'}}));
  await send('固定 R=7，运行两种方法');await paused();
  assert.equal(fits.length,2);assert.equal(await page.evaluate(()=>currentIdentificationPlan().done.length),1);
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).experiments.length),1);
  assert.match(await page.locator('#plan-note').innerText(),/PANN test failure/);
  await page.locator('#plan-continue').click();await paused();assert.equal(fits.length,2,'A failed submitted job is not blindly retried');
  await page.locator('#fixed-r').fill('8');await page.locator('#plan-continue').click();await paused();
  assert.match(await page.locator('#plan-note').innerText(),/数据或参数已更改/);assert.equal(fits.length,2);
  await page.locator('#plan-cancel').click();
  const duplicate=await page.evaluate(async()=>{
    const action={id:'replay-plan',type:'identification_plan',parser:'deepseek',version:2,methods:['weighted_integral','pann'],filename:null,changes:{pwm_source:'measured'}};
    let ctx={request_id:action.id,fingerprint:configurationFingerprint()};
    await applyIdentificationAction(action,ctx);
    ctx.fingerprint=configurationFingerprint();return applyIdentificationAction(action,ctx);
  });assert.match(duplicate,/已处理/);assert.equal(fits.length,2);
  await page.locator('#plan-cancel').click();
  const stale=await page.evaluate(async()=>{
    const ctx={request_id:'stale-plan',fingerprint:configurationFingerprint()};document.getElementById('vin').value='29';
    return applyIdentificationAction({id:ctx.request_id,type:'identification_plan',parser:'deepseek',version:2,methods:['weighted_integral','pann'],filename:null,changes:{pwm_source:'measured'}},ctx);
  });assert.match(stale,/已变化/);assert.equal(fits.length,2);
  const csv=fs.readFileSync('examples/plecs_buck_case16a.csv','utf8');
  for(const data of [csv,csv+'\n']){
    await page.locator('#csv-file').setInputFiles({name:'ambiguous.csv',mimeType:'text/csv',buffer:Buffer.from(data)});await ready();
  }
  await page.locator('#new-chat').click();await ready();
  await send('请使用 ambiguous.csv 完成两种方式的参数识别');await paused();
  assert.match(await page.locator('#plan-note').innerText(),/同名但内容不同/);assert.equal(fits.length,2);
  await page.locator('#language-toggle').click();
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#identification-plan').innerText()));
  const intents=await page.evaluate(()=>[
    '请比较已运行的两种方法结果','I have run both methods. Compare their results.',
    '两种方法运行了吗？','请运行两种方法，然后比较结果',
  ].map(requestsIdentificationExecution));
  assert.deepEqual(intents,[false,false,false,true]);
  await page.locator('#plan-cancel').click();
  // A malformed saved plan must not crash controls()/page restoration.
  assert.equal(await page.evaluate(()=>{
    const key=identificationPlanKey();sessionStorage.setItem(key,'{"status":"running"}');identificationPlans.delete(key);
    controls();return currentIdentificationPlan();
  }),null);
  await page.evaluate(async()=>{
    const ctx={request_id:'quota-plan',fingerprint:configurationFingerprint()};
    await applyIdentificationAction({id:ctx.request_id,type:'identification_plan',parser:'deepseek',version:2,methods:['weighted_integral','pann'],filename:null,changes:{}},ctx);
    const original=Storage.prototype.setItem;
    Storage.prototype.setItem=function(key,value){
      if(key.startsWith('buck-plan:'))throw new DOMException('Quota exhausted','QuotaExceededError');
      return original.call(this,key,value);
    };
    try{await continueIdentificationPlan();}finally{Storage.prototype.setItem=original;}
  });
  assert.equal(await page.evaluate(()=>Boolean(historyLocked())),false,'Storage failure must release the plan lock');
  assert.equal(await page.locator('#new-chat').isDisabled(),false);
  assert.match(await page.locator('#plan-note').innerText(),/Unable to save plan progress/);
  assert.equal(fits.length,2);
  assert.deepEqual(errors,[]);
  console.log('PASS: partial result retained, no failed-job retry, frozen inputs, replay/stale protection, ambiguous CSV, read-only intent, corrupt storage, quota failure unlock and English.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
