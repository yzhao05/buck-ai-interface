const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
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
  await page.locator('#plecs-executable').fill('E:\\Documents\\Plexim\\PLECS 4.9 (64 bit)\\PLECS.exe');
  await page.locator('#plecs-model-directory').fill('D:\\开学作业\\ST4001\\仿真');
  await page.screenshot({path:'.tmp/plecs-settings.png'});
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/plecs/settings')&&r.request().method()==='POST');
  await page.locator('#settings-form button[type=submit]').click();assert.equal((await saved).status(),200);
  await page.waitForFunction(()=>!document.getElementById('settings-dialog').open);
  await page.locator('#open-settings').click();
  assert.equal(await page.locator('#plecs-model-directory').inputValue(),'D:\\开学作业\\ST4001\\仿真');
  assert.deepEqual(errors,[]);console.log('PLECS settings browser checks passed');
 }finally{if(browser)await browser.close();proc.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
