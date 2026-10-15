// Run with the local backend on :8001. Chat responses are mocked; CSV routes use the real backend.
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const errors=[], requests=[];
  let hold=false, release, reached;
  async function setup(context) {
    context.on('page',page=>page.on('pageerror',e=>errors.push(e.message)));
    await context.route('**/api/chat',async route=>{
      requests.push({body:route.request().postDataJSON(),owner:route.request().headers()['x-session-id']});
      if(hold){reached();await new Promise(resolve=>release=resolve);}
      await route.fulfill({json:{answer:'测试回答：'+requests.at(-1).body.message,mode:'deepseek',route:{label:'公式知识',mode:'agent'},sources:[{title:'安全来源',url:'https://example.com'},{title:'不安全地址',url:'javascript:alert(1)'}],tool_calls:[{name:'search_knowledge',status:'已执行'}]}});
    });
  }
  async function ready(page){await page.locator('#send-chat:not([disabled])').waitFor();}
  async function send(page,text){await page.locator('#chat-input').fill(text);await page.locator('#send-chat').click();await ready(page);}
  try {
    let context=await browser.newContext({viewport:{width:1450,height:1000}});await setup(context);
    let page=await context.newPage();await page.goto('http://127.0.0.1:8001');await ready(page);
    await send(page,'<img src=x onerror=alert(1)> Buck 公式');
    const first=await page.locator('.history-item[aria-current=true]').getAttribute('data-conversation-id');
    await page.locator('#load-demo').click();await ready(page);
    assert.equal(await page.locator('.message').count(),2,'Import keeps conversation');
    await page.locator('#enter-identification').click();await page.locator('#fit-method').selectOption('weighted_integral');await page.locator('#identify').click();await ready(page);
    assert.equal(await page.locator('#result-l').textContent(),'60');
    for(let i=0;i<6;i++)await send(page,'问题 '+i);
    assert.equal(await page.locator('.message').count(),14);
    const dataA=requests.at(-1).body.dataset_id;
    assert(dataA);assert.equal(requests.at(-1).owner,first);
    await page.locator('#chat-input').fill('第一会话草稿');
    await page.locator('#new-chat').click();await ready(page);
    assert.equal(await page.locator('.message').count(),0);
    assert(await page.locator('#file-info').isHidden());
    await send(page,'第二会话');
    const second=requests.at(-1).owner;
    assert.notEqual(second,first);assert.equal(requests.at(-1).body.dataset_id,null);
    assert.deepEqual(requests.at(-1).body.history,[]);
    await page.locator('#load-demo').click();await ready(page);
    await send(page,'第二会话波形');const dataB=requests.at(-1).body.dataset_id;assert.notEqual(dataA,dataB);
    await page.locator(`[data-conversation-id="${first}"]`).click();await ready(page);
    assert.equal(await page.locator('.message').count(),14);
    assert.equal(await page.locator('#chat-input').inputValue(),'第一会话草稿');
    assert.equal(await page.locator('#result-l').textContent(),'60','Available identification result restores with its conversation');
    assert.equal(await page.locator('.message-content img').count(),0);
    assert.equal(await page.locator('.message-sources a[href^="javascript:"]').count(),0);
    await send(page,'继续第一会话');
    assert.equal(requests.at(-1).body.dataset_id,dataA);
    assert.equal(requests.at(-1).body.history.length,10);
    assert(!requests.at(-1).body.history.some(m=>m.content.includes('第二会话')));
    await page.locator('#toggle-experiment').click();await page.locator('#toggle-history').click();
    await page.reload();await ready(page);
    assert(await page.locator('#experiment-content').isHidden());assert(await page.locator('#history-content').isHidden());
    assert.equal(await page.locator('.message').count(),16);
    await page.locator('#toggle-experiment').click();await page.locator('#toggle-history').click();
    assert.equal((await page.locator('.message-task').count()),8);
    await page.locator('#language-toggle').click();
    assert.equal(await page.locator('#toggle-history').getAttribute('aria-label'),'Collapse chat history');
    for(const width of [1450,900,390,320]){
      await page.setViewportSize({width,height:1000});
      assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)),`No overflow at ${width}`);
    }
    await page.setViewportSize({width:1450,height:1000});
    const saved=await context.storageState();await context.close();
    context=await browser.newContext({storageState:saved});await setup(context);
    page=await context.newPage();await page.goto('http://127.0.0.1:8001');await ready(page);
    assert.equal(await page.locator('.message').count(),16,'Survives fresh browser context');
    await page.request.delete('http://127.0.0.1:8001/api/datasets/'+dataA,{headers:{'X-Session-ID':first}});
    await page.reload();await ready(page);
    assert.equal(await page.locator('.message').count(),16,'Unavailable CSV does not erase messages');
    assert(await page.locator('#file-info').isHidden());
    assert((await page.locator('#notice').textContent()).includes('CSV'));
    await send(page,'继续解释公式');
    assert.equal(requests.at(-1).body.dataset_id,null);
    assert.equal(requests.at(-1).body.result_id,null);
    assert.equal(requests.at(-1).body.history.length,10);
    const arrived=new Promise(resolve=>reached=resolve);hold=true;
    await page.locator('#chat-input').fill('慢请求');await page.locator('#send-chat').click();await arrived;
    assert(await page.locator('#new-chat').isDisabled());
    assert(await page.locator('.history-item').first().isDisabled());
    release();hold=false;await ready(page);
    await page.screenshot({path:'runtime/history-desktop.png',fullPage:true});
    await page.locator('#clear-chat').click();await ready(page);
    assert.equal(await page.locator('.history-item').count(),3,'Reset creates new conversation and preserves history');
    await context.close();
    context=await browser.newContext({storageState:saved});await setup(context);
    await context.route('**/api/health',route=>route.abort());
    page=await context.newPage();await page.goto('http://127.0.0.1:8001');
    await page.locator('.message').first().waitFor();
    assert.equal(await page.locator('.message').count(),16,'Offline history opens without waiting for backend');
    await page.locator('#chat-input').fill('离线草稿');
    const offline=await context.storageState();
    const archived=JSON.parse(offline.origins[0].localStorage.find(x=>x.name.endsWith(':'+first)).value);
    assert.equal(archived.draft,'离线草稿');assert.equal(archived.attachment.dataset.dataset_id,dataA,'Offline draft save preserves attachment reference');
    await context.close();
    context=await browser.newContext();await setup(context);
    await context.addInitScript(()=>{const set=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k.startsWith('buck-history:'))throw new DOMException('Full','QuotaExceededError');return set.call(this,k,v);};});
    page=await context.newPage();await page.goto('http://127.0.0.1:8001');await ready(page);
    await send(page,'不能保存的测试');assert(await page.locator('#history-status').isVisible());
    assert.equal(await page.locator('.message').count(),2);
    assert.deepEqual(errors,[]);
    console.log('PASS: durable messages, drafts, metadata, isolation, CSV expiry, history continuation, busy guards, safe rendering, bilingual responsive layout, storage failure warning.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
