const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const cryptoNode=require('node:crypto');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const origin=process.env.TEST_BASE_URL || 'http://127.0.0.1:8001';
(async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
  try{
    await page.goto(origin);await ready();
    await page.locator('#load-demo').click();await ready();
    const sync=()=>page.evaluate(async()=>{clearTimeout(archiveTimer);document.activeElement?.blur();await synchronizeArchive();clearTimeout(archiveTimer);});
    await sync();await sync();
    const saved=await page.evaluate(()=>({id:activeConversation.id,key:archiveKey,record:wireRecord(activeConversation),sha:state.dataset.sha256}));
    const requests=[];
    page.on('request',r=>{if(r.url().includes('/api/archive'))requests.push({url:r.url(),method:r.method()});});
    await sync();
    assert.equal(requests.filter(r=>r.url.includes('/conversations/')).length,0,'Unchanged sync only fetches the version index');
    // Reload receipts survive; no full metadata or CSV reconciliation is needed again.
    await page.reload();await ready();await sync();requests.length=0;await sync();
    assert.equal(requests.filter(r=>r.url.includes('/conversations/')).length,0);
    const before=await page.evaluate(()=>activeConversation.meta_updated);
    await page.locator('#chat-input').fill('只同步这项修改');await sync();
    assert.equal(requests.filter(r=>r.method==='POST'&&r.url.includes('/conversations/')).length,1);
    assert.equal(requests.filter(r=>r.url.includes('/files/')).length,0);
    assert((await page.evaluate(()=>activeConversation.meta_updated))>before);
    // Explicit audit repairs damaged server bytes while routine metadata reads do not hash blobs.
    assert(process.env.ARCHIVE_DIR,'Run through the isolated sync review runner');
    const owner=cryptoNode.createHash('sha256').update(saved.key).digest('hex');
    execFileSync('.venv/Scripts/python.exe',['-c',
      'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE files SET raw=? WHERE owner=? AND id=?",(b"bad",sys.argv[2],sys.argv[3])); c.commit(); c.close()',
      path.join(process.env.ARCHIVE_DIR,'archive.sqlite3'),owner,saved.id]);
    requests.length=0;
    await page.evaluate(async()=>{document.activeElement?.blur();await synchronizeArchive({verify:true});clearTimeout(archiveTimer);});
    assert(requests.some(r=>r.url.includes('verify=true')));
    const repaired=await page.request.get(origin+'/api/archive/conversations/'+saved.id+'/files/'+saved.sha,{headers:{'X-Archive-Key':saved.key}});
    assert(repaired.ok());
    assert.equal(cryptoNode.createHash('sha256').update(await repaired.body()).digest('hex'),saved.sha);
    // An experiment added while POST is in flight must not be falsely acknowledged.
    await page.locator('#chat-input').fill('并发保存实验');
    let injected=false;
    await page.route('**/api/archive/conversations/*',async route=>{
      if(!injected&&route.request().method()==='POST'){
        injected=true;
        const response=await route.fetch();
        await page.evaluate(()=>{
          const run={id:'concurrent-run',created:Date.now(),updated:Date.now(),dataset:{filename:'test.csv',sha256:''},
            result:{parameters:{L_H:0.00005,C_F:0.0005,R_ohm:6}}};
          localStorage.setItem(conversationPrefix+activeConversation.id+':experiment:'+run.id,JSON.stringify(run));
        });
        return route.fulfill({response});
      }
      return route.continue();
    });
    await sync();await sync();
    assert(injected);
    const concurrent=(await (await page.request.get(origin+'/api/archive/conversations/'+saved.id,{headers:{'X-Archive-Key':saved.key}})).json()).record;
    assert(concurrent.experiments.some(run=>run.id==='concurrent-run'));
    await page.unroute('**/api/archive/conversations/*');
    // Current conversation first: 12 remote histories must not hold startup UI.
    const ids=[];
    for(let i=0;i<12;i++){
      const id=crypto.randomUUID();ids.push(id);
      const record={...saved.record,id,attachment:null,messages:[],experiments:[],updated:Date.now()+i,meta_updated:Date.now()+i};
      assert((await page.request.post(origin+'/api/archive/conversations/'+id,{headers:{'X-Archive-Key':saved.key},data:record})).ok());
    }
    await page.evaluate(async()=>{clearTimeout(archiveTimer);await synchronizeArchive({preferRemote:true,currentFirst:true});});
    assert(await page.locator('#chat-input').isEnabled());
    assert.equal(await page.evaluate(()=>activeConversation.id),saved.id);
    await page.waitForFunction(ids=>ids.every(id=>conversationCache.has(id)),ids,{timeout:30000});
    await ready();
    assert.equal(await page.evaluate(()=>activeConversation.id),saved.id);
    assert.equal(await page.locator('#chat-input').inputValue(),'并发保存实验');
    await sync();requests.length=0;await sync();
    assert.equal(requests.filter(r=>r.url.includes('/conversations/')).length,0,'Experiment defaults and key order must not cause endless syncing');
    // Version change from a different client is fetched and its immutable messages survive.
    const remote=(await (await page.request.get(origin+'/api/archive/conversations/'+saved.id,{headers:{'X-Archive-Key':saved.key}})).json()).record;
    remote.messages.push({id:crypto.randomUUID(),created:Date.now(),role:'user',content:'远端消息'});
    assert((await page.request.post(origin+'/api/archive/conversations/'+saved.id,{headers:{'X-Archive-Key':saved.key},data:remote})).ok());
    requests.length=0;await sync();
    assert(requests.some(r=>r.method==='GET'&&r.url.includes(saved.id)));
    assert(await page.evaluate(()=>readConversation(activeConversation.id).messages.some(m=>m.content==='远端消息')));
    await page.evaluate(()=>archiveProgress(2,12,'上传 CSV','data.csv'));
    await page.locator('#language-toggle').click();
    assert.match(await page.locator('#sync-status').textContent(),/Sync 2\/12: Uploading CSV/);
    assert.deepEqual(errors,[]);
    console.log('PASS: no-op sync uses one index request; edits transfer one record and no CSV; receipts survive reload; current-first and background batches preserve editor; remote changes and bilingual progress work.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
