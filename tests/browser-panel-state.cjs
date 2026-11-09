/* Inline lists stay open while chatting; keyboard navigation stays local. */
const assert=require('node:assert/strict');
const http=require('node:http');
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const root=path.resolve(__dirname,'../frontend');
 const server=http.createServer(async(req,res)=>{
  try{
   const file=path.resolve(root,'.'+(req.url==='/'?'/index.html':req.url.split('?')[0]));
   if(!file.startsWith(root+path.sep))throw Error('Invalid path');
   res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');
   res.end(await fs.readFile(file));
  }catch{res.statusCode=404;res.end();}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',route=>route.fulfill({json:route.request().url().endsWith('/api/health')?{chat_mode:'deepseek',archive_enabled:false,pann_available:false}:{}}));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked());
  await page.evaluate(()=>{
   identificationPlans.set(identificationPlanKey(),{version:2,methods:['weighted_integral','pann'],pwmSource:'measured',status:'running',done:[]});
   renderIdentificationPlan();
  });
  const planSummary=page.locator('#identification-plan summary');
  await planSummary.click();
  assert(await page.locator('#plan-file').isHidden());
  await page.evaluate(()=>{currentIdentificationPlan().done.push({method:'weighted_integral'});renderIdentificationPlan();});
  assert.match(await planSummary.innerText(),/1 \/ 2/);
  assert(await page.locator('#plan-cancel').isHidden());
  await planSummary.press('Enter');
  assert(await page.locator('#plan-cancel').isVisible());
  await page.evaluate(()=>{identificationPlans.delete(identificationPlanKey());renderIdentificationPlan();});
  await page.evaluate(async()=>{
   for(let i=0;i<12;i++)await BuckAttachments.save(conversationPrefix+activeConversation.id,String(i).padStart(64,'0'),`sample-${i}.csv`,new Blob(['time,i,v\n0,1,2']));
   const result={parameters:{L_H:60e-6,C_F:500e-6,R_ohm:6},metrics:{current_nrmse_pct:1,voltage_nrmse_pct:1},options:{method:'weighted_integral',pwm_source:'measured',mode:'LC'},method:'weighted_integral'};
   activeConversation.experiments=[{id:'panel-result',created:Date.now(),updated:Date.now(),dataset:{filename:'sample.csv',sha256:'a'.repeat(64)},result}];
   persistConversation();state.result=result;state.resultId='panel-result';setIdentificationOpen(true);
  });
  await page.locator('#toggle-csv-library').click();
  await page.waitForFunction(()=>!historyLocked());
  assert.equal(await page.locator('.csv-library-item').count(),12);
  await page.locator('#toggle-result-library').click();
  await page.locator('#ask-error').click();
  const menus=['csv-library-menu','result-library-menu','analysis-menu'];
  async function assertOpen(){for(const id of menus)assert(await page.locator('#'+id).isVisible(),id+' must stay open');}
  await assertOpen();
  await page.locator('#csv-library-list').evaluate(el=>el.scrollTop=80);
  const scroll=await page.locator('#csv-library-list').evaluate(el=>el.scrollTop);
  await page.locator('#chat-input').fill('第一行草稿\n第二行草稿');
  for(const key of ['ArrowUp','ArrowDown','Escape']){
   await page.locator('#chat-input').press(key);
   assert.equal(await page.evaluate(()=>document.activeElement.id),'chat-input','An open list must not steal typing focus');
   await assertOpen();
  }
  assert.equal(await page.locator('#csv-library-list').evaluate(el=>el.scrollTop),scroll);
  await page.evaluate(()=>{state.chatBusy=true;controls();});
  await assertOpen();
  assert(await page.locator('.result-library-item').first().isDisabled());
  assert(await page.locator('.csv-library-item').first().isDisabled());
  assert(await page.locator('#analyze-here').isDisabled());
  for(const [toggle,menu] of [['toggle-csv-library','csv-library-menu'],['toggle-result-library','result-library-menu'],['ask-error','analysis-menu']]){
   await page.locator('#'+toggle).click();
   assert(await page.locator('#'+menu).isHidden(),'Busy tasks must not prevent closing a list');
   assert(await page.locator('#'+toggle).isDisabled(),'Busy tasks still prevent opening a new selection');
  }
  await page.evaluate(()=>{state.chatBusy=false;controls();});
  await page.locator('#toggle-csv-library').click();await page.waitForFunction(()=>!historyLocked());
  await page.locator('#toggle-result-library').click();await page.locator('#ask-error').click();
  await page.locator('#language-toggle').click();await assertOpen();
  await page.locator('.result-library-item').first().focus();await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(()=>document.activeElement.className),'result-library-item');
  await page.keyboard.press('Escape');assert(await page.locator('#result-library-menu').isHidden());
  assert(await page.locator('#analysis-menu').isVisible());
  await page.locator('#analyze-here').focus();await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'analyze-new');
  await page.keyboard.press('Escape');assert(await page.locator('#analysis-menu').isHidden());
  assert(await page.locator('#csv-library-menu').isVisible());
  await page.locator('#toggle-csv-library').focus();await page.keyboard.press('Escape');
  assert(await page.locator('#csv-library-menu').isHidden());
  for(const [toggle,menu] of [['toggle-csv-library','csv-library-menu'],['toggle-result-library','result-library-menu'],['ask-error','analysis-menu']]){
   await page.locator('#'+toggle).click();await page.waitForFunction(()=>!historyLocked());
   await page.locator('#chat-input').click();assert(await page.locator('#'+menu).isVisible());
   await page.locator('#'+toggle).click();assert(await page.locator('#'+menu).isHidden());
  }
  for(const toggle of ['toggle-import','toggle-experiment','toggle-history','toggle-left-pane','toggle-right-pane','toggle-chat-compose']){
   await page.locator('#'+toggle).click();await page.locator('#chat-input').click();
   assert.equal(await page.locator('#'+toggle).getAttribute('aria-expanded'),'false',toggle);
   await page.locator('#'+toggle).click();await page.locator('#chat-input').click();
   assert.equal(await page.locator('#'+toggle).getAttribute('aria-expanded'),'true',toggle);
  }
  for(const id of ['mapping-details','experiment-history','analysis-history','agent-progress']){
   await page.locator('#'+id+' > summary').click();await page.locator('#chat-input').click();
   assert(await page.locator('#'+id).evaluate(el=>el.open),id);
  }
  await page.locator('#toggle-left-pane').click();
  await page.locator('#toggle-right-pane').click();
  async function checkProgressWidth(){
   const boxes=await page.evaluate(()=>['#agent-progress','.chat-compose','.chat-panel'].map(selector=>{
    const {left,right,width}=document.querySelector(selector).getBoundingClientRect();
    return {left,right,width};
   }));
   const [progress,composer,panel]=boxes;
   assert(Math.abs(progress.left-composer.left)<1 && Math.abs(progress.right-composer.right)<1,'Progress must align with both composer edges');
   assert(panel.width-progress.width<60,'Progress must use the available chat width');
  }
  await checkProgressWidth();
  await page.locator('#agent-progress > summary').click();
  await checkProgressWidth();
  await page.setViewportSize({width:390,height:844});
  await checkProgressWidth();
  await page.locator('#toggle-left-pane').click();
  await page.locator('#toggle-right-pane').click();
  await page.locator('#toggle-csv-library').click();await page.waitForFunction(()=>!historyLocked());
  await page.locator('#chat-input').click();assert(await page.locator('#csv-library-menu').isVisible());
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.deepEqual(errors,[]);
  console.log('PASS: persistent CSV/results/analysis lists, typing focus, scoped keyboard, busy guards, language, all fold buttons/details and mobile');
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;});
