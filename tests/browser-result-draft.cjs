const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.evaluate(()=>{archiveEnabled=false;clearTimeout(archiveTimer);});
  await page.locator('#load-demo').click();await page.waitForFunction(()=>state.mapping&&!historyLocked());
  await page.locator('#enter-identification').click();await page.locator('#fit-method').selectOption('weighted_integral');
  await page.locator('#identify').click();await page.waitForFunction(()=>state.result&&!state.job);await ready();
  await page.locator('#truth-l').fill('60');await page.locator('#truth-c').fill('500');
  await page.locator('#calculate-parameter-error').click();await page.locator('#view-tail').click();
  const snapshot=()=>page.evaluate(()=>({result:state.result,id:state.resultId,chart:state.chart,truth:state.truthComparison,zoom:state.zoom,
   values:['result-l','result-c','result-r','result-badge','result-notes','rmse-i','rmse-v','parameter-error-l','parameter-error-c'].map(id=>$(id).textContent),
   disabled:['ask-error','download-report','calculate-parameter-error'].map(id=>$(id).disabled)}));
  const original=await snapshot();
  assert(await page.locator('#identify').isDisabled());
  // Unsubmitted invalid fields must not clear results or block read-only analysis/export.
  await page.locator('#vin').fill('');assert.deepEqual(await snapshot(),original);
  assert(!(await page.locator('#identify').isDisabled()));
  const chatWait=page.waitForRequest(r=>r.url().endsWith('/api/chat'));
  await page.locator('#ask-error').click();await page.locator('#analyze-here').click();const chat=(await chatWait).postDataJSON();await ready();
  assert.equal(chat.result_id,original.id);assert.deepEqual(chat.options,original.result.options);
  assert.deepEqual(chat.mapping,original.result.mapping);
  assert.equal(chat.identification_enabled,false,'Analyzing the displayed result cannot rerun its old settings');
  assert.deepEqual(chat.ground_truth,{result_id:original.id,...original.truth.ground_truth});
  assert.match(await page.locator('.message.assistant').last().innerText(),/电感相对误差/);
  await page.locator('#vin').fill(String(original.result.options.vin));assert(await page.locator('#identify').isDisabled());

  for(const [id,value] of [['vin','29'],['frequency','80'],['fixed-r','7'],['pann-epochs','200'],['start-ms','0.01'],['end-ms','0.7'],['train-fraction','60']]){
   // Hidden PANN inputs are also exercised without launching an expensive training job.
   await page.evaluate(({id,value})=>{const field=$(id);field.value=value;field.dispatchEvent(new Event('input',{bubbles:true}));},{id,value});
   assert.deepEqual(await snapshot(),original,`Draft ${id} must not modify completed result`);
  }
  for(const [id,value] of [['fit-mode','LC'],['fit-method','pann'],['pann-state','measured'],['pann-cycles','2'],['pwm-source','controller'],['controller-confirmed','yes']]){
   await page.evaluate(({id,value})=>{const field=$(id);field.value=value;field.dispatchEvent(new Event(id==='pwm-source'||id==='controller-confirmed'?'change':'input',{bubbles:true}));},{id,value});
   assert.deepEqual(await snapshot(),original,`Draft ${id} must not modify completed result`);
  }
  await page.evaluate(()=>document.dispatchEvent(new Event('buck-circuit-change')));
  assert.deepEqual(await snapshot(),original,'Circuit edits preserve previous result');
  await page.locator('#view-tail').click();assert.deepEqual(await page.evaluate(()=>state.zoom),original.zoom,'Tail view uses result frequency');
  await page.locator('#language-toggle').click();
  assert.match(await page.locator('#identify-complete-hint').innerText(),/Previous results remain/);
  await page.locator('#language-toggle').click();
  // Return to a valid weighted-integral draft with a single changed operating condition.
  await page.evaluate(opts=>{
   const fields={vin:'29',frequency:String(opts.frequency_hz/1000),'fixed-r':String(opts.resistance_ohm),'fit-mode':opts.mode,'fit-method':opts.method,
    'start-ms':opts.start_s===null?'':String(opts.start_s*1000),'end-ms':opts.end_s===null?'':String(opts.end_s*1000),
    'train-fraction':String(opts.train_fraction*100),'pwm-source':opts.pwm_source,'controller-confirmed':opts.controller_confirmed?'yes':'no',
    'pann-state':opts.pann_initial_state,'pann-cycles':String(opts.pann_window_cycles)};
   for(const [id,value] of Object.entries(fields))$(id).value=value;
   controls();persistConversation();
  },original.result.options);
  await page.reload();await ready();await page.waitForFunction(()=>state.result);
  assert.equal(await page.locator('#vin').inputValue(),'29');assert.equal(await page.evaluate(()=>state.resultId),original.id);
  assert.deepEqual(await page.evaluate(()=>state.result),original.result);
  assert.deepEqual(await page.evaluate(()=>state.truthComparison),original.truth);
  await page.locator('#enter-identification').click();assert(!(await page.locator('#identify').isDisabled()));
  const jsonWait=page.waitForEvent('download',d=>d.suggestedFilename().endsWith('.json'));
  const pdfWait=page.waitForRequest(r=>r.url().endsWith('/api/reports/pdf'));
  await page.locator('#download-report').click();const json=await jsonWait;
  const stream=await json.createReadStream(),parts=[];for await(const part of stream)parts.push(part);
  const report=JSON.parse(Buffer.concat(parts));
  assert.deepEqual(report.result.options,original.result.options);
  assert.deepEqual((await pdfWait).postDataJSON().result.options,original.result.options);
  await page.waitForFunction(()=>!reportExportBusy);
  const links=await page.locator('#report-downloads').innerHTML();
  await page.locator('#vin').fill('30');assert.equal(await page.locator('#report-downloads').innerHTML(),links,'Draft edit keeps downloadable reports');
  assert(await page.locator('#parameter-error-values').isVisible());
  const nextFit=page.waitForRequest(r=>r.url().endsWith('/api/identify'));
  let release;const submitted=new Promise(resolve=>{release=resolve;});
  await page.route('**/api/identify',async route=>{await submitted;await route.continue();});
  await page.locator('#identify').click();const body=(await nextFit).postDataJSON();
  assert.equal(body.options.vin,30);
  assert.equal(await page.evaluate(()=>state.result),null);
  assert(await page.locator('#ask-error').isDisabled());assert(await page.locator('#report-downloads').isHidden());
  release();await page.waitForFunction(()=>state.result&&!state.job);await ready();
  assert.notEqual(await page.evaluate(()=>state.resultId),original.id);
  assert.equal(await page.evaluate(()=>state.result.options.vin),30);
  assert(await page.locator('#identify').isDisabled());assert.equal(await page.locator('#truth-l').inputValue(),'');
  assert.deepEqual(errors,[]);
  console.log('PASS: draft/result isolation, invalid input, all identification controls, AI context, result-frequency zoom, bilingual hint, reload, PDF/JSON snapshot, explicit resubmission');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
