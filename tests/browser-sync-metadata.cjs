const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/health',async route=>{const r=await route.fetch(),body=await r.json();body.archive_enabled=false;await route.fulfill({json:body});});
 try{
  await page.goto('http://127.0.0.1:8001');await page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked());
  await page.locator('#enter-identification').click();
  await page.evaluate(()=>{
   persistConversation();
   const remote=structuredClone(wireRecord(activeConversation));
   remote.fields.vin='33';remote.fields.frequency='90';remote.draft='远端草稿';
   remote.meta_updated=(activeConversation.meta_updated || 0)+1;remote.file_epoch=9;
   saveMergedRecord(remote);
  });
  assert.equal(await page.locator('#vin').inputValue(),'28','Background merge leaves the visible editor alone');
  await page.locator('#chat-input').fill('本地只修改聊天草稿');
  const saved=()=>page.evaluate(()=>JSON.parse(localStorage.getItem(conversationPrefix+activeConversation.id)));
  assert.equal((await saved()).fields.vin,'33','Editing a draft must not overwrite a newer remote operating condition');
  assert.equal((await saved()).fields.frequency,'90');assert.equal((await saved()).file_epoch,9);
  assert.equal((await saved()).draft,'本地只修改聊天草稿');
  await page.evaluate(()=>persistConversation());
  assert.equal((await saved()).fields.vin,'33','A second passive save must preserve the same merged metadata');
  await page.locator('#vin').fill('30');assert.equal((await saved()).fields.vin,'30');
  assert.equal((await saved()).fields.frequency,'90','Editing one field must not revert a different remote field');
  // A failed metadata write is retried from the in-memory merged record.
  await page.evaluate(()=>{
   window.originalSet=Storage.prototype.setItem;
   Storage.prototype.setItem=function(k,v){if(k===conversationPrefix+activeConversation.id)throw new DOMException('Test quota','QuotaExceededError');return window.originalSet.call(this,k,v);};
  });
  await page.locator('#chat-input').fill('保存失败后重试的草稿');
  await page.evaluate(()=>{Storage.prototype.setItem=window.originalSet;persistConversation();});
  assert.equal((await saved()).draft,'保存失败后重试的草稿');assert.equal((await saved()).fields.frequency,'90');
  // A deliberate new import must use the already-received remote clear epoch.
  await page.locator('#load-demo').click();await page.waitForFunction(()=>state.mapping&&!state.busy);
  assert.equal((await saved()).attachment?.csvEpoch,9,'A new upload after a remote clear must remain attached');
  assert(await page.evaluate(async()=>Boolean(await BuckAttachments.load(csvScope(),state.dataset.sha256,9))),'The new CSV must use the current file epoch');
  await page.locator('#vin').fill('30');await page.locator('#frequency').fill('90');
  const id=await page.evaluate(()=>activeConversation.id);
  await page.locator('#new-chat').click();await page.waitForFunction(id=>activeConversation.id!==id,id);
  await page.locator(`[data-conversation-id="${id}"]`).click();
  assert.equal(await page.locator('#frequency').inputValue(),'90');assert.equal(await page.locator('#vin').inputValue(),'30');
  assert.equal(await page.locator('#chat-input').inputValue(),'保存失败后重试的草稿');
  assert.deepEqual(errors,[]);
  console.log('PASS: stale visible form preserves remote metadata, per-field edits, monotonic CSV epoch, import after remote clear, passive save, quota retry and reopen');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
