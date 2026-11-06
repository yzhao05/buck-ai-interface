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
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',route=>route.fulfill({json:route.request().url().endsWith('/api/health')?{chat_mode:'deepseek',archive_enabled:false,pann_available:false}:{}}));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked());
  const form=page.locator('#chat-form'),input=page.locator('#chat-input'),toggle=page.locator('#toggle-chat-compose');
  await input.fill('第一行草稿\n第二行草稿');
  await form.screenshot({path:path.join(__dirname,'../.tmp/composer-expanded.png')});
  const expanded=(await form.boundingBox()).height;
  await toggle.click();
  assert.equal(await input.inputValue(),'第一行草稿\n第二行草稿');
  assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  assert((await form.boundingBox()).height<60);
  assert((await form.boundingBox()).height<expanded/2);
  await input.fill('收起后仍然可以输入文字');
  await form.screenshot({path:path.join(__dirname,'../.tmp/composer-collapsed.png')});
  await page.reload();
  await page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked());
  assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  assert.equal(await input.inputValue(),'收起后仍然可以输入文字');
  await input.press('End');await input.press('Shift+Enter');
  assert.equal(await toggle.getAttribute('aria-expanded'),'true');
  assert((await input.inputValue()).includes('\n'));
  await toggle.click();
  await page.evaluate(()=>{window.BuckHarness.request=async body=>{window.reviewSent=body;return {mode:'deepseek',answer:'已收到文字'};};});
  await input.fill('单行输入发送测试');await page.locator('#send-chat').click();
  await page.waitForFunction(()=>!state.chatBusy&&window.reviewSent);
  assert.equal(await page.evaluate(()=>reviewSent.message),'单行输入发送测试');
  assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  await page.evaluate(()=>setChatImages([{name:'pixel.png',data_url:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII='}]));
  assert(await page.locator('#compact-chat-images').isVisible());
  assert.equal(await page.locator('#compact-chat-images').innerText(),'▧ 1');
  await page.locator('#compact-chat-images').click();
  assert(await page.locator('#chat-image-list').isVisible());
  await toggle.click();await page.locator('#language-toggle').click();
  assert.equal(await toggle.getAttribute('aria-label'),'Expand input box');
  await page.setViewportSize({width:390,height:844});
  await input.fill('手机输入也保持单行 '+ 'longtext'.repeat(50));
  await form.screenshot({path:path.join(__dirname,'../.tmp/composer-mobile.png')});
  const bounds=await form.boundingBox();
  assert(bounds.height<60&&bounds.x>=0&&bounds.x+bounds.width<=390);
  assert(await page.locator('#send-chat').isVisible());
  await toggle.click();
  await page.evaluate(()=>setIdentificationOpen(true));
  for(const language of ['en','zh']){
   if(await page.locator('html').getAttribute('lang')!==language)await page.locator('#language-toggle').click();
   for(const width of [1440,1191,768,390,320]){
    await page.setViewportSize({width,height:1000});
    await page.locator('#reasoning-mode').selectOption('thinking');
    const row=await page.locator('#attach-chat-image,#chat-use-circuit,#reasoning-mode').evaluateAll(elements=>elements.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,right:r.right,cy:r.y+r.height/2};}));
    assert(row[0].right<=row[1].x && row[1].right<=row[2].x,'Image buttons stay left of the mode selector');
    assert(Math.max(...row.map(r=>r.cy))-Math.min(...row.map(r=>r.cy))<2,'Toolbar controls stay on one row');
    assert(await page.locator('#reasoning-mode').evaluate(el=>{
     const style=getComputedStyle(el),canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');ctx.font=style.font;
     return el.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight)>=ctx.measureText(el.selectedOptions[0].text).width+20;
    }),'The selected mode needs room for its full label and dropdown arrow');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   }
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: desktop/mobile compact layout, typing/sending, multiline draft, Shift+Enter, reload, attachments and translations');
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;});
