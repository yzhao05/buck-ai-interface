const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage(),fits=[],errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('dialog',dialog=>dialog.dismiss());
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
 const selection=(methods=['weighted_integral'],sources=['measured'])=>({methods,pwm_sources:sources,method_evidence:'User selected the method',pwm_evidence:'User selected PWM'});
 let proposal={open_panel:true,changes:{},start_after_apply:true};
 await require('./harness-browser-fixture.cjs').install(page);
 await page.route('**/api/chat',route=>{const body=route.request().postDataJSON();return route.fulfill({json:{mode:'deepseek',answer:'proposed',ui_action:{id:body.ui_context.request_id,type:'configure_identification',...proposal}}});});
 await page.route('**/api/identify',route=>{fits.push(route.request().postDataJSON());return route.fulfill({json:{job_id:'selection-test'}});});
 await page.route('**/api/jobs/selection-test',route=>route.fulfill({json:{status:'failed',error:'Mock job; no training executed'}}));
 const send=async(text)=>{await page.locator('#chat-input').fill(text);await page.locator('#send-chat').click();await ready();};
 const answer=()=>page.locator('.message.assistant').last().locator('.message-content').innerText();
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.locator('#load-demo').click();await ready();
  await page.evaluate(()=>document.querySelector('#fit-method [value="pann"]').disabled=false);
  await send('加载并检查后直接开始');assert.equal(fits.length,0);
  assert.match(await answer(),/PWM 来源：/);assert.match(await answer(),/计算方法：/);assert.match(await answer(),/尚未启动/);
  proposal={...proposal,changes:{method:'pann'},execution_selection:{...selection(['pann']),pwm_sources:[],pwm_evidence:''}};
  await send('使用PANN');assert.equal(fits.length,0);assert.match(await answer(),/PWM 来源：/);assert.doesNotMatch(await answer(),/计算方法：/);
  proposal={...proposal,changes:{pwm_source:'measured'},execution_selection:{...selection(),methods:[],method_evidence:''}};
  await send('PWM用CSV实际开关状态');assert.equal(fits.length,0);assert.match(await answer(),/计算方法：/);assert.doesNotMatch(await answer(),/PWM 来源：/);
  proposal={open_panel:true,changes:{method:'weighted_integral',pwm_source:'measured'},start_after_apply:true,execution_selection:selection()};
  await send('选用实测PWM和抗噪加权积分，继续开始');assert.equal(fits.length,1);
  assert.equal(fits[0].options.method,'weighted_integral');assert.equal(fits[0].options.pwm_source,'measured');
  proposal={...proposal,changes:{method:'pann'}};
  await send('检查不一致的提案');assert.equal(fits.length,1);assert.match(await answer(),/计算方法：/);
  // Older backend proposals and older stored plans cannot bypass the gate.
  const blocked=await page.evaluate(async()=>{
   const action={id:'legacy-plan',type:'identification_plan',parser:'deepseek',version:2,filename:null,methods:['weighted_integral','pann'],changes:{pwm_source:'measured'}};
   return acceptIdentificationPlan(action,{request_id:action.id,fingerprint:configurationFingerprint()});
  });assert.match(blocked,/PWM 来源：/);assert.match(blocked,/计算方法：/);assert.equal(fits.length,1);
  await page.evaluate(()=>saveIdentificationPlan({id:'old-plan',type:'identification_plan',parser:'deepseek',version:2,filename:null,methods:['weighted_integral','pann'],changes:{pwm_source:'measured'},status:'paused',done:[],jobId:null,settingsApplied:true}));
  await page.evaluate(()=>continueIdentificationPlan());await ready();
  assert.equal(fits.length,1);assert.match(await answer(),/尚未启动/);assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'paused');
  // Explicit auto resolves to the real source for each plan run; prerequisites
  // and failures must also be visible in the main conversation.
  const autoChoices=selection(['weighted_integral','pann'],['auto']);
  for(const source of ['measured','controller']){
   const count=fits.length;
   await page.evaluate(({source,choices})=>{
    const doc=id=>document.getElementById(id);
    state.mapping={...state.mapping,pwm:source==='measured'?doc('map-pwm').value:null};
    doc('pwm-source').value='auto';doc('fit-mode').value='LC';doc('controller-confirmed').value='yes';
    doc('start-ms').value='0';doc('end-ms').value='0.8';
    saveIdentificationPlan({id:'auto-'+source,type:'identification_plan',parser:'deepseek',version:2,filename:null,
     methods:['weighted_integral','pann'],changes:{pwm_source:'auto'},execution_selection:choices,
     status:'paused',done:[],jobId:null,settingsApplied:true});
   },{source,choices:autoChoices});
   await page.evaluate(()=>continueIdentificationPlan());await ready();
   assert.equal(fits.length,count+1);
   assert.equal(fits.at(-1).options.pwm_source,source);
   assert.deepEqual(await page.evaluate(()=>planRuns(currentIdentificationPlan()).map(run=>run.pwm_source)),[source,source]);
   assert.match(await page.locator('#plan-source').innerText(),source==='measured'?/CSV 实际开关状态/:/已知控制器重建/);
   assert.match(await answer(),/Mock job; no training executed/);
  }
  await page.evaluate(()=>{const plan=currentIdentificationPlan();plan.status='cancelled';saveIdentificationPlan(plan);document.getElementById('fit-method').value='weighted_integral';controls();});
  // This rule applies to Agent execution, not the user's manual start button.
  const manualBefore=fits.length;
  await page.locator('#identify').click();await ready();assert.equal(fits.length,manualBefore+1);
  assert.deepEqual(errors,[]);
  console.log('PASS: no silent defaults, ask only missing choices, explicit selection starts once, mismatch/legacy plan blocked in main chat, manual start preserved');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
