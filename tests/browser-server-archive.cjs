const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const {chromium} = require('../.tmp/browser/node_modules/playwright-core');

(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  try {
    const page = await browser.newPage();
    const origin = 'https://fixture.ms.show', records = new Map(), files = new Map(), errors = [];
    let writable = true, revision = 0;
    page.on('pageerror', error => errors.push(error.message));
    const root = path.resolve(__dirname, '../frontend');
    await page.route(origin+'/**', async route => {
      const request = route.request(), pathname = new URL(request.url()).pathname;
      const json = data => route.fulfill({json:data});
      if (pathname==='/config.js') return route.fulfill({contentType:'text/javascript', body:'window.BUCK_CONFIG={studioBackend:true,apiBaseUrl:location.origin};'});
      if (!pathname.startsWith('/api/')) {
        const name = path.resolve(root, '.'+(pathname==='/'?'/index.html':pathname));
        if (!name.startsWith(root+path.sep)) return route.abort();
        try {return await route.fulfill({path:name});} catch {return route.abort();}
      }
      if(pathname==='/api/health')return json({archive_enabled:true,archive_persistent:true,chat_mode:'deepseek',chat_models:['deepseek']});
      if(!pathname.startsWith('/api/archive'))return json({});
      if(!writable)return route.fulfill({status:503,json:{detail:'test offline'}});
      if(pathname==='/api/archive')return json({conversations:[...records.values()].map(r=>({id:r.record.id,updated:r.record.updated,revision:r.revision}))});
      const [, , , , id, fileRoute, sha] = pathname.split('/');
      if(fileRoute==='files'){
        assert.equal(request.method(),'POST');
        const digest=text=>require('node:crypto').createHash('sha256').update(text).digest('hex');
        assert([digest('1,2'),digest('3,4')].includes(sha));
        files.set(sha,id);
        return json({sha256:sha});
      }
      if(request.method()==='POST')records.set(id,{record:request.postDataJSON(),revision:String(++revision)});
      if(!records.has(id))return route.fulfill({status:404,json:{detail:'missing'}});
      return json({...records.get(id),files:[...files].filter(([,owner])=>owner===id).map(([sha256])=>({sha256,filename:'test.csv',valid:true,verified:true}))});
    });
    const ready = () => page.waitForFunction(()=>state.connected && activeConversation && !historyLocked() && !archiveRunning);
    await page.goto(origin); await ready();
    writable=false;
    const id = await page.evaluate(async () => {
      clearTimeout(archiveTimer);
      const id=activeConversation.id, scope=conversationPrefix+id;
      const message={id:'legacy',created:100,role:'user',content:'legacy-text'};
      localStorage.setItem(scope+':message:legacy',JSON.stringify(message));
      const db = await new Promise(resolve=>{
        const req=indexedDB.open('buck-csv-v1',1);
        req.onupgradeneeded=()=>req.result.createObjectStore('files',{keyPath:'key'}).createIndex('conversation','conversation');
        req.onsuccess=()=>resolve(req.result);
      });
      const blob=new Blob(['1,2']), sha256=await csvDigest(blob);
      await new Promise(resolve=>{
        const tx=db.transaction('files','readwrite');
        tx.objectStore('files').put({key:scope+':'+sha256,conversation:scope,sha256,filename:'test.csv',blob,epoch:0});
        tx.oncomplete=resolve;
      });
      db.close();
      $('chat-input').value='new-private-draft';persistConversation();
      await synchronizeArchive({verify:true});
      if(!localStorage.getItem(scope+':message:legacy') || !(await BuckAttachments.list(scope)).length)throw Error('Lost an unconfirmed legacy record');
      if(Object.values(localStorage).some(value=>value.includes('new-private-draft')))throw Error('New draft was persisted locally');
      return id;
    });
    writable=true;
    await page.evaluate(()=>synchronizeArchive({verify:true}));
    assert.equal(records.get(id).record.draft,'new-private-draft');
    assert.equal(records.get(id).record.messages[0].content,'legacy-text');
    assert.equal(files.size,1);
    const local=await page.evaluate(async()=>({
      keys:legacyRecordKeys(csvScope()),files:(await BuckAttachments.list(csvScope())).length,
      pending:archiveUncommitted.size
    }));
    assert.deepEqual(local,{keys:[],files:0,pending:0});
    await page.reload();await ready();
    assert.equal(await page.locator('#chat-input').inputValue(),'new-private-draft');
    assert.equal(await page.evaluate(()=>activeConversation.id),id);
    await page.evaluate(async()=>{
      const scope=csvScope(), blob=new Blob(['3,4']);
      await BuckAttachments.save(scope,await csvDigest(blob),'new.csv',blob,0);
      rememberMessage('user','new-server-only-message');
      clearTimeout(archiveTimer);
      const key=identificationPlanKey();
      saveIdentificationPlan({version:2,parser:'deepseek',status:'paused',methods:['weighted_integral','pann'],changes:{},done:[],filename:null});
      if(sessionStorage.getItem(key))throw Error('Plan written locally');
      if(Object.values(localStorage).some(value=>value.includes('new-server-only-message')))throw Error('Message written locally');
      const db=await new Promise(resolve=>{const req=indexedDB.open('buck-csv-v1');req.onsuccess=()=>resolve(req.result);});
      const count=await new Promise(resolve=>{const req=db.transaction('files').objectStore('files').count();req.onsuccess=()=>resolve(req.result);});
      db.close();
      if(count!==0)throw Error('New CSV written to IndexedDB');
      await synchronizeArchive({verify:true});
    });
    assert.equal(files.size,2);
    assert.equal(records.get(id).record.messages.at(-1).content,'new-server-only-message');
    await page.reload();await ready();
    assert.equal(await page.evaluate(()=>currentIdentificationPlan()?.status),'paused');
    // An older snapshot can share a message ID with the server and still
    // contain a different draft or message body. Two syncs must not erase it.
    await page.evaluate(async()=>{
      const old={...wireRecord(activeConversation),meta_updated:1,draft:'unconfirmed legacy draft',
        messages:[{...activeConversation.messages[0],content:'unconfirmed legacy message'}]};
      const key=csvScope();
      localStorage.setItem(key,JSON.stringify(old));
      for(let i=0;i<2;i++)await synchronizeArchive({verify:true});
      if(JSON.parse(localStorage.getItem(key))?.draft!==old.draft)throw Error('Conflicting local snapshot was deleted');
    });
    // Five records requiring re-verification must not monopolize every batch.
    const pendingIds=await page.evaluate(()=>{
      const ids=[];
      for(let i=0;i<8;i++){
        const id=crypto.randomUUID(),now=Date.now()-i-1000;
        localStorage.setItem(conversationPrefix+id,JSON.stringify({id,updated:now,draft:'valid draft '+i,messages:[]}));
        localStorage.setItem(conversationPrefix+id+':message:unreadable','{');
        ids.push(id);
      }
      return ids;
    });
    await page.evaluate(async()=>{
      await synchronizeArchive({background:true});
      clearTimeout(archiveTimer);
      await synchronizeArchive({background:true});
      clearTimeout(archiveTimer);
    });
    assert(pendingIds.every(id=>records.has(id)),'All batches reached the server');
    assert.deepEqual(errors,[]);
    console.log('Hosted archive verified: failed upload, legacy migration, local cleanup and reload.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
