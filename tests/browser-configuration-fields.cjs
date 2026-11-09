const assert=require('node:assert/strict');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const proc=spawn('.venv/Scripts/python.exe',['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8018'],{windowsHide:true,stdio:'ignore',env:{...process.env,DEEPSEEK_API_KEY:'',APP_ACCESS_TOKEN:'',APP_ENV:'development',CORS_ORIGINS:'http://127.0.0.1:8018'}});
 let browser;
 try{
  for(let i=0;i<60;i++){try{if((await fetch('http://127.0.0.1:8018/api/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,500));}
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage(),errors=[],fits=[],chats=[];let action={};
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().endsWith('/api/identify'))fits.push(r.postDataJSON());});
  await require('./harness-browser-fixture.cjs').install(page);
  await page.route('**/api/chat',async route=>{
   const body=route.request().postDataJSON();chats.push(body);
   const methods=action.methods || (body.ui_context.paused_plan?['weighted_integral','pann']:[action.changes?.method||body.ui_context.options.method]);
   // Exercise field/circuit confirmation with execution choices already made.
   await route.fulfill({json:{mode:'deepseek',answer:'Validated',sources:[],tool_calls:[],job_id:null,ui_action:{id:body.ui_context.request_id,type:'configure_identification',open_panel:false,changes:{},start_after_apply:false,
     execution_selection:{methods,pwm_sources:[action.changes?.pwm_source||body.ui_context.pwm_source_setting||body.ui_context.options.pwm_source],method_evidence:'Previously confirmed in this fixture',pwm_evidence:'Previously confirmed in this fixture'},...action}}});
  });
  const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
  const send=async(text,patch)=>{action=patch;await page.locator('#chat-input').fill(text);await page.locator('#send-chat').click();await ready();};
  const answer=()=>page.locator('.message.assistant').last().locator('.message-content').innerText();
  await page.goto('http://127.0.0.1:8018');await ready();
  await page.evaluate(()=>{toggleSection('toggle-experiment','experiment-content','实验配置',true);toggleSection('toggle-import','import-content','导入波形',true);});
  await page.locator('#load-demo').click();await ready();
  await send('训练轮数300，时间0–0.8ms，滤波modified-sinc窗口4周期',{changes:{epochs:300,start_s:0,end_s:.0008,pwm_denoise:'modified_sinc',pwm_filter_cycles:4}});
  assert.equal(await page.locator('#pann-epochs').inputValue(),'300');assert.equal(await page.locator('#end-ms').inputValue(),'0.8');assert.equal(fits.length,0);
  const before=await page.evaluate(()=>Object.fromEntries(experimentFields.map(id=>[id,$(id).value])));
  await send('只改结束时间为0.7ms',{changes:{end_s:.0007}});
  assert.deepEqual(await page.evaluate(()=>Object.fromEntries(experimentFields.map(id=>[id,$(id).value]))),{...before,'end-ms':'0.7'});
  await send('无效区间与参数组合',{changes:{start_s:.002,vin:40}});
  assert.equal(await page.locator('#vin').inputValue(),before.vin);assert.match(await answer(),/无效/);
  await send('关闭滤波',{changes:{pwm_denoise:'none'}});assert.equal(await page.locator('#pwm-filter-cycles').inputValue(),'4');
  assert.equal(chats.at(-1).ui_context.options.pwm_filter_cycles,4);
  await send('设置所有基础选项',{changes:{vin:35,frequency_hz:80000,train_fraction:.75,pann_initial_state:'measured',pann_window_cycles:3,pwm_source:'measured',mode:'LC',method:'weighted_integral',resistance_ohm:8}});
  assert.deepEqual(await page.evaluate(()=>[options().vin,options().frequency_hz,options().train_fraction,options().pann_initial_state,options().pann_window_cycles]),[35,80000,.75,'measured',3]);
  const controller=await page.evaluate(()=>BuckCircuit.read());
  await send('仅修改R1为12kΩ',{changes:{controller:{R1_ohm:12000}}});
  assert.deepEqual(await page.evaluate(()=>BuckCircuit.read()),{...controller,R1_ohm:12000});
  const all=await page.evaluate(()=>Object.fromEntries(BuckCircuit.specs.map(([key,,,scale,value])=>[key,Number((value*scale).toPrecision(15))])));
  await send('设置所有控制器字段',{changes:{controller:all}});assert.deepEqual(await page.evaluate(()=>BuckCircuit.read()),all);
  // Manual input inventory: every experimental control maps to a chat field.
  assert.deepEqual(await page.evaluate(()=>experimentFields.filter(id=>!['reasoning-mode','controller-image','controller-image-name','controller-image-library','controller-parameters','controller-confirmed','selected-result','chat-images'].includes(id)&&!Object.values(configurationFields).some(s=>s[0]===id))),[]);
  const mapping=await page.evaluate(()=>state.mapping);
  await send('修改时间单位为ms',{mapping:{time_unit:'ms'}});assert.equal(await page.locator('#time-unit').inputValue(),'ms');
  await send('恢复s',{mapping:{time_unit:'s'}});assert.deepEqual(await page.evaluate(()=>state.mapping),mapping);
  await send('无效列名',{mapping:{voltage:'invented'}});assert.equal(await page.locator('#map-voltage').inputValue(),mapping.voltage);
  // Retained files are selected by exact catalog IDs, never arbitrary paths.
  await page.evaluate(()=>{
   const canvas=document.createElement('canvas');canvas.width=2;canvas.height=2;
   const image={name:'uploaded-buck.png',data_url:canvas.toDataURL('image/png')};
   rememberMessage('user','uploaded circuit',null,'',[image]);persistConversation();
  });
  await send('列出配置上下文',{});
  const catalog=chats.at(-1).ui_context.catalog,circuit=catalog.find(x=>x.kind==='circuit'),csv=catalog.find(x=>x.kind==='csv');assert(circuit&&csv);
  assert(!JSON.stringify(catalog).includes('base64'));assert(!JSON.stringify(chats.at(-1).ui_context).includes('access-token'));
  await send('切换电路',{assets:{circuit_id:circuit.id}});assert.equal(await page.locator('#controller-image-name').inputValue(),'uploaded-buck.png');
  assert.equal(await page.locator('#controller-confirmed').inputValue(),'no');
  await send('恢复默认电路',{assets:{circuit_id:null}});assert.equal(await page.locator('#controller-image').inputValue(),'');
  await send('切换到已上传CSV',{assets:{csv_id:csv.id}});assert.equal(await page.locator('#end-ms').inputValue(),'0.7');
  assert.match(await answer(),/列与单位映射/);assert.match(await answer(),/列映射已应用/);
  assert.match(await answer(),/实验设置/);assert.match(await answer(),/Vin = 35 V/);
  assert.match(await answer(),/结束时间 \(ms\) = 0.7/);
  assert.match(await answer(),/开始参数辨识/);assert.equal(fits.length,0);
  assert.doesNotMatch(await answer(),/列映射尚未应用/);
  await send('选择不存在文件并修改电压',{assets:{csv_id:'invented'},changes:{vin:99}});assert.equal(await page.locator('#vin').inputValue(),'35');
  // A real weighted-integral fit verifies the submitted configuration.
  await send('恢复工况并开始辨识',{changes:{vin:28,frequency_hz:100000,resistance_ohm:6,start_s:0,end_s:.0008,pwm_denoise:'sg'},start_after_apply:true});
  assert.equal(fits.length,1);assert.equal(fits[0].options.end_s,.0008);assert.equal(fits[0].options.method,'weighted_integral');
  assert(await page.evaluate(()=>Boolean(state.result)));
  const completedId=await page.evaluate(()=>state.resultId);
  await page.locator('#controller-confirmed').evaluate(el=>el.value='yes');
  await send('时间单位保持s',{mapping:{time_unit:'s'}});
  assert.equal(await page.evaluate(()=>state.resultId),completedId,'Applying the same mapping must preserve the completed result');
  assert.equal(await page.locator('#controller-confirmed').inputValue(),'yes','Unchanged mapping must preserve circuit confirmation');
  assert.doesNotMatch(await answer(),/控制器接线与参数尚未确认/);
  await send('真实L为1mH，C为1000uF，计算误差',{analysis:{truth_L_H:.001,truth_C_F:.001,calculate_error:true}});
  assert.equal(await page.locator('#truth-l').inputValue(),'1000');assert(await page.evaluate(()=>Boolean(parameterComparison())));
  await send('选择诊断参考',{analysis:{reference_csv_id:csv.id}});assert.match(await answer(),/同一实验/);assert.equal(await page.locator('#diagnostic-reference-confirm').isChecked(),false);
  await send('确认参考来自同一实验同一工况',{analysis:{reference_confirmed:true}});assert.equal(await page.locator('#diagnostic-reference-confirm').isChecked(),true);
  await send('清空参考和真实C',{analysis:{reference_csv_id:null,truth_C_F:null}});assert.equal(await page.locator('#truth-c').inputValue(),'');
  // Requesting a run asks for circuit confirmation first; a plain confirmation resumes once.
  await page.locator('#controller-confirmed').evaluate(el=>el.value='no');
  await send('改用重建PWM并开始',{changes:{pwm_source:'controller'},start_after_apply:true});
  assert.equal(fits.length,1);assert.match(await answer(),/回复.*确认/);
  // The Agent receives the pending state and returns the typed cancellation;
  // the frontend must not reinterpret the user's wording before the request.
  await send('取消开始辨识',{type:'cancel_pending_identification'});assert.equal(chats.at(-1).ui_context.pending_start,true);
  assert.equal(await page.evaluate(()=>pendingConfigurationStart),null);assert.equal(fits.length,1);
  await send('开始辨识',{changes:{},start_after_apply:true});assert.match(await answer(),/回复.*确认/);
  assert.equal(chats.at(-1).ui_context.pending_start,false);
  await page.route('**/api/identify',route=>route.fulfill({json:{job_id:'mock-training'}}));
  await page.route('**/api/jobs/mock-training',route=>route.fulfill({json:{status:'failed',error:'test stop'}}));
  await send('是的',{changes:{controller_confirmed:true},start_after_apply:true});
  assert.equal(chats.at(-1).ui_context.pending_start,true);assert.equal(fits.length,2);assert.equal(fits[1].options.controller_confirmed,true);
  assert.equal(fits[1].options.pwm_source,'controller');
  // A confirmation resumes the existing multi-run plan, never a standalone fit.
  await page.locator('#controller-confirmed').selectOption('no');
  await send('运行重建PWM下两种方法',{type:'identification_plan',parser:'deepseek',version:2,filename:null,methods:['weighted_integral','pann'],changes:{pwm_source:'controller',mode:'LC'}});
  assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'paused');assert.equal(fits.length,2);
  await send('确认',{changes:{controller_confirmed:true},start_after_apply:true});
  assert.equal(chats.at(-1).ui_context.pending_start,true);assert.equal(fits.length,3);
  assert.equal(fits[2].options.method,'weighted_integral');
  assert.equal(await page.evaluate(()=>currentIdentificationPlan().jobId),null);
  assert.match(await page.locator('#plan-note').innerText(),/test stop/);
  await page.locator('#new-chat').click();await ready();
  assert.equal(await page.evaluate(()=>state.dataset),null);
  await send('使用已上传CSV运行两种方法',{type:'identification_plan',parser:'deepseek',version:2,filename:null,methods:['weighted_integral','pann'],assets:{csv_id:csv.id},changes:{pwm_source:'controller',mode:'LC'}});
  assert.equal(await page.evaluate(()=>state.dataset.sha256),csv.id.slice(4));
  assert(await page.evaluate(()=>Boolean(state.mapping)));
  assert.equal(await page.evaluate(()=>currentIdentificationPlan().status),'paused');assert.equal(fits.length,3);
  assert.deepEqual(errors,[]);
  console.log('PASS: all experimental fields, partial edits, units, atomic rejection, uploaded CSV/circuit selection, truth/reference analysis, confirmation pause/resume, real fit payload');
 }finally{if(browser)await browser.close();if(proc.exitCode===null)try{execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{stdio:'ignore',windowsHide:true});}catch{proc.kill();}}
})().catch(e=>{console.error(e);process.exitCode=1;});
