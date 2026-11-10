// Test the real chat UI with a gated event stream and no external model.
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('../.tmp/browser/node_modules/playwright-core');

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true
  });
  try {
    const page = await browser.newPage();
    const timer = page.locator('.reply-timer');
    const row = page.locator('.message.assistant').last().locator('.message-task');
    let release, latest;
    let ending = new Promise(resolve => { release=resolve; });
    const result = {mode:'deepseek',answer:'Done',route:{label:'自主 Agent'},agent_budget:{elapsed_s:99}};
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/events')) {
        const terminal = await ending;
        return route.fulfill({contentType:'application/x-ndjson',body:JSON.stringify({kind:'end',data:terminal})+'\n'});
      }
      if (url.pathname.startsWith('/api/')) {
        const data = {
          '/api/health':{status:'ok',chat_mode:'deepseek',archive_enabled:false},
          '/api/agent/tasks':{id:'timed-task'},
          '/api/agent/tasks/timed-task':latest,
          '/api/agent/tasks/latest':{task:latest},
        };
        return route.fulfill({json:data[url.pathname] || {}});
      }
      return route.fulfill({path:path.join(__dirname,'../frontend',url.pathname==='/'?'index.html':url.pathname)});
    });
    async function ready() {
      await page.waitForFunction(() => state.connected && activeConversation && !historyLocked());
    }
    async function finish(data) {
      await timer.waitFor();
      await page.waitForFunction(() => parseFloat(document.querySelector('.reply-timer').textContent.match(/[\d.]+/)[0]) >= 0.1);
      release(data);
      await ready();
      ending = new Promise(resolve => { release=resolve; });
      assert.equal(await timer.count(),0);
    }
    await page.goto('http://127.0.0.1:5501/');
    await ready();
    await page.locator('#chat-input').fill('check timing');
    await page.locator('#send-chat').click();
    await timer.waitFor();
    assert.match(await timer.innerText(),/本次回复用时/);
    assert(await timer.evaluate(el=>Math.abs(el.getBoundingClientRect().right-el.parentNode.getBoundingClientRect().right)<2));
    await page.locator('#language-toggle').click();
    await page.waitForFunction(()=>document.querySelector('.reply-timer').textContent.startsWith('Response time:'));
    await finish({state:'completed',result});
    const final = await row.innerText();
    assert.match(final,/Task: Autonomous Agent · Response time: \d+\.\d{2} s$/);
    assert(!final.includes('99.00'));
    await page.reload();
    await ready();
    assert.equal(await row.innerText(),final);
    await page.locator('#chat-input').fill('stop timing');
    await page.locator('#send-chat').click();
    await finish({state:'cancelled',result:null});
    assert.match(await row.innerText(),/^Wait time:/);
    latest={id:'timed-task',state:'cancelled'};
    await page.locator('#agent-progress').evaluate(el=>{el.open=true;});
    await page.locator('#agent-resume').click();
    assert.match(await timer.innerText(),/^Recovery time:/);
    await finish({state:'completed',result});
    assert.match(await row.innerText(),/Recovery time:/);
    await page.reload();
    await ready();
    await page.locator('#language-toggle').click();
    assert.match(await row.innerText(),/本次恢复用时/);
    await page.locator('#chat-input').fill('provider error');
    await page.locator('#send-chat').click();
    await finish({state:'failed',result:{mode:'provider_error',answer:'',provider_error:'Mock provider error'}});
    const failureTime=await row.innerText();
    assert.match(failureTime,/^本次回复用时 \d+\.\d{2} 秒$/);
    await page.reload();
    await ready();
    assert.equal(await row.innerText(),failureTime);
    // Archive entries without browser measurements keep their own timing semantics.
    const legacy = await page.evaluate(()=>{
      const article=message('assistant','older');
      decorateResponse(article,{route:{label:'自主 Agent'},agent_budget:{elapsed_s:3}});
      return article.querySelector('.message-task').textContent;
    });
    assert.match(legacy,/后台处理用时 3.00 秒/);
    console.log('PASS: live timer, alignment, language switch, cancellation, recovery, provider failure and history');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
