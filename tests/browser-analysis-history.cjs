const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1050}}),errors=[];let fits=0,sent;
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url().endsWith('/api/identify'))fits++;});
 const ready=()=>page.waitForFunction(()=>state.connected && activeConversation && !historyLocked() && !archiveRunning);
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  const interleaved=await page.evaluate(()=>findHistoricalAnalyses({messages:[
   {id:'ordinary-q',role:'user',content:'普通问题'},
   {id:'analysis-q',role:'user',content:'请分析当前辨识结果的误差，并给出有依据的修改计划。'},
   {id:'ordinary-a',role:'assistant',content:'普通回答',response:{request_message_id:'ordinary-q'}},
   {id:'analysis-a',role:'assistant',content:'实际结果分析',response:{request_message_id:'analysis-q',analysis:{request_id:'analysis-q',method:'pann'}}}
  ]}));
  assert.deepEqual(interleaved.map(m=>m.id),['analysis-a']);
  assert.equal(interleaved[0].request_id,'analysis-q');
  assert(await page.evaluate(()=>wantsConversationRetrieval('总结当前会话的所有讨论')));
  assert.deepEqual(await page.evaluate(()=>[
   requestsIdentificationExecution('先辨识参数，再比较之前的结果'),
   requestsIdentificationExecution('First run identification, then compare previous results'),
   requestsIdentificationExecution('总结之前“开始辨识”的回答'),
   requestsIdentificationExecution('不要辨识，只比较之前的结果')
  ]),[true,true,false,false]);
  const ids=await page.evaluate(()=>{
   clearTimeout(archiveTimer);archiveEnabled=false;
   const q=rememberMessage('user','GitHub 部署选哪个分支？');
   const a=rememberMessage('assistant','选择 main，推送后检查自动构建。',{request_message_id:q.id},'DeepSeek');
   const longQ=rememberMessage('user','散热试验的结果是什么？');
   const longA=rememberMessage('assistant','背景资料。'.repeat(2000)+'散热试验：热阻 2.73 K/W，温升 18.5 °C。',{request_message_id:longQ.id},'DeepSeek');
   for(let i=0;i<4;i++){
    rememberMessage('user','请分析当前辨识结果的误差，并给出有依据的修改计划。');
    rememberMessage('assistant',`method=${i%2?'weighted_integral':'pann'}, pwm_source=${i<2?'measured':'controller'} 旧分析 ${i}`,null,'DeepSeek');
   }
   rememberMessage('user','失败测试');rememberMessage('assistant','网络失败',null,'请求未完成');
   for(let i=0;i<8;i++){rememberMessage('user',`其他问题-${i}`);rememberMessage('assistant',`其他回答-${i}`,null,'DeepSeek');}
   return {a:a.id,longA:longA.id};
  });
  await page.reload();await ready();
  assert.equal(await page.locator('#experiment-count-number').textContent(),'0');
  const before=Number(await page.locator('#analysis-count').textContent());assert(before>20);
  await page.route('**/api/chat',route=>{sent=route.request().postDataJSON();return route.continue();});
  await page.locator('#analysis-history summary').click();await page.locator('#compare-analyses').click();
  assert.match(await page.locator('#chat-input').inputValue(),/本会话/);
  await page.locator('#chat-input').fill('保留正在输入的问题');
  await page.locator('#compare-analyses').click();
  assert.equal(await page.locator('#chat-input').inputValue(),'保留正在输入的问题');
  await page.evaluate(()=>sendMessage('总结之前关于 GitHub 部署的讨论'));await ready();
  assert.equal(sent.conversation_messages.length,before);assert(sent.conversation_retrieval_requested);
  assert.equal(sent.experiments.length,0);assert(!sent.history.some(m=>m.content.includes('GitHub')));
  assert(sent.conversation_messages.find(m=>m.id===ids.longA).content.length>8000);
  assert(!sent.conversation_messages.some(m=>m.content==='网络失败'));
  const reply=page.locator('.message.assistant').last();
  assert.match(await reply.innerText(),/main/);assert.match(await reply.innerText(),/已读取材料：2/);
  await reply.locator('.conversation-retrieval button').last().click();
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.messageId),ids.a);assert.equal(fits,0);
  await page.evaluate(()=>sendMessage('查找之前关于散热试验的回答'));await ready();
  assert.match(await page.locator('.message.assistant').last().innerText(),/2\.73 K\/W/);
  assert.match(await page.locator('.message.assistant').last().innerText(),/片段/);
  assert.equal((await page.evaluate(()=>findHistoricalAnalyses(readConversation(activeConversation.id)))).length,4);
  await page.locator('#language-toggle').click();
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#analysis-history').innerText()));
  assert(!/[\u3400-\u9fff]/.test(await page.locator('.conversation-retrieval').last().innerText()));
  await page.locator('#load-demo').click();await page.waitForFunction(()=>state.mapping&&!historyLocked());
  await page.locator('#enter-identification').click();await page.locator('#fit-method').selectOption('weighted_integral');
  await page.locator('#identify').click();await page.waitForFunction(()=>state.result&&!state.job);await ready();
  await page.route('**/api/chat',route=>route.fulfill({json:{answer:'New result analysis',mode:'deepseek'}}));
  await page.locator('#ask-error').click();await ready();
  const latest=await page.evaluate(()=>readConversation(activeConversation.id).messages.at(-1));
  assert.equal(latest.response.analysis.kind,'identification_analysis');assert.equal(latest.response.analysis.method,'weighted_integral');
  assert.equal(latest.response.request_message_id,latest.response.analysis.request_id);assert(latest.response.analysis.filename);
  let csvReads=0;
  page.on('request',r=>{if(/\/api\/datasets\/[^/]+$/.test(r.url()))csvReads++;});
  await page.route('**/api/chat',route=>{sent=route.request().postDataJSON();return route.fulfill({json:{answer:'mixed request received',mode:'local'}});});
  await page.evaluate(()=>sendMessage('先辨识参数，再比较之前的结果'));await ready();
  assert(csvReads>0,'Explicit execution must check/restore the CSV');
  assert(sent.result_id && sent.options && sent.identification_enabled);
  await page.evaluate(()=>{archiveEnabled=true;return synchronizeArchive();});await ready();
  const archived=await page.evaluate(async()=> (await archiveRequest('/conversations/'+activeConversation.id)).record.messages);
  assert(archived.some(m=>m.response?.conversation_retrieval?.read_count===2));
  await page.reload();await ready();assert(await page.locator('.conversation-retrieval').count()>=2);
  await page.evaluate(()=>newConversation());
  assert.equal(await page.locator('#analysis-count').textContent(),'0');assert(await page.locator('#compare-analyses').isDisabled());
  assert.deepEqual(await page.evaluate(()=>conversationMessages(activeConversation)),[]);assert.deepEqual(errors,[]);
  console.log('PASS: general dialogue search, long-tail retrieval, source jumps, bilingual UI, analysis metadata, sync/reload, isolation');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
