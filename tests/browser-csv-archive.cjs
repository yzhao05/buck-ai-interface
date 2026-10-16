// Run against the local backend on :8001. CSV routes are real; chat is mocked.
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const assert=require('node:assert/strict');
const origin='http://127.0.0.1:8001';
const chrome={executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true};
const ready=p=>p.locator('#send-chat:not([disabled])').waitFor({timeout:60000});
async function archive(p){return p.evaluate(()=>{
  const id=document.querySelector('.history-item[aria-current=true]').dataset.conversationId;
  const scope=Object.keys(localStorage).find(k=>k.startsWith('buck-history:')&&k.endsWith(':'+id));
  return {scope,record:JSON.parse(localStorage.getItem(scope))};
});}
async function expire(p,record){
  const response=await p.request.delete(origin+'/api/datasets/'+record.attachment.dataset.dataset_id,{headers:{'X-Session-ID':record.id}});
  assert.equal(response.status(),200);
}
async function send(p,text){await p.locator('#chat-input').fill(text);await p.locator('#send-chat').click();await ready(p);}
(async()=>{
  fs.mkdirSync('runtime',{recursive:true});
  const profile=fs.mkdtempSync(path.resolve('runtime/csv-profile-'));
  const errors=[],chats=[];
  let context;
  async function launch(){
    context=await chromium.launchPersistentContext(profile,{...chrome,viewport:{width:1450,height:1000}});
    context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
    await context.route('**/api/chat',route=>{chats.push(route.request().postDataJSON());return route.fulfill({json:{answer:'Archive test response',mode:'local'}});});
    const p=await context.newPage();await p.goto(origin);await ready(p);return p;
  }
  try {
    let p=await launch();
    const raw=Buffer.concat([fs.readFileSync('examples/plecs_buck_case16a.csv'),Buffer.from('\n')]);
    await p.locator('#csv-file').setInputFiles({name:'my-waveform.csv',mimeType:'text/csv',buffer:raw});await ready(p);
    await send(p,'第一会话');
    const first=await archive(p),sha=crypto.createHash('sha256').update(raw).digest('hex');
    assert.equal(first.record.attachment.dataset.sha256,sha);
    assert.equal(await p.evaluate(async({scope,sha})=>{
      const file=await BuckAttachments.load(scope,sha);
      const hash=await crypto.subtle.digest('SHA-256',await file.blob.arrayBuffer());
      return Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');
    },{scope:first.scope,sha}),sha,'Archive preserves original bytes');
    await p.locator('#new-chat').click();await ready(p);await p.locator('#load-demo').click();await ready(p);
    await send(p,'第二会话');const second=await archive(p);
    assert.notEqual(second.record.attachment.dataset.sha256,sha);
    await p.locator(`[data-conversation-id="${first.record.id}"]`).click();await ready(p);
    await p.locator('#mapping-details').evaluate(el=>el.open=true);
    await p.locator('#time-unit').selectOption('ms');
    await expire(p,first.record);
    await context.close(); // Real persistent browser profile, including IndexedDB blobs.
    p=await launch();
    assert.equal(await p.locator('#file-name').textContent(),'my-waveform.csv');
    assert.equal(await p.locator('#time-unit').inputValue(),'ms','Unconfirmed mapping survives archive restoration');
    await p.locator('#enter-identification').click();assert(await p.locator('#identify').isDisabled());
    assert.equal(await p.locator('.message').count(),2);
    const restored=await archive(p);assert.notEqual(restored.record.attachment.dataset.dataset_id,first.record.attachment.dataset.dataset_id);
    await p.locator('#time-unit').selectOption('s');await p.locator('#apply-mapping').click();await ready(p);
    await p.locator('#fit-method').selectOption('weighted_integral');await p.locator('#start-ms').fill('0');await p.locator('#end-ms').fill('0.8');
    await expire(p,(await archive(p)).record);
    await p.locator('#identify').click();await ready(p);
    assert.equal(await p.locator('#result-l').textContent(),'60','Identification recovers an expired CSV without reload');
    await expire(p,(await archive(p)).record);
    await send(p,'分析波形');assert(chats.at(-1).dataset_id);assert.equal(chats.at(-1).result_id,null,'Expired results are not relabelled after CSV restoration');
    assert.equal(await p.locator('#result-l').textContent(),'—');
    // Corruption must be repaired from a verified server replica, never analyzed.
    await p.evaluate(()=>synchronizeArchive());await ready(p);
    const current=await archive(p);
    await p.evaluate(async({scope,sha})=>BuckAttachments.save(scope,sha,'my-waveform.csv',new Blob(['corrupt'])),{scope:first.scope,sha});
    await expire(p,current.record);await p.reload();await ready(p);
    assert(await p.locator('#file-info').isVisible());
    assert.equal(await p.evaluate(async({scope,sha})=>csvDigest((await BuckAttachments.load(scope,sha)).blob),{scope:first.scope,sha}),sha);
    // Repair explicitly, then verify deletion and cross-tab stale-write protection.
    await p.locator('#csv-file').setInputFiles({name:'my-waveform.csv',mimeType:'text/csv',buffer:raw});await ready(p);
    await p.evaluate(()=>{
      window.originalRemove=BuckAttachments.remove;
      BuckAttachments.remove=async()=>{throw new Error('Storage blocked');};
    });
    p.once('dialog',d=>d.accept());await p.locator('#delete-chat').click();await ready(p);
    assert.equal((await archive(p)).record.id,first.record.id,'Failed file deletion must preserve the conversation');
    assert.equal(await p.evaluate(()=>Object.keys(localStorage).some(k=>k.includes(':deleted:'))),false);
    await p.evaluate(()=>{BuckAttachments.remove=window.originalRemove;});
    const stale=await context.newPage();await stale.goto(origin);await ready(stale);
    p.once('dialog',d=>d.accept());await p.locator('#delete-chat').click();await ready(p);
    await stale.waitForFunction(id=>document.querySelector('.history-item[aria-current=true]')?.dataset.conversationId!==id,first.record.id);
    assert.equal(await p.evaluate(async({scope,sha})=>await BuckAttachments.load(scope,sha),{scope:first.scope,sha}),null);
    assert.equal(await stale.locator(`[data-conversation-id="${first.record.id}"]`).count(),0);
    await p.locator(`[data-conversation-id="${second.record.id}"]`).click();await ready(p);
    assert((await p.locator('#file-name').textContent()).includes('Case16A'));
    assert(await p.evaluate(async({scope,sha})=>Boolean(await BuckAttachments.load(scope,sha)),{scope:second.scope,sha:second.record.attachment.dataset.sha256}),'Other conversation CSV must survive deletion');
    await p.locator('#clear-data').click();await ready(p);
    assert.equal(await p.locator('.message').count(),2,'Clearing CSV preserves messages');
    assert.equal(await p.evaluate(async({scope,sha})=>await BuckAttachments.load(scope,sha),{scope:second.scope,sha:second.record.attachment.dataset.sha256}),null);
    await p.evaluate(()=>{BuckAttachments.save=async()=>{throw new DOMException('Full','QuotaExceededError');};});
    await p.locator('#load-demo').click();await ready(p);
    assert(await p.locator('#csv-storage-status').isVisible(),'Storage failure must remain visible after metadata persistence');
    assert(await p.locator('#file-info').isVisible(),'Storage failure does not prevent current analysis');
    await p.locator('#language-toggle').click();assert((await p.locator('#csv-storage-status').textContent()).includes('could not be saved'));
    assert.equal(await p.locator('#delete-chat').textContent(),'Delete current conversation');
    assert.deepEqual(errors,[]);
    console.log('PASS: original CSV bytes, browser restart, isolated conversations, expiry recovery before fit/chat, mapping drafts, verified corruption repair, conversation/CSV deletion, stale tabs, storage failure and bilingual warnings.');
  } finally {if(context)await context.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
