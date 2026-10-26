// Chat persistence must survive partial writes, stale sync responses and lost indexes.
const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const context=await browser.newContext(),errors=[];
 await context.route('**/api/health',async route=>{
  const response=await route.fetch(),body=await response.json();body.archive_enabled=false;await route.fulfill({json:body});
 });
 await context.route('**/api/chat',route=>route.fulfill({json:{answer:'保存测试回答：'+route.request().postDataJSON().message,mode:'local'}}));
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked());
 const send=async text=>{await page.locator('#chat-input').fill(text);await page.locator('#send-chat').click();await ready();};
 try{
  await page.goto('http://127.0.0.1:8001');await ready();await send('第一轮旧对话');
  const id=await page.evaluate(()=>activeConversation.id);
  await page.evaluate(()=>{
   window.realStorageSet=Storage.prototype.setItem;window.failExperimentWrites=true;
   Storage.prototype.setItem=function(key,value){
    if(window.failExperimentWrites&&key.includes(':experiment:'))throw new DOMException('Test quota','QuotaExceededError');
    return window.realStorageSet.call(this,key,value);
   };
   activeConversation.experiments=[{id:'test-run',created:1,updated:1,dataset:{filename:'test.csv',sha256:'test'},result:{parameters:{L_H:60e-6,C_F:500e-6,R_ohm:6}}}];
  });
  await send('刚才新增的消息必须保存');
  assert.equal(await page.evaluate(()=>storedMessages(activeConversation.id).length),4,'Failed experiment writes must not skip chat writes');
  await page.locator('#new-chat').click();
  assert.equal(await page.evaluate(()=>activeConversation.id),id,'Do not leave an incompletely saved conversation');
  assert.equal(await page.locator('.message').count(),4);
  assert(await page.locator('#history-status').isVisible());
  await page.evaluate(()=>{window.failExperimentWrites=false;});
  await page.locator('#new-chat').click();await ready();
  assert.notEqual(await page.evaluate(()=>activeConversation.id),id);
  await page.locator(`[data-conversation-id="${id}"]`).click();await ready();
  assert.equal(await page.locator('.message').count(),4);
  assert.match(await page.locator('#chat-messages').innerText(),/刚才新增的消息必须保存/);

  // A failed individual message write must also block navigation, without clearing the UI.
  await page.evaluate(()=>{
   window.failMessageWrites=true;
   Storage.prototype.setItem=function(key,value){
    if(window.failMessageWrites&&key.includes(':message:')&&value.includes('尚未写入'))throw new DOMException('Test quota','QuotaExceededError');
    return window.realStorageSet.call(this,key,value);
   };
  });
  await send('尚未写入的最新消息');await page.locator('#new-chat').click();
  assert.equal(await page.evaluate(()=>activeConversation.id),id);assert.equal(await page.locator('.message').count(),6);
  await page.evaluate(()=>{window.failMessageWrites=false;});
  await page.locator('#new-chat').click();await ready();
  await page.reload();await ready();
  await page.locator(`[data-conversation-id="${id}"]`).click();await ready();assert.equal(await page.locator('.message').count(),6);

  // An older sync response cannot remove a newer locally saved exchange.
  await page.evaluate(()=>{window.oldReplica=structuredClone(wireRecord(activeConversation));});
  await send('同步开始后新增的消息');
  await page.evaluate(()=>saveMergedRecord(window.oldReplica));
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).messages.length),8);
  await page.locator('#new-chat').click();await ready();
  await page.locator(`[data-conversation-id="${id}"]`).click();await ready();assert.equal(await page.locator('.message').count(),8);

  // Legacy embedded messages must be unioned even when a newer metadata-only cache wins.
  const legacy=await page.evaluate(()=>{
   const id=crypto.randomUUID(),item={id:crypto.randomUUID(),created:100,role:'user',content:'旧版本内嵌的历史正文'};
   localStorage.setItem(conversationPrefix+id,JSON.stringify({id,updated:100,meta_updated:100,messages:[item]}));
   conversationCache.set(id,{id,updated:200,meta_updated:200,messages:[],draft:'新草稿'});
   return {id,count:readConversation(id).messages.length};
  });
  assert.equal(legacy.count,1,'Metadata version must not discard legacy embedded messages');

  // Missing metadata index: intact per-message records must remain discoverable after reload.
  await page.locator('#new-chat').click();await ready();
  await page.evaluate(id=>{localStorage.removeItem(conversationPrefix+id);conversationCache.delete(id);},id);
  await page.reload();await ready();
  await page.locator(`[data-conversation-id="${id}"]`).click();await ready();
  assert.equal(await page.locator('.message').count(),8,'Recover chat from message keys when metadata is missing');
  assert.match(await page.locator(`[data-conversation-id="${id}"]`).innerText(),/8/);
  await page.locator('#language-toggle').click();
  assert.match(await page.locator(`[data-conversation-id="${id}"]`).innerText(),/Messages: 8/);
  assert.deepEqual(errors,[]);
  console.log('PASS: independent chat writes, guarded navigation on partial save, retry/new/restore, stale replica union, legacy messages, orphan index recovery and bilingual message counts');
 }finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
