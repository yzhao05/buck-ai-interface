const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1650,height:1100}}),errors=[];let fits=0;
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url().endsWith('/api/identify'))fits++;});
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning,{},{timeout:90000});
 const analyze=async()=>{
  const response=page.waitForResponse(r=>r.url().endsWith('/api/chat'),{timeout:90000});
  await page.locator('#ask-error').click();await page.locator('#analyze-here').click();
  const data=await (await response).json();await ready();return data;
 };
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  const clean=fs.readFileSync('examples/plecs_buck_case16a.csv','utf8');
  const noisy=clean.trim().split(/\r?\n/).map((line,i)=>{if(i<2)return line;const row=line.split(',');row[1]=String(Number(row[1])+.0001*Math.sin(i*1.37));row[2]=String(Number(row[2])+.0001*Math.cos(i*1.73));return row.join(',');}).join('\n');
  await page.locator('#csv-file').setInputFiles({name:'noisy.csv',mimeType:'text/csv',buffer:Buffer.from(noisy)});await ready();
  await page.locator('#enter-identification').click();await page.locator('#fit-method').selectOption('pann');
  await page.locator('#fit-mode').selectOption('LC');await page.locator('#pann-epochs').fill('10');
  await page.locator('#end-ms').fill('0.8');
  await page.locator('#identify').click();await page.waitForFunction(()=>state.result&&!state.job,{},{timeout:90000});await ready();
  const before=await page.evaluate(()=>({id:state.resultId,p:state.result.parameters,dataset:state.dataset.dataset_id,metrics:state.result.metrics}));
  await page.locator('summary').filter({hasText:'诊断对照与可选参考'}).click();
  await page.locator('#diagnostic-reference-file').setInputFiles({name:'clean.csv',mimeType:'text/csv',buffer:Buffer.from(clean)});
  await page.waitForFunction(()=>Boolean(selectedDiagnosticReference()));
  const first=await analyze();assert.equal(first.control_checks.status,'completed');
  assert.equal(first.control_checks.reference.status,'not_provided','Unconfirmed reference is not sent');
  assert.equal(Object.keys(first.control_checks.cases).length,4);
  for(const [key,value] of Object.entries(before.metrics))assert(Math.abs(first.control_checks.cases.pann_pann_state.heldout_measured[key]-value)<1e-8,'Replay must match original PANN rollout');
  await page.locator('#diagnostic-reference-confirm').check();
  const second=await analyze();assert.equal(second.control_checks.reference.status,'evaluated_user_declared_reference');
  assert(second.control_checks.cases.pann_pann_state.heldout_reference);
  assert.equal(second.control_checks.physical_causes_verified.length,0);
  assert.deepEqual(await page.evaluate(()=>state.result.parameters),before.p);assert.equal(fits,1);
  assert.equal(await page.evaluate(()=>state.dataset.dataset_id),before.dataset);
  assert(second.answer.indexOf('已验证的数值效应')<second.answer.indexOf('可能原因'));
  const evidence=page.locator('.diagnostic-controls-evidence').last();await evidence.locator('summary').click();
  await page.locator('#language-toggle').click();
  assert(!/[\u3400-\u9fff]/.test(await evidence.innerText()));
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#diagnostic-reference-file').locator('..').innerText()));
  await page.screenshot({path:'runtime/diagnostic-controls-en.png',fullPage:true});
  await page.reload();await ready();assert.equal(await page.evaluate(()=>selectedDiagnosticReference()),null);
  assert(await page.locator('.diagnostic-controls-evidence').count()>0,'Execution record survives history restore');
  assert.deepEqual(errors,[]);
  console.log('PASS: actual PANN replay, four swaps, explicit reference confirmation, clean-reference metrics, no retraining, unchanged result, bilingual evidence and persisted chat');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
