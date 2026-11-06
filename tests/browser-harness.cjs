const assert=require('node:assert/strict');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const server=spawn('.venv/Scripts/python.exe',['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8020'],{windowsHide:true,stdio:'ignore',env:{...process.env,DEEPSEEK_API_KEY:'',APP_ACCESS_TOKEN:'',APP_ENV:'development',CORS_ORIGINS:'http://127.0.0.1:8020'}});
 let browser;
 try{
  for(let i=0;i<60;i++){try{if((await fetch('http://127.0.0.1:8020/api/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,500));}
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1450,height:1000}}),errors=[],instructions=[];
  page.on('pageerror',error=>errors.push(error.message));
  const alerts=[];page.on('dialog',async dialog=>{alerts.push(dialog.message());await dialog.dismiss();});
  await page.goto('http://127.0.0.1:8020');
  await page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
  await page.locator('#open-settings').click();
  await page.locator('#bridge-pair').click();
  await page.waitForFunction(()=>document.getElementById('bridge-instructions').textContent.includes('backend.plecs_companion'));
  assert.match(await page.locator('#bridge-instructions').innerText(),/--owner/);
  const pairing=await page.locator('#bridge-instructions').innerText();
  await page.locator('#bridge-check').click();
  await page.waitForFunction(()=>document.getElementById('settings-status').textContent.includes('已配对'));
  assert.equal(await page.locator('#bridge-instructions').innerText(),pairing);
  const confirmations=await page.evaluate(()=>{
    document.getElementById('plecs-binding-confirmed').checked=true;
    document.getElementById('plecs-binding-same').checked=true;
    document.getElementById('plecs-binding-current').dispatchEvent(new Event('input'));
    return [document.getElementById('plecs-binding-confirmed').checked,document.getElementById('plecs-binding-same').checked];
  });
  assert.deepEqual(confirmations,[false,false]);
  await page.locator('#close-settings').click();
  await page.route('**/api/agent/tasks',route=>route.fulfill({json:{id:'browser-harness',state:'running'}}));
  await page.route('**/api/agent/tasks/browser-harness/steer',route=>{instructions.push(route.request().postDataJSON());return route.fulfill({json:{status:'queued'}});});
  let recoveryEvents=null;
  await page.route('**/api/agent/tasks/browser-harness/events',async route=>{
   if(recoveryEvents)return route.fulfill({contentType:'application/x-ndjson',body:recoveryEvents.map(e=>JSON.stringify(e)).join('\n')+'\n'});
   await new Promise(r=>setTimeout(r,1200));
   const result={mode:'deepseek',answer:'对照显示模型偏差，已停止等价重复实验。',sources:[],tool_calls:[{name:'run_plecs_experiment',status:'已执行'}],diagnosis:{stop_reason:'systematic_floor'}};
   const events=[{kind:'status',data:{text:'读取证据'}},{kind:'text_delta',data:{text:'模型偏差'}},{kind:'result',data:result},{kind:'end',data:{state:'completed',result}}];
   await route.fulfill({contentType:'application/x-ndjson',body:events.map(e=>JSON.stringify(e)).join('\n')+'\n'});
  });
  await page.locator('#chat-input').fill('检查 C 误差并做有区分力的对照');await page.locator('#send-chat').click();
  await page.waitForFunction(()=>state.agentTask==='browser-harness');
  await page.locator('#agent-instruction').fill('证据足够就停止');await page.locator('#agent-steer').click();
  await page.waitForFunction(()=>!state.chatBusy);
  assert.equal(instructions[0].message,'证据足够就停止');
  assert.match(await page.locator('.message.assistant').last().innerText(),/模型偏差/);
  assert.match(await page.locator('#agent-preview').innerText(),/模型偏差/);
  const old={mode:'deepseek',answer:'old attempt'};
  recoveryEvents=[{kind:'result',data:old},{kind:'attempt_start',data:{text:'恢复中'}},{kind:'end',data:{state:'cancelled',result:null}}];
  const cancelled=await page.evaluate(async()=>{try{await BuckHarness.request({message:'resume'});return 'accepted';}catch(error){return error.taskState;}});
  assert.equal(cancelled,'cancelled');
  // Exercise the real chat catch branch, not only the stream reader.
  await page.locator('#chat-input').fill('停止状态回归检查');await page.locator('#send-chat').click();
  await page.waitForFunction(()=>!state.chatBusy);
  assert.match(await page.locator('.message.assistant').last().innerText(),/任务已停止，可恢复/);
  assert.equal(alerts.length,0);
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).messages.at(-1).response.task_state),'cancelled');
  recoveryEvents=[{kind:'end',data:{state:'interrupted',result:null}}];
  await page.locator('#chat-input').fill('中断状态回归检查');await page.locator('#send-chat').click();
  await page.waitForFunction(()=>!state.chatBusy);
  assert.match(await page.locator('.message.assistant').last().innerText(),/任务已中断，可恢复/);
  assert.equal(alerts.length,0);
  recoveryEvents=[{kind:'result',data:old}];
  const disconnected=await page.evaluate(async()=>{try{await BuckHarness.request({message:'resume'});return 'accepted';}catch(error){return error.taskState;}});
  assert.equal(disconnected,'disconnected');
  await page.locator('#chat-input').fill('断线状态回归检查');await page.locator('#send-chat').click();
  await page.waitForFunction(()=>!state.chatBusy);
  assert.match(await page.locator('.message.assistant').last().innerText(),/后台任务可能仍在运行/);
  assert.equal(alerts.length,0);
  const providerError={mode:'provider_error',answer:'',provider_error:'AI service is not configured. Please set DEEPSEEK_API_KEY on the backend and try again.'};
  recoveryEvents=[{kind:'end',data:{state:'failed',result:providerError}}];
  await page.locator('#chat-input').fill('缺少 API 回归检查');await page.locator('#send-chat').click();
  await page.waitForFunction(()=>!state.chatBusy);
  assert.deepEqual(alerts,[providerError.provider_error]);
  recoveryEvents=[{kind:'text_delta',data:{text:'old text'}},{kind:'attempt_start',data:{text:'恢复中'}},{kind:'text_delta',data:{text:'new text'}},{kind:'end',data:{state:'completed',result:{mode:'deepseek',answer:'new answer'}}}];
  const recovered=await page.evaluate(()=>BuckHarness.request({message:'resume'}));
  assert.equal(recovered.answer,'new answer');
  // A task pointer can survive a redeploy that removed its old backend task.
  await page.route('**/api/agent/tasks/missing-task',route=>route.fulfill({status:404,json:{detail:'Task not found'}}));
  await page.route('**/api/agent/tasks/latest',route=>route.fulfill({json:{task:{id:'recovered-task',state:'completed'}}}));
  await page.route('**/api/agent/tasks/recovered-task/events',route=>route.fulfill({contentType:'application/x-ndjson',body:recoveryEvents.map(e=>JSON.stringify(e)).join('\n')+'\n'}));
  await page.evaluate(()=>localStorage.setItem(`buck-agent:${base}:${sessionId}`,'missing-task'));
  await page.evaluate(()=>BuckHarness.recover());
  assert.match(await page.locator('.message.assistant').last().innerText(),/new answer/);
  assert.equal(await page.evaluate(()=>localStorage.getItem(`buck-agent:${base}:${sessionId}`)),null);
  const quotaResult=await page.evaluate(async()=>{
    const original=Storage.prototype.setItem;
    Storage.prototype.setItem=function(key,value){if(key.startsWith('buck-agent:'))throw new DOMException('Full','QuotaExceededError');return original.call(this,key,value);};
    try{return await BuckHarness.request({message:'storage-full'});}finally{Storage.prototype.setItem=original;}
  });
  assert.equal(quotaResult.answer,'new answer','A local storage error must not detach a successfully submitted task');
  assert.equal(await page.locator('#agent-preview').innerText(),'new text');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:'.tmp/harness-browser.png',fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS: pairing, streaming, steering, cancelled/disconnected recovery rejects stale results, resumed preview resets, no browser errors');
 }finally{
  if(browser)await browser.close();
  if(server.exitCode===null)try{execFileSync('taskkill',['/PID',String(server.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}catch{server.kill();}
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
