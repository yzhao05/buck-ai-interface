const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage(),errors=[];let analysisResponse;
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/health',async route=>{const r=await route.fetch(),json=await r.json();json.archive_enabled=false;await route.fulfill({json});});
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked());
 const analyze=async()=>{
  const request=page.waitForRequest(r=>r.url().endsWith('/api/chat'));
  const response=page.waitForResponse(r=>r.url().endsWith('/api/chat'));
  await page.locator('#ask-error').click();await page.locator('#analyze-here').click();
  const body=(await request).postDataJSON();analysisResponse=await (await response).json();await ready();return body;
 };
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.locator('#load-demo').click();await page.waitForFunction(()=>state.mapping&&!historyLocked());
  await page.locator('#enter-identification').click();
  await page.locator('#fit-method').selectOption('weighted_integral');
  await page.locator('#fit-mode').selectOption('LCR');
  await page.locator('#identify').click();await page.waitForFunction(()=>state.result&&!state.job);
  const originalId=await page.evaluate(()=>state.resultId);
  assert.equal((await analyze()).result_id,originalId);
  // Reproduce the user's preceding answer without calling an external model.
  await page.evaluate(()=>rememberMessage('assistant','OLD_LCR_RESULT: C=489.8 μF，R=5.7 Ω。'));
  await page.locator('#fit-mode').selectOption('LC');await page.locator('#fixed-r').fill('6');
  // Editing a draft alone still analyzes the displayed old result.
  const before=await analyze();assert.equal(before.result_id,originalId);assert.equal(before.options.mode,'LCR');
  await page.locator('#identify').click();await page.waitForFunction(id=>state.result&&!state.job&&state.resultId!==id,originalId);
  await page.locator('#truth-l').fill('60');await page.locator('#truth-c').fill('500');
  await page.locator('#calculate-parameter-error').click();
  const current=await page.evaluate(()=>({id:state.resultId,result:state.result,truth:state.truthComparison}));
  for(let i=0;i<2;i++){
   const body=await analyze();
   assert.equal(body.current_result_analysis,true);
   assert.equal(body.result_id,current.id);assert.equal(body.options.mode,'LC');assert.equal(body.options.resistance_ohm,6);
   assert.deepEqual(body.options,current.result.options);assert.deepEqual(body.ground_truth,{result_id:current.id,...current.truth.ground_truth});
   assert.deepEqual(body.history,[]);assert.deepEqual(body.conversation_messages,[]);
   // Complete summaries reach the backend for grouping; only the current
   // configuration's aggregate may enter the current-result model context.
   assert.equal(body.experiments.length,2);
   assert.equal(analysisResponse.repeatability.groups.length,1);
   assert.equal(analysisResponse.repeatability.groups[0].mode,'LC');
   assert.deepEqual(analysisResponse.repeatability.groups[0].representative_ids,[current.id]);
   assert.equal(body.identification_enabled,false);
   assert.match(await page.locator('.message.assistant').last().innerText(),/R = 6 Ω（固定已知）/);
  }
  assert(await page.evaluate(()=>activeConversation.messages.some(m=>m.content.includes('OLD_LCR_RESULT'))),'Old answers remain saved');
  assert(await page.evaluate(()=>activeConversation.experiments.some(r=>r.id===state.resultId)));
  // A reference to the preceding conclusion is a result follow-up, not a search
  // that drops the current result ID. Both Chinese and English stay anchored.
  for(const question of ['为什么？','请复查刚才的结论','Any other possible causes?']){
   const req=page.waitForRequest(r=>r.url().endsWith('/api/chat'));
   const response=page.waitForResponse(r=>r.url().endsWith('/api/chat'));
   await page.locator('#chat-input').fill(question);await page.locator('#send-chat').click();
   const body=(await req).postDataJSON();const answer=await (await response).json();await ready();
   assert.equal(body.current_result_analysis,false);assert.equal(body.result_id,current.id);
   assert.equal(body.conversation_retrieval_requested,false);assert.equal(body.options.mode,'LC');
   assert(body.conversation_messages.some(m=>m.analysis?.result_id===current.id));
   assert.equal(answer.route.task,'diagnosis');
  }
  // General comparison still receives prior experiments and searchable messages.
  const pastRequest=page.waitForRequest(r=>r.url().endsWith('/api/chat'));
  await page.locator('#chat-input').fill('为什么上次的 LCR 结果不同？');await page.locator('#send-chat').click();
  const past=(await pastRequest).postDataJSON();await ready();
  assert.equal(past.result_id,null,'An explicit earlier-run question must not be bound to the current LC result');
  assert.equal(past.conversation_retrieval_requested,true);
  assert(past.conversation_messages.some(m=>m.content.includes('OLD_LCR_RESULT')));
  const compareRequest=page.waitForRequest(r=>r.url().endsWith('/api/chat'));
  await page.locator('#chat-input').fill('比较本会话的两次实验');await page.locator('#send-chat').click();
  const compare=(await compareRequest).postDataJSON();await ready();
  assert.equal(compare.current_result_analysis,false);assert(compare.experiments.length>=2);assert(compare.conversation_messages.length>0);
  assert.deepEqual(errors,[]);
  console.log('PASS: LCR to fixed-R LC, two successive current-result analyses, exact ID/options/truth, draft preservation and independent historical comparison');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
