const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const origin=process.env.TEST_BASE_URL || 'http://127.0.0.1:8001';
(async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage(),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().includes('/api/archive'))requests.push(r.url());});
  const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
  const sync=()=>page.evaluate(async()=>{clearTimeout(archiveTimer);document.activeElement?.blur();await synchronizeArchive();clearTimeout(archiveTimer);});
  try{
    await page.goto(origin);await ready();await sync();
    const old=await page.evaluate(()=>({key:archiveKey,id:activeConversation.id,record:wireRecord(activeConversation)}));
    const replacement=crypto.randomUUID();
    assert((await page.request.post(origin+'/api/archive/conversations/'+replacement,
      {headers:{'X-Archive-Key':old.key},data:{...old.record,id:replacement,updated:Date.now(),meta_updated:Date.now(),draft:'恢复可用会话'}})).ok());
    assert((await page.request.delete(origin+'/api/archive/conversations/'+old.id,{headers:{'X-Archive-Key':old.key}})).ok());
    await page.evaluate(async()=>{clearTimeout(archiveTimer);await synchronizeArchive({currentFirst:true});clearTimeout(archiveTimer);});
    assert.equal(await page.evaluate(()=>activeConversation.id),replacement,'A deleted active record must not consume the foreground recovery slot');
    assert.equal(await page.locator('#chat-input').inputValue(),'恢复可用会话');
    // Deletion may also arrive after the directory snapshot, before the merge.
    const next=crypto.randomUUID();
    assert((await page.request.post(origin+'/api/archive/conversations/'+next,
      {headers:{'X-Archive-Key':old.key},data:{...old.record,id:next,updated:Date.now(),meta_updated:Date.now(),draft:'目录读取后的恢复'}})).ok());
    await page.locator('#chat-input').fill('等待合并');
    let deletedDuringMerge=false;
    await page.route('**/api/archive/conversations/'+replacement+'?*',async route=>{
      if(!deletedDuringMerge){
        deletedDuringMerge=true;
        await page.request.delete(origin+'/api/archive/conversations/'+replacement,{headers:{'X-Archive-Key':old.key}});
      }
      await route.continue();
    });
    await page.evaluate(async()=>{clearTimeout(archiveTimer);document.activeElement?.blur();await synchronizeArchive({currentFirst:true});clearTimeout(archiveTimer);});
    await page.unroute('**/api/archive/conversations/'+replacement+'?*');
    assert(deletedDuringMerge);
    assert.equal(await page.evaluate(()=>activeConversation.id),next);
    // A deleted record is absent from cache, but a late attachment write can still exist.
    await page.evaluate(async id=>{
      const scope=conversationPrefix+id;
      conversationCache.delete(id);
      await BuckAttachments.save(scope,'a'.repeat(64),'late.csv',new Blob(['late']),0);
      localStorage.setItem(conversationPrefix+'sync:'+id,JSON.stringify({remote:'stale'}));
    },old.id);
    await sync();
    assert.equal(await page.evaluate(async id=>(await BuckAttachments.list(conversationPrefix+id)).length,old.id),0);
    assert.equal(await page.evaluate(id=>localStorage.getItem(conversationPrefix+'sync:'+id),old.id),null);
    // A requested audit queued during an ordinary sync must survive its completion.
    let queued=false;
    await page.route('**/api/archive',async route=>{
      if(!queued){queued=true;await page.evaluate(()=>synchronizeArchive({background:true,verify:true}));}
      await route.continue();
    });
    await sync();await page.unroute('**/api/archive');
    assert(await page.evaluate(()=>archiveVerifyPending));
    requests.length=0;await sync();
    assert(requests.some(url=>url.includes('verify=true')));
    assert.equal(await page.evaluate(()=>archiveVerifyPending),false);
    // An expired persisted receipt triggers an audit even without explicit verification.
    await page.evaluate(()=>{
      const key=conversationPrefix+'sync:'+activeConversation.id;
      const receipt=JSON.parse(localStorage.getItem(key));receipt.checkedAt=Date.now()-2*86400000;
      localStorage.setItem(key,JSON.stringify(receipt));archiveReceipts.clear();
    });
    requests.length=0;await sync();
    assert(requests.some(url=>url.includes('verify=true')));
    // Work queued before a network failure must not replace backoff with a 1s loop.
    const delay=await page.evaluate(async()=>{
      clearTimeout(archiveTimer);archiveAgain=true;
      const request=archiveRequest,timer=window.setTimeout,delays=[];
      archiveRequest=async()=>{throw Error('offline test');};
      window.setTimeout=(fn,ms,...args)=>{delays.push(ms);return timer(fn,ms,...args);};
      try{await synchronizeArchive();return delays.at(-1);}
      finally{clearTimeout(archiveTimer);archiveRequest=request;window.setTimeout=timer;}
    });
    assert.equal(delay,30000);
    assert.deepEqual(errors,[]);
    console.log('PASS: deleted-current recovery, orphan CSV cleanup, queued audits, daily audit and failure backoff');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
