const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1050}}), errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const ready=()=>page.waitForFunction(()=>state.connected && activeConversation && !historyLocked());
 try {
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.locator('#load-demo').click();await page.waitForFunction(()=>state.mapping&&!historyLocked());
  await page.locator('#enter-identification').click();await page.locator('#fit-method').selectOption('weighted_integral');
  await page.locator('#identify').click();await page.waitForFunction(()=>state.result && !state.job);await ready();
  const template=await page.evaluate(()=>JSON.parse(JSON.stringify(state.result)));
  // One real run provides the result schema. Simulate completion of the other
  // method/PWM combinations to test persistence, independently of training speed.
  for (const [index,method,pwm] of [[1,'pann','measured'],[2,'pann','controller'],[3,'weighted_integral','controller']]) {
   await page.evaluate(({method,pwm})=>{
    clearTimeout(archiveTimer);archiveEnabled=false;
    $('fit-method').value=method;$('pwm-source').value=pwm;
    if(pwm==='controller'){$('fit-mode').value='LC';prepareControllerDefaults();$('controller-confirmed').value='yes';}
    invalidateResult();controls();persistConversation();
   },{method,pwm});
   const config=await page.evaluate(()=>({options:options(),mapping:state.mapping}));
   const result={...template,...config,method,parameters:{...template.parameters,L_H:(60+index)*1e-6}};
   await page.route(`**/api/jobs/history-test-${index}`,route=>route.fulfill({json:{status:'complete',result}}));
   await page.evaluate(id=>watchJob(id),`history-test-${index}`);
  }
  assert.equal(await page.locator('#experiment-count-number').textContent(),'4');
  await page.locator('#truth-l').fill('60');await page.locator('#truth-c').fill('500');
  await page.locator('#calculate-parameter-error').click();
  const runs=await page.evaluate(()=>readConversation(activeConversation.id).experiments);
  assert.equal(runs.length,4);assert.equal(runs.filter(r=>r.ground_truth).length,1);
  assert(runs.find(r=>r.id==='history-test-3').ground_truth);
  assert.equal(new Set(runs.map(r=>r.result.options.method+':'+r.result.options.pwm_source)).size,4);
  // A stale metadata writer must not erase append-only experiment keys.
  await page.evaluate(()=>{
    const old=JSON.parse(localStorage.getItem(conversationPrefix+activeConversation.id));
    old.experiments=[];old.meta_updated+=1;localStorage.setItem(conversationPrefix+activeConversation.id,JSON.stringify(old));
  });
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).experiments.length),4);
  await page.locator('#language-toggle').click();
  assert.equal(await page.locator('#experiment-count').textContent(),'Saved experiments');
  assert.match(await page.locator('#experiment-ai-note').textContent(),/experiment summaries/);
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#experiment-history').innerText()),'History labels and scope note must be translated');
  await page.evaluate(()=>{archiveEnabled=true;return synchronizeArchive();});await ready();
  const saved=await page.evaluate(async()=> (await archiveRequest('/conversations/'+activeConversation.id)).record.experiments);
  assert.equal(saved.length,4);
  // Simulate a newer truth edit synced from a second browser while this card
  // still contains its old value. Passive persistence and reload must preserve it.
  await page.evaluate(()=>{
    clearTimeout(archiveTimer);archiveEnabled=false;
    const run=structuredClone(activeConversation.experiments.find(r=>r.id===state.resultId));
    run.updated+=100;run.ground_truth.L_H=70e-6;
    localStorage.setItem(conversationPrefix+activeConversation.id+':experiment:'+run.id,JSON.stringify(run));
    persistConversation();
  });
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).experiments.find(r=>r.id==='history-test-3').ground_truth.L_H),70e-6);
  await page.reload();await ready();await page.waitForFunction(()=>state.resultId==='history-test-3');
  assert.equal(await page.locator('#truth-l').inputValue(),'70');
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).experiments.find(r=>r.id==='history-test-3').ground_truth.L_H),70e-6);
  // Cache expiry must not remove completed experiment summaries.
  await page.route('**/api/jobs/**',route=>route.fulfill({status:404,json:{detail:'expired'}}));
  await page.route('**/api/experiments',route=>route.fulfill({json:{experiments:[]}}));
  await page.reload();await ready();
  assert.equal(await page.locator('#experiment-count-number').textContent(),'4');
  let sent;
  let csvRequests=0;
  await page.route('**/api/datasets/**',route=>{csvRequests++;return route.fulfill({status:404,json:{detail:'expired'}});});
  await page.route('**/api/chat',route=>{sent=route.request().postDataJSON();return route.continue();});
  await page.locator('#enter-identification').click();
  await page.evaluate(()=>sendMessage('对比这四次实验'));
  assert.equal(sent.experiments.length,4);
  assert.equal(sent.experiments.filter(r=>r.ground_truth).length,1);
  assert.equal(csvRequests,0,'Saved comparisons must not require a live CSV');
  for(const run of sent.experiments)assert.deepEqual(Object.keys(run).sort(),['dataset','ground_truth','id','method','parameters','pwm_source']);
  assert.match(await page.locator('.message.assistant').last().innerText(),/已保存 4 次实验/);
  assert.equal(sent.result_id,null);
  assert.equal(sent.identification_enabled,true,'An open identification panel does not authorize executing a comparison');
  assert(sent.dataset_id && sent.mapping,'Retain current context for explicit mixed requests');
  // The pure comparison above reached the real backend and returned no job.
  // Check that an explicit mixed command is not disabled in the frontend.
  await page.route('**/api/chat',route=>{sent=route.request().postDataJSON();return route.fulfill({json:{answer:'mixed request received',mode:'local',job_id:null}});});
  await page.evaluate(()=>sendMessage('先辨识参数，再比较这四次实验'));
  assert.equal(sent.identification_enabled,true);
  assert(sent.dataset_id && sent.mapping && sent.options);
  await page.evaluate(()=>newConversation());
  assert.equal(await page.locator('#experiment-count-number').textContent(),'0');
  assert.equal(await page.evaluate(()=>chatExperiments().length),0);
  assert.deepEqual(errors,[]);
  console.log('PASS: four method/PWM runs, per-run truth, stale tab/reload truth protection, backend sync, expiry/reload, approved chat fields, real local comparison without CSV, conversation isolation, English');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
