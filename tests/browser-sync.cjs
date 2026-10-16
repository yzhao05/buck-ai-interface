// Local server on :8001 with ARCHIVE_DIR=runtime/archive-sync-review.
// Only rows owned by this test's random recovery code are fault-injected.
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const origin='http://127.0.0.1:8001';
const ready=p=>p.locator('#send-chat:not([disabled])').waitFor({timeout:60000});
const sync=async p=>{await ready(p);await p.evaluate(()=>synchronizeArchive());await ready(p);assert((await p.locator('#sync-status').textContent()).includes('已同步'),await p.locator('#sync-status').textContent());};
const snapshot=p=>p.evaluate(()=>({key:archiveKey,scope:csvScope(),id:activeConversation.id,record:structuredClone(activeConversation)}));
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const errors=[];
 async function open(){
  const c=await browser.newContext({viewport:{width:1450,height:1000}});
  await c.route('**/api/chat',route=>route.fulfill({json:{answer:'同步回答',mode:'local'}}));
  const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));await p.goto(origin);await ready(p);return {c,p};
 }
 async function send(p,text){await ready(p);await p.locator('#chat-input').fill(text);await p.locator('#send-chat').click();await ready(p);}
 try{
  const {c:a,p}=await open();await p.locator('#load-demo').click();await ready(p);await send(p,'第一条需要保存的消息');await sync(p);
  const initial=await snapshot(p),sha=initial.record.attachment.dataset.sha256;
  const {c:b,p:q}=await open();assert.equal(await q.locator('.message').count(),0,'A different recovery code must not see another archive');
  await q.locator('#open-settings').click();await q.locator('#sync-code').fill(initial.key);await q.locator('#settings-form button[type=submit]').click();
  await q.locator('#settings-dialog').waitFor({state:'hidden',timeout:60000});await ready(q);
  assert.equal(await q.locator('.message').count(),2,'Recovery code restores messages into an empty browser');
  assert.equal(await q.locator('#file-name').textContent(),'PLECS · Case16A.csv');
  const recovered=await snapshot(q);assert.equal(recovered.id,initial.id);
  assert(await q.evaluate(async({scope,sha})=>Boolean(await BuckAttachments.load(scope,sha)),{scope:recovered.scope,sha}));
  // Missing browser message and blob are filled from the backend on reopening.
  await q.evaluate(async({scope})=>{
    await BuckAttachments.remove(scope);
    const key=Object.keys(localStorage).find(k=>k.startsWith(scope+':message:'));localStorage.removeItem(key);
  },{scope:recovered.scope});
  await q.reload();await ready(q);await sync(q);
  assert.equal(await q.locator('.message').count(),2);
  assert(await q.evaluate(async({scope,sha})=>Boolean(await BuckAttachments.load(scope,sha)),{scope:recovered.scope,sha}));
  // Backend copy is lost; the browser must repopulate both messages and bytes.
  const owner=crypto.createHash('sha256').update(initial.key).digest('hex');
  execFileSync('.venv/Scripts/python.exe',['-c',
   'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("DELETE FROM files WHERE owner=?",(sys.argv[2],)); c.execute("DELETE FROM conversations WHERE owner=?",(sys.argv[2],)); c.commit(); c.close()',
   path.resolve('runtime/archive-sync-review/archive.sqlite3'),owner]);
  await sync(q);
  const remote=await q.request.get(origin+'/api/archive/conversations/'+initial.id,{headers:{'X-Archive-Key':initial.key}});
  const remoteBody=await remote.json();assert.equal(remoteBody.record.messages.length,2);assert.equal(remoteBody.files[0].sha256,sha);assert(remoteBody.files[0].valid);
  // Concurrent messages are unioned, not replaced by a stale whole conversation.
  await send(p,'浏览器 A 的新消息');await send(q,'浏览器 B 的新消息');await sync(p);await sync(q);await sync(p);
  assert.equal(await p.locator('.message').count(),6);assert.equal(await q.locator('.message').count(),6);
  // A damaged local blob is repaired with the verified remote bytes.
  await q.evaluate(async({scope,sha})=>BuckAttachments.save(scope,sha,'bad.csv',new Blob(['bad'])),{scope:recovered.scope,sha});
  await sync(q);assert.equal(await q.evaluate(async({scope,sha})=>csvDigest((await BuckAttachments.load(scope,sha)).blob),{scope:recovered.scope,sha}),sha);
  // Metadata already merged must reach the UI even if the CSV download fails.
  await q.evaluate(()=>clearTimeout(archiveTimer));
  const updated=(await (await q.request.get(origin+'/api/archive/conversations/'+initial.id,{headers:{'X-Archive-Key':initial.key}})).json()).record;
  updated.meta_updated=Math.max(Date.now(),updated.meta_updated+1);
  updated.updated=updated.meta_updated;updated.draft='恢复后的远端草稿';updated.fields.vin='29';
  assert((await q.request.post(origin+'/api/archive/conversations/'+initial.id,{headers:{'X-Archive-Key':initial.key},data:updated})).ok());
  await q.evaluate(scope=>BuckAttachments.remove(scope),recovered.scope);
  await b.route('**/api/archive/conversations/*/files/*',route=>route.abort());
  await q.evaluate(()=>synchronizeArchive());await ready(q);
  assert((await q.locator('#sync-status').textContent()).includes('同步未完成'));
  assert.equal(await q.locator('#chat-input').inputValue(),updated.draft,'A partial sync must not leave stale metadata in the editor');
  assert.equal(await q.locator('#vin').inputValue(),'29');
  await q.locator('#language-toggle').click();
  assert(!/[\u3400-\u9fff]/.test(await q.locator('#sync-status').textContent()),'Nested sync errors must also be translated');
  await q.locator('#language-toggle').click();
  await b.unroute('**/api/archive/conversations/*/files/*');await sync(q);
  // Clear CSV propagates; stale A must not recreate it.
  await q.locator('#clear-data').click();await ready(q);await sync(q);await sync(p);
  assert(await p.locator('#file-info').isHidden());assert.equal(await p.locator('.message').count(),6);
  assert.equal((await (await p.request.get(origin+'/api/archive/conversations/'+initial.id,{headers:{'X-Archive-Key':initial.key}})).json()).files.length,0);
  // Explicitly uploading after clear uses the new generation and is allowed.
  await q.locator('#load-demo').click();await ready(q);await sync(q);await sync(p);
  assert(await p.locator('#file-info').isVisible());
  // Offline deletion is propagated on reconnection and wins over stale copies.
  await b.route('**/api/archive**',route=>route.abort());
  q.once('dialog',d=>d.accept());await q.locator('#delete-chat').click();await ready(q);
  await b.unroute('**/api/archive**');await sync(q);await sync(p);
  assert.equal(await p.locator(`[data-conversation-id="${initial.id}"]`).count(),0);
  assert.equal(await q.locator(`[data-conversation-id="${initial.id}"]`).count(),0);
  const deleted=await (await p.request.get(origin+'/api/archive/conversations/'+initial.id,{headers:{'X-Archive-Key':initial.key}})).json();assert(deleted.deleted);
  // Successful backup must not hide a failure to remember its recovery code.
  const limited=await browser.newContext();
  await limited.addInitScript(()=>{
    const set=Storage.prototype.setItem;
    Storage.prototype.setItem=function(key,value){
      if(key.startsWith('buck-sync-key:'))throw new DOMException('Storage full','QuotaExceededError');
      return set.call(this,key,value);
    };
  });
  const r=await limited.newPage();await r.goto(origin);await ready(r);
  await r.evaluate(()=>synchronizeArchive());await ready(r);
  assert((await r.locator('#sync-status').textContent()).includes('同步恢复码无法保存'));
  await r.locator('#language-toggle').click();
  assert((await r.locator('#sync-status').textContent()).includes('The recovery code could not be saved'));
  await limited.close();
  assert.deepEqual(errors,[]);
  console.log('PASS: recovery-code isolation, fresh-browser restore, missing browser/server copies, concurrent message union, CSV hash repair, clear generation, offline deletion and stale replica protection.');
  await a.close();await b.close();
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
