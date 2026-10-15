// Run with a local, key-free backend on 127.0.0.1:8001.
const {chromium} = require('../.tmp/browser/node_modules/playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page = await browser.newPage({viewport:{width:1400,height:1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  async function ask(text) {
    await page.locator('#chat-input').fill(text);
    const response = page.waitForResponse(r => r.url().endsWith('/api/chat') && r.request().method() === 'POST');
    await page.locator('#send-chat').click();
    const data = await (await response).json();
    await page.waitForFunction(() => !document.getElementById('send-chat').disabled);
    return data;
  }
  try {
    await page.goto('http://127.0.0.1:8001/');
    await page.getByText('后端已连接', {exact:true}).waitFor();
    const formula = await ask('推导 Buck 补偿器传递函数');
    assert.equal(formula.route.task, 'knowledge');
    assert.equal(formula.job_id, null);
    assert.match(await page.locator('.message-task').last().innerText(), /任务：公式知识 · 本地规则分类/);
    await page.locator('#language-toggle').click();
    assert.equal(await page.locator('.message-task').last().innerText(), 'Task: Circuit knowledge · Local rule routing');
    const hardware = await ask('How to assess capacitor lifetime?');
    assert.equal(hardware.route.task, 'hardware');
    assert.equal(await page.locator('.message-task').last().innerText(), 'Task: Hardware & lifetime · Local rule routing');
    const blocked = await ask('开始参数辨识');
    assert.equal(blocked.route.task, 'identification');
    assert.equal(blocked.job_id, null);
    assert.match(blocked.answer, /进入参数辨识/);
    assert.equal(await page.locator('.message-task').last().innerText(), 'Task: Parameter identification · Local rule routing');
    // Exercise fallback/agent badges without contacting a model provider.
    for (const mode of ['fallback', 'agent']) {
      await page.route('**/api/chat', route => route.fulfill({json:{mode:'deepseek', answer:'Mock task response',
        route:{task:'knowledge',label:'公式知识',secondary_tasks:[],mode},sources:[],tool_calls:[],job_id:null}}));
      await ask('Explain this formula');
      assert.equal(await page.locator('.message-task').last().innerText(), mode === 'agent'
        ? 'Task: Circuit knowledge' : 'Task: Circuit knowledge · Classifier unavailable; local rules used');
      await page.unroute('**/api/chat');
    }
    await page.locator('#language-toggle').click();
    assert.equal(await page.locator('.message-task').last().innerText(), '任务：公式知识');
    await page.locator('.message.assistant').last().scrollIntoViewIfNeeded();
    await fs.mkdir('runtime', {recursive:true});
    await page.screenshot({path:'runtime/task-routing.png',fullPage:true});
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({passed:true,localRouting:true,readOnlyFormula:true,entryGuard:true,bilingualLabels:true,fallbackBadge:true,browserErrors:errors}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
