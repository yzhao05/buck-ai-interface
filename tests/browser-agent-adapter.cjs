const assert=require('node:assert/strict');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const server=spawn('.venv/Scripts/python.exe',['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8022'],
  {windowsHide:true,stdio:'ignore',env:{...process.env,DEEPSEEK_API_KEY:'',APP_ACCESS_TOKEN:'',APP_ENV:'development',CORS_ORIGINS:'http://127.0.0.1:8022'}});
 let browser;
 try{
  for(let i=0;i<60;i++){try{if((await fetch('http://127.0.0.1:8022/api/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,500));}
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage(),errors=[],alerts=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',async d=>{alerts.push(d.message());await d.dismiss();});
  await page.goto('http://127.0.0.1:8022');
  await page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
  await page.evaluate(()=>{
   clearTimeout(archiveTimer);archiveEnabled=false;
   window.sentRequests=[];window.cancelNext=false;
   window.BuckHarness.request=async body=>{
    sentRequests.push(structuredClone(body));
    const response={mode:'deepseek',answer:'Agent response'};
    if(cancelNext){response.ui_action={id:body.ui_context.request_id,type:'cancel_pending_identification'};cancelNext=false;}
    return response;
   };
  });
  for(const mode of ['fast','thinking']){
   await page.locator('#reasoning-mode').selectOption(mode);
   for(const text of ['不要打开文件，不运行仿真，只检查连接和目录。','比较之前结果','继续解释','Run identification, then compare.','请分析当前辨识结果的误差，并给出有依据的修改计划。']){
    await page.evaluate(text=>sendMessage(text),text);
    const body=await page.evaluate(()=>sentRequests.at(-1));
    assert.equal(body.message,text);assert.equal(body.reasoning_mode,mode);
    assert.equal(body.current_result_analysis,false);assert(body.ui_context);
    assert(Array.isArray(body.conversation_messages));assert.equal(body.conversation_retrieval_requested,undefined);
   }
  }
  // Attachments supply evidence; they do not remove configuration capabilities.
  await page.evaluate(()=>{
   setChatImages([{name:'circuit.png',data_url:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/xZkAAAAASUVORK5CYII='}]);
   return sendMessage('结合这张图和之前的讨论。');
  });
  const pictured=await page.evaluate(()=>sentRequests.at(-1));
  assert.equal(pictured.images.length,1);assert(pictured.ui_context);
  await page.evaluate(()=>{pendingConfigurationStart={fingerprint:configurationFingerprint()};return sendMessage('解释“取消辨识”这句话');});
  assert(await page.evaluate(()=>Boolean(pendingConfigurationStart)),'Text must not mutate pending work before the Agent responds');
  await page.evaluate(()=>{cancelNext=true;return sendMessage('剩下的工作留到以后');});
  assert.equal(await page.evaluate(()=>pendingConfigurationStart),null);
  await page.locator('#load-demo').click();await page.waitForFunction(()=>state.mapping&&!historyLocked());
  await page.locator('#enter-identification').click();await page.locator('#fit-method').selectOption('weighted_integral');
  await page.locator('#identify').click();await page.waitForFunction(()=>state.result&&!state.job&&!historyLocked());
  let sourceReads=0;
  await page.route('**/api/datasets/*',route=>{
   if(route.request().method()==='GET'){sourceReads++;return route.fulfill({status:404,json:{detail:'expired cache'}});}
   return route.continue();
  });
  for(const text of ['不要重新辨识，只比较历史','运行下一次辨识','为什么上次失败？']){
   await page.evaluate(text=>sendMessage(text),text);
   const body=await page.evaluate(()=>sentRequests.at(-1));
   assert(body.result_id&&body.ui_context&&body.experiments.length);
   assert(body.enable_diagnostic_controls);
   // A displayed snapshot is read-only; new executions use ui_context and
   // configure_identification rather than the snapshot's legacy start tool.
   assert.equal(body.identification_enabled,false);
   assert.equal(body.saved_result.id,body.result_id);
   assert.equal(body.ui_context.options.method,'weighted_integral');
  }
  // Only the explicit result-analysis UI action narrows the scope.
  await page.evaluate(()=>sendMessage('分析这个结果',{analysisRequest:true}));
  const scoped=await page.evaluate(()=>sentRequests.at(-1));
  assert(scoped.current_result_analysis);assert.equal(scoped.ui_context,undefined);
  assert.deepEqual(scoped.conversation_messages,[]);assert.deepEqual(scoped.experiments,[]);
  assert.equal(scoped.identification_enabled,false);
  assert.equal(sourceReads,0,'Chat must preserve the displayed result when the CSV cache expires');
  assert.deepEqual(errors,[]);assert.deepEqual(alerts,[]);
  console.log('PASS: fast/thinking request parity, image context, Agent cancellation, result/form separation, explicit scope');
 }finally{
  if(browser)await browser.close();
  if(server.pid)try{execFileSync('taskkill',['/PID',String(server.pid),'/T','/F'],{stdio:'ignore',windowsHide:true});}catch{}
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
