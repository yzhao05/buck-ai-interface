const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1050}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/chat',route=>route.fulfill({json:{answer:'原会话回答\n\n'+'保留滚动位置。\n\n'.repeat(40),mode:'local'}}));
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
 const send=async text=>{await page.locator('#chat-input').fill(text);await page.locator('#send-chat').click();await ready();};
 try{
  await page.goto('http://127.0.0.1:8001');await ready();await send('需要保留的旧会话');
  await page.evaluate(()=>synchronizeArchive());await ready();
  const previous=await page.evaluate(()=>({id:activeConversation.id,record:wireRecord(activeConversation),key:archiveKey}));
  // Another browser has a newer message/settings copy. Automatic sync must store
  // it without replacing the live editor, DOM, scroll or selected conversation.
  const remote=structuredClone(previous.record);
  remote.messages.push({id:crypto.randomUUID(),created:Date.now(),role:'assistant',content:'远端新增历史消息',mode:'local',response:null});
  remote.meta_updated=Date.now()+1;remote.fields.vin='33';remote.draft='远端草稿';
  const saved=await page.request.post('http://127.0.0.1:8001/api/archive/conversations/'+previous.id,{headers:{'X-Archive-Key':previous.key},data:remote});assert(saved.ok());
  let release,arrived;let rootRequests=0;
  let gate=new Promise(resolve=>release=resolve),reached=new Promise(resolve=>arrived=resolve);
  await page.route('**/api/archive',async route=>{rootRequests++;arrived();await gate;await route.continue();});
  const start=async()=>{
   await page.evaluate(()=>{document.activeElement?.blur();archiveLastSuccess=0;document.dispatchEvent(new Event('visibilitychange'));});
   await reached;await page.waitForFunction(()=>archiveRunning);
  };
  await start();
  assert.equal(await page.evaluate(()=>state.syncBusy),false);
  for(const id of ['chat-input','send-chat','load-demo','new-chat'])assert(await page.locator('#'+id).isEnabled(),id+' stays usable during background sync');
  await page.locator('#chat-input').fill('切回网页后继续输入，不应被刷新');
  // Let focus/caret scrolling from the user action finish before measuring sync effects.
  await page.waitForTimeout(200);
  await page.evaluate(()=>{
   $('chat-input').setSelectionRange(2,6);$('chat-messages').style.scrollBehavior='auto';$('chat-messages').scrollTop=70;
   window.savedArticle=$('chat-messages').querySelector('.message');
  });
  await page.waitForTimeout(200);
  const scroll=await page.locator('#chat-messages').evaluate(e=>e.scrollTop);
  release();await ready();
  assert.equal(await page.locator('#chat-input').inputValue(),'切回网页后继续输入，不应被刷新');
  assert.deepEqual(await page.locator('#chat-input').evaluate(e=>[e.selectionStart,e.selectionEnd]),[2,6]);
  assert(await page.locator('#chat-input').evaluate(e=>e===document.activeElement));
  assert.equal(await page.locator('#vin').inputValue(),'28');
  assert.equal(await page.locator('#chat-messages').evaluate(e=>e.scrollTop),scroll);
  assert(await page.evaluate(()=>window.savedArticle===$('chat-messages').querySelector('.message')),'Do not recreate message DOM');
  assert(await page.evaluate(()=>readConversation(activeConversation.id).messages.some(m=>m.content==='远端新增历史消息')));

  // Returning repeatedly within a minute does not request a full archive again.
  await page.evaluate(()=>{clearTimeout(archiveTimer);archiveTimer=null;archiveDeferred=false;document.activeElement?.blur();});
  const before=rootRequests;
  await page.evaluate(()=>{for(let i=0;i<5;i++)document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForTimeout(1300);assert.equal(rootRequests,before);

  // User navigation during a slow copy must win over its captured previousId.
  gate=new Promise(resolve=>release=resolve);reached=new Promise(resolve=>arrived=resolve);
  await start();await page.locator('#new-chat').click();
  await page.waitForFunction(id=>activeConversation.id!==id,previous.id);
  const newId=await page.evaluate(()=>activeConversation.id);
  await page.locator('#chat-input').fill('新会话的草稿');release();await ready();
  assert.equal(await page.evaluate(()=>activeConversation.id),newId);
  assert.equal(await page.locator('#chat-input').inputValue(),'新会话的草稿');
  assert.equal(await page.locator('.message').count(),0);
  await page.locator(`[data-conversation-id="${previous.id}"]`).click();await ready();
  assert.match(await page.locator('#chat-messages').innerText(),/需要保留的旧会话/);
  assert.match(await page.locator('#chat-messages').innerText(),/远端新增历史消息/);
  assert.equal(await page.locator('#chat-input').inputValue(),'切回网页后继续输入，不应被刷新');
  // A late file copy / cleanup from before Clear CSV must not replace newer data.
  assert.deepEqual(await page.evaluate(async()=>{
   const scope=csvScope()+':test-race';
   await BuckAttachments.save(scope,'same-sha','new.csv',new Blob(['new']),2);
   await BuckAttachments.save(scope,'same-sha','old.csv',new Blob(['old']),1);
   await BuckAttachments.remove(scope,1);
   const file=await BuckAttachments.load(scope,'same-sha',2);
   const result={filename:file?.filename,text:await file?.blob.text()};
   await BuckAttachments.remove(scope);return result;
  }),{filename:'new.csv',text:'new'});
  assert.deepEqual(errors,[]);
  console.log('PASS: slow automatic sync leaves controls/focus/selection/scroll/DOM intact, merges remote messages, throttles visibility events and preserves navigation/new drafts');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
