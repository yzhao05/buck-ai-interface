// Review regressions; run against the local backend on :8001.
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  try {
    const context=await browser.newContext();
    await context.route('**/api/chat',route=>route.fulfill({json:{answer:'回答 '+route.request().postDataJSON().message,mode:'deepseek'}}));
    let a=await context.newPage();await a.goto('http://127.0.0.1:8001');
    const ready=p=>p.locator('#send-chat:not([disabled])').waitFor();
    const send=async(p,text)=>{await ready(p);await p.locator('#chat-input').fill(text);await p.locator('#send-chat').click();await ready(p);};
    await send(a,'初始问题');
    const b=await context.newPage();await b.goto('http://127.0.0.1:8001');await ready(b);
    await send(a,'另一标签页的新消息');
    await b.locator('#chat-input').fill('旧标签页草稿');
    await b.close();a=await context.newPage();await a.goto('http://127.0.0.1:8001');await ready(a);
    assert.equal(await a.locator('.message').count(),4,'A stale tab must not overwrite newer messages');
    await Promise.all([send(a,'并行消息 A'),send(context.pages()[0],'并行消息 B')]);
    a=await context.newPage();await a.goto('http://127.0.0.1:8001');await ready(a);
    assert.equal(await a.locator('.message').count(),8,'Concurrent tabs must retain both exchanges');
    await a.locator('#load-demo').click();await ready(a);
    await a.locator('#mapping-details summary').click();
    await a.locator('#time-unit').selectOption('ms');
    await a.reload();await ready(a);
    assert.equal(await a.locator('#time-unit').inputValue(),'ms','Unapplied mapping edits must survive reload');
    await a.locator('#enter-identification').click();
    await a.locator('#fit-method').selectOption('weighted_integral');
    assert(await a.locator('#identify').isDisabled(),'Restoration must not auto-apply unconfirmed mapping');
    assert(await a.locator('#file-info').isVisible(),'Unconfirmed mapping must remain editable');
    await a.locator('#time-unit').selectOption('s');await a.locator('#apply-mapping').click();await ready(a);
    await a.locator('#identify').click();await ready(a);
    assert.equal(await a.locator('#result-l').textContent(),'60');
    // A restored pending job must also be checked when it eventually completes.
    const pendingContext=await browser.newContext({storageState:await context.storageState()});
    try {
      let polls=0;
      await pendingContext.route('**/api/jobs/*',async route=>{
        const response=await route.fetch(),body=await response.json();
        if (++polls === 1) { body.status='running'; body.result=null; }
        else body.result.options.method='pann';
        await route.fulfill({json:body});
      });
      const pending=await pendingContext.newPage();await pending.goto('http://127.0.0.1:8001');
      await pending.waitForFunction(()=>document.querySelector('#job-status').textContent.includes('任务未完成'));
      await ready(pending);
      assert(polls>=2,'Restore must resume polling the pending job');
      assert.equal(await pending.locator('#result-l').textContent(),'—','Reject mismatched results when a restored job finishes');
      assert(await pending.locator('#download-report').isDisabled(),'Mismatched results must not be downloadable');
    } finally { await pendingContext.close(); }
    await a.route('**/api/jobs/*',async route=>{
      const response=await route.fetch(),body=await response.json();body.result.options.vin=999;
      await route.fulfill({json:body});
    });
    await a.reload();await ready(a);
    assert.equal(await a.locator('#result-l').textContent(),'—','Do not restore results with incompatible settings');
    console.log('PASS: stale/concurrent tabs preserve messages, unconfirmed mapping survives, incompatible completed and pending results are rejected.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
