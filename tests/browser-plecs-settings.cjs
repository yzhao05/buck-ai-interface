const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const models=fs.mkdtempSync(path.join(os.tmpdir(),'buck-ports-'));
 const executable=path.join(models,'PLECS.exe');
 fs.writeFileSync(executable,''); // This settings test never launches PLECS.
 const proc=spawn('.venv/Scripts/python.exe',['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8017'],
   {windowsHide:true,stdio:'ignore',env:{...process.env,DEEPSEEK_API_KEY:'',APP_ACCESS_TOKEN:'',APP_ENV:'development',CORS_ORIGINS:'http://127.0.0.1:8017'}});
 let browser;
 try{
  for(let i=0;i<60;i++){try{if((await fetch('http://127.0.0.1:8017/api/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,500));}
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1400,height:1000}});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8017');
  await page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
  await page.locator('#open-settings').click();
  await page.locator('#plecs-executable').fill(executable);
  await page.locator('#plecs-model-directory').fill(models);
  await page.screenshot({path:'.tmp/plecs-settings.png'});
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/plecs/settings')&&r.request().method()==='POST');
  await page.locator('#settings-form button[type=submit]').click();assert.equal((await saved).status(),200);
  await page.waitForFunction(()=>!document.getElementById('settings-dialog').open);
  await page.locator('#open-settings').click();
  assert.equal(await page.locator('#plecs-model-directory').inputValue(),models);
  await page.locator('#plecs-binding-model').evaluate(el=>el.closest('details').open=true);
  await page.locator('#plecs-binding-model').fill('test.plecs');
  await page.locator('#plecs-binding-current').fill('1');
  await page.locator('#plecs-binding-voltage').fill('2');
  await page.locator('#plecs-binding-l').fill('L1.L');
  await page.locator('#plecs-binding-c').fill('C1.C');
  await page.locator('#plecs-binding-confirmed').check();
  await page.locator('#plecs-binding-same').check();
  await page.locator('#plecs-binding-voltage').fill('1');
  await page.locator('#plecs-binding-confirmed').check();
  await page.locator('#settings-form button[type=submit]').click();
  await page.waitForFunction(()=>document.getElementById('settings-status').textContent.includes('需要不同的通道号'));
  await page.locator('#plecs-binding-voltage').fill('2');
  await page.locator('#plecs-executable').fill(path.join(models,'missing','PLECS.exe'));
  const rejected=page.waitForResponse(r=>r.url().endsWith('/api/plecs/settings') && r.request().method()==='POST');
  await page.locator('#settings-form button[type=submit]').click();
  assert.equal((await rejected).status(),422);
  assert.equal(await page.evaluate(()=>localPlecsConfig().analysis_binding),null);
  await page.locator('#plecs-executable').fill(executable);
  await page.locator('#plecs-binding-confirmed').check();
  await page.locator('#plecs-binding-same').check();
  await page.locator('#settings-form button[type=submit]').click();
  await page.waitForFunction(()=>!document.getElementById('settings-dialog').open);
  await require('./harness-browser-fixture.cjs').install(page);
  await page.route('**/api/chat',async route=>{
    const ctx=route.request().postDataJSON().ui_context;
    assert.deepEqual(ctx.plecs_ports,{model_name:'test.plecs',current_port:1,voltage_port:2,pwm_port:null});
    await route.fulfill({json:{mode:'deepseek',answer:'',ui_action:{type:'configure_plecs_ports',id:ctx.request_id,
      model_name:'test.plecs',source_sha256:'a'.repeat(64),changes:{current_port:2,voltage_port:1}}}});
  });
  await page.locator('#chat-input').fill('把连接设置的电流改为2、电压改为1');
  await page.locator('#send-chat').click();
  await page.waitForFunction(()=>!historyLocked() && localPlecsConfig().analysis_binding.current_port===2);
  const binding=await page.evaluate(async()=>(await api('/api/plecs/settings')).analysis_binding);
  assert.deepEqual(binding,{model_name:'test.plecs',source_sha256:'a'.repeat(64),current_port:2,voltage_port:1,pwm_port:null,L_parameter:'L1.L',
    C_parameter:'C1.C',confirmed:false,same_circuit_and_conditions:false});
  await page.reload();
  await page.locator('#open-settings').click();
  assert.equal(await page.locator('#plecs-binding-current').inputValue(),'2');
  assert.equal(await page.locator('#plecs-binding-voltage').inputValue(),'1');
  assert.equal(await page.evaluate(()=>readPlecsSettings().analysis_binding.source_sha256),'a'.repeat(64));
  await page.locator('#close-settings').click();
  await page.evaluate(()=>localStorage.removeItem(`buck-plecs:${base}`));
  await page.locator('#open-settings').click();
  await page.waitForFunction(()=>document.getElementById('plecs-binding-current').value==='2');
  await page.locator('#close-settings').click();
  await page.route('https://remote.example/api/bridge/status',route=>route.fulfill({json:{paired:false}}));
  const offline=await page.evaluate(async()=>{
    const config=readPlecsSettings();
    base='https://remote.example';
    localStorage.setItem(`buck-plecs:${base}`,JSON.stringify(config));
    const reply=await applyPlecsPorts({model_name:'test.plecs',changes:{pwm_port:3}}, {plecs:config});
    return {reply,pwm:localPlecsConfig().analysis_binding.pwm_port};
  });
  assert.equal(offline.pwm,3);
  assert.match(offline.reply,/配对连接器后可同步/);
  assert.deepEqual(errors,[]);console.log('PLECS settings browser checks passed');
 }finally{if(browser)await browser.close();proc.kill();fs.unlinkSync(executable);fs.rmdirSync(models);}
})().catch(e=>{console.error(e);process.exitCode=1;});
