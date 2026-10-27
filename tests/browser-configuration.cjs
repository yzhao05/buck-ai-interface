const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[],fits=[],chats=[];
 let proposal={open_panel:true,changes:{method:'weighted_integral',mode:'LC',resistance_ohm:6},start_after_apply:true};
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url().endsWith('/api/identify'))fits.push(r.postDataJSON());});
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
 const send=async text=>{await page.locator('#chat-input').fill(text);await page.locator('#send-chat').click();await ready();};
 const answer=()=>page.locator('.message.assistant').last().locator('.message-content').innerText();
 await page.route('**/api/chat',async route=>{
   const body=route.request().postDataJSON();chats.push(body);
   if(body.message==='请解释 Buck 原理')return route.continue();
   await route.fulfill({json:{mode:'deepseek',answer:'Validated, not executed yet',sources:[],tool_calls:[],job_id:null,
     route:{task:'configuration',label:'辨识配置',mode:'agent',secondary_tasks:[]},
     ui_action:{id:body.ui_context.request_id,type:'configure_identification',...proposal}}});
 });
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.locator('#toggle-experiment').click();await page.locator('#toggle-left-pane').click();
  await send('使用加权积分，固定 R=6，进入辨识并开始');
  assert(await page.locator('#identification-form').isVisible());assert.match(await answer(),/上传 CSV/);assert.equal(fits.length,0);
  await page.locator('#load-demo').click();await ready();
  await page.locator('#pann-epochs').evaluate(el=>el.value='230');
  await send('按这些设置开始辨识');
  assert.equal(fits.length,1);assert.equal(fits[0].options.mode,'LC');assert.equal(fits[0].options.resistance_ohm,6);
  assert.equal(fits[0].options.method,'weighted_integral');
  assert.equal(chats.at(-1).ui_context.options.epochs,230);
  const result=await page.evaluate(()=>({id:state.resultId,parameters:state.result.parameters}));
  assert(result.id);assert.match(await answer(),/任务已提交/);
  assert.deepEqual(await page.evaluate(()=>{
    const el=document.getElementById('fixed-r'),old=el.value;el.disabled=true;
    const valid=value=>{el.value=value;try{options();return true;}catch{return false;}};
    const result=[valid('0.01'),valid('0.0105'),valid('100001')];el.value=old;controls();return result;
  }),[false,true,false],'R limits match backend even when the input is disabled');
  // Unfinished hidden fields must not poison read-only chat validation.
  await page.locator('#pann-epochs').evaluate(el=>el.value='');
  await send('请解释 Buck 原理');
  assert.equal(chats.at(-1).ui_context.options.epochs,100);
  assert(!/greater than|请求未完成|Input should/.test(await answer()));
  await page.locator('#fixed-r').fill('100001');
  await send('请解释 Buck 原理');assert.equal(chats.at(-1).ui_context,undefined);
  assert(!/greater than|请求未完成|Input should/.test(await answer()));
  await page.locator('#fixed-r').fill('6');
  await page.locator('#pann-epochs').evaluate(el=>el.value='230');
  // Setting-only changes preserve the completed result and its action buttons.
  proposal={open_panel:true,changes:{mode:'LCR',resistance_ohm:8},start_after_apply:false};
  await send('切换到 LCR，把 R 设置为8，暂不开始');
  assert.equal(fits.length,1);assert.equal(await page.locator('#fit-mode').inputValue(),'LCR');
  assert.deepEqual(await page.evaluate(()=>({id:state.resultId,parameters:state.result.parameters})),result);
  assert.equal(await page.locator('#ask-error').isDisabled(),false);
  assert.equal(await page.locator('#download-report').isDisabled(),false);
  assert.match(await answer(),/未启动实验/);
  // Next fit submits the form as changed, not the old LC result context.
  proposal={open_panel:true,changes:{},start_after_apply:true};
  await send('开始参数辨识');assert.equal(fits.length,2);assert.equal(fits[1].options.mode,'LCR');assert.equal(fits[1].options.resistance_ohm,8);
  await send('开始参数辨识');assert.equal(fits.length,2);assert.match(await answer(),/已有完成结果/);
  // Old responses are never replayed when restoring the conversation.
  await page.reload();await ready();assert.equal(fits.length,2);
  // A unique request ID is consumed before application, including no-fit actions.
  const duplicate=await page.evaluate(async()=>{
    const ctx={request_id:'duplicate-check',fingerprint:configurationFingerprint()};
    const action={id:ctx.request_id,type:'configure_identification',open_panel:true,changes:{resistance_ohm:9},start_after_apply:false};
    await applyIdentificationAction(action,ctx);
    ctx.fingerprint=configurationFingerprint();
    return applyIdentificationAction(action,ctx);
  });assert.match(duplicate,/已处理/);assert.equal(fits.length,2);
  for(const mutation of ['settings','dataset','conversation']){
    const stale=await page.evaluate(async kind=>{
      const ctx={request_id:'stale-'+kind,fingerprint:configurationFingerprint()};
      const old={vin:document.getElementById('vin').value,dataset:state.dataset.dataset_id,conversation:activeConversation.id};
      if(kind==='settings')document.getElementById('vin').value='31';
      if(kind==='dataset')state.dataset.dataset_id='changed';
      if(kind==='conversation')activeConversation.id='changed';
      const message=await applyIdentificationAction({id:ctx.request_id,type:'configure_identification',open_panel:true,changes:{mode:'LC'},start_after_apply:true},ctx);
      document.getElementById('vin').value=old.vin;state.dataset.dataset_id=old.dataset;activeConversation.id=old.conversation;
      return message;
    },mutation);assert.match(stale,/已变化/);
  }
  // An applied mapping is mandatory, even if dropdowns still display old columns.
  await page.evaluate(()=>{state.mapping=null;controls();});
  await send('开始参数辨识');assert.equal(fits.length,2);assert.match(await answer(),/列映射/);
  await page.locator('#apply-mapping').click();await ready();
  await page.locator('#pwm-source').selectOption('controller');await ready();
  await page.locator('#controller-confirmed').selectOption('yes');
  proposal={open_panel:true,changes:{mode:'LC',resistance_ohm:6},start_after_apply:true};
  await send('固定 R 为6，然后开始');assert.equal(fits.length,2);
  assert.equal(await page.locator('#controller-confirmed').inputValue(),'no');assert.match(await answer(),/核对并确认/);
  // PANN can be selected without training, leaving the completed result displayed.
  await page.evaluate(()=>document.querySelector('#fit-method [value="pann"]').disabled=false);
  proposal={open_panel:true,changes:{method:'pann'},start_after_apply:false};
  await send('选择 PANN');assert.equal(await page.locator('#fit-method').inputValue(),'pann');assert.equal(fits.length,2);
  await page.locator('#language-toggle').click();
  await send('Use PANN without starting');assert.match(await answer(),/Identification settings applied/);assert.match(await answer(),/No experiment was started/);
  // A backend active job blocks changes, even before the page has resumed it.
  await page.route('**/api/jobs/active',r=>r.fulfill({json:{job:{job_id:'existing'}}}));
  await page.route('**/api/jobs/existing',r=>r.fulfill({json:{status:'failed',error:'Test existing task'}}));
  proposal={open_panel:true,changes:{resistance_ohm:10},start_after_apply:true};
  await send('Set R to 10 and start');assert.equal(await page.locator('#fixed-r').inputValue(),'6');assert.equal(fits.length,2);
  assert.match(await answer(),/already running/);
  assert.deepEqual(errors,[]);
  console.log('PASS: configure-only, real fits use changed configuration, unchanged draft epochs, old result preserved, prerequisites, stale context, replay, backend active task, PANN selection, English');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
