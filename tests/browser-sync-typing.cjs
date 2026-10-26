// Run against the isolated backend on :8001. Exercise real debounce + slow sync.
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1050}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const ready=()=>page.waitForFunction(()=>state.connected && activeConversation && !historyLocked() && !archiveRunning);
 try {
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.evaluate(()=>{clearTimeout(archiveTimer);archiveTimer=null;});
  let requests=0;
  await page.route('**/api/archive**',async route=>{
   requests++;
   await new Promise(resolve=>setTimeout(resolve,150));
   await route.continue();
  });
  const input=page.locator('#chat-input');
  await input.fill('纵向横向对比');
  await page.evaluate(()=>{$('chat-input').setSelectionRange(2,4);});
  // Longer than the former one-second auto-sync timer: a pause is not blur.
  await page.waitForTimeout(1600);
  assert.equal(requests,0,'Typing must not start a locking background sync');
  assert(await input.isEnabled());assert(await page.locator('#send-chat').isEnabled());
  assert(await page.locator('#load-demo').isEnabled());
  assert.deepEqual(await input.evaluate(e=>[e.selectionStart,e.selectionEnd]),[2,4]);
  assert(await input.evaluate(e=>e===document.activeElement),'Focus must stay in the editor');
  await page.keyboard.insertText('测试');
  const draft=await input.inputValue();
  assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem(conversationPrefix+activeConversation.id)).draft),draft);
  await page.evaluate(()=>{
   document.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
   window.dispatchEvent(new Event('online'));
   document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.waitForTimeout(1200);
  assert.equal(requests,0,'Online and visibility sync must respect composition');
  // Direct background retries also have to check focus, not just the debounce.
  await page.evaluate(()=>synchronizeArchive({background:true,preferRemote:true}));
  assert.equal(requests,0);
  assert(await page.evaluate(()=>archivePreferRemote),'Deferred bootstrap preference must survive');
  await page.evaluate(()=>$('chat-input').blur());
  await page.waitForTimeout(1200);
  assert.equal(requests,0,'Composition must finish before deferred sync starts');
  await page.evaluate(()=>document.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})));
  await page.waitForFunction(()=>archiveRunning);await ready();
  assert(requests>0);
  assert.equal(await input.inputValue(),draft);
  const snapshot=await page.evaluate(()=>({id:activeConversation.id,key:archiveKey}));
  const remote=await page.request.get('http://127.0.0.1:8001/api/archive/conversations/'+snapshot.id,{headers:{'X-Archive-Key':snapshot.key}});
  assert.equal((await remote.json()).record.draft,draft,'Deferred sync must upload the latest draft');
  // A previously queued timer must also be canceled when the user starts editing.
  await page.evaluate(()=>scheduleArchiveSync());
  const before=requests;
  await input.focus();await page.waitForTimeout(1400);
  assert.equal(requests,before);assert(await input.isEnabled());
  await page.locator('#language-toggle').click();await input.focus();
  assert.match(await page.locator('#sync-status').innerText(),/Automatic sync will resume after you leave the input field/);
  // Manual synchronization remains available and intentionally reconciles state.
  await page.locator('#sync-now').click();await ready();
  assert.equal(await input.inputValue(),draft);
  await page.reload();await ready();assert.equal(await input.inputValue(),draft);
  assert.deepEqual(errors,[]);
  console.log('PASS: paused typing, selection/focus, IME, online/visibility/retry deferral, latest-draft backup, queued timer cancellation, manual sync and reload.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
