const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('../.tmp/browser/node_modules/playwright-core');

(async () => {
  const root = path.resolve(__dirname, '../frontend');
  const server = http.createServer(async (req, res) => {
    try {
      const file = path.resolve(root, '.' + (req.url === '/' ? '/index.html' : req.url.split('?')[0]));
      if (!file.startsWith(root + path.sep)) throw Error('Invalid path');
      res.setHeader('Content-Type', ({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml'})[path.extname(file)] || 'application/octet-stream');
      res.end(await fs.readFile(file));
    } catch { res.statusCode = 404; res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless:true});
  try {
    const page = await browser.newPage(), dialogs = [], errors = [], requests = [];
    page.on('request', r => {if(r.url().endsWith('/api/agent/tasks')) requests.push(r.postDataJSON());});
    page.on('dialog', async dialog => { dialogs.push(dialog.message()); await dialog.accept(); });
    page.on('pageerror', error => errors.push(error.message));
    let response = {mode:'provider_error',answer:'',error_code:'missing_api_key',provider_error:'AI service is not configured. Please set DEEPSEEK_API_KEY on the backend and try again.',sources:[],tool_calls:[],job_id:null};
    await page.route('**/api/**', route => route.fulfill({json: route.request().url().endsWith('/api/health')
      ? {chat_mode:'unavailable',archive_enabled:false,pann_available:false} : response}));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(() => state.connected && activeConversation && !historyLocked());
    async function ask() {
      await page.locator('#chat-input').fill('解释 Buck 公式');
      await page.locator('#send-chat').click();
      await page.waitForFunction(() => !state.chatBusy && !document.getElementById('send-chat').disabled);
    }
    await page.locator("#reasoning-mode").selectOption("thinking");
    await ask();
    assert.equal(requests.at(-1).reasoning_mode,"thinking");
    assert.equal(dialogs.length, 1);
    assert.match(dialogs[0], /DEEPSEEK_API_KEY/);
    assert.equal(await page.evaluate(() => activeConversation.messages.at(-1).mode), '请求未完成');
    response = {...response,error_code:'provider_failed',provider_error:'AI request failed (HTTP 401). Please check the API key, model settings and network connection, then try again.'};
    await ask();
    assert.equal(dialogs.length, 2);
    assert.match(dialogs[1], /HTTP 401/);
    await page.locator('#reasoning-mode').selectOption('fast');
    await page.evaluate(() => localStorage.setItem(`buck-plecs:${base}`,JSON.stringify({executable:'missing.exe'})));
    await page.route('**/api/plecs/settings', route => route.fulfill({status:422,json:{detail:'Invalid PLECS settings'}}));
    response = {mode:'deepseek',answer:'模型说明 [kb:buck-physics]',sources:[{id:'buck-physics',title:'Buck 模型'}],
      tool_calls:[{name:'search_knowledge',query:'Buck 模型',count:3,status:'已执行'}],job_id:null};
    await ask();
    assert.equal(dialogs.length, 2);
    assert.equal(requests.at(-1).reasoning_mode,"fast");
    await page.reload();
    await page.waitForFunction(() => state.connected && activeConversation && !historyLocked());
    assert.equal(await page.locator("#reasoning-mode").inputValue(),"fast");
    assert.match(await page.locator('.message.assistant').last().innerText(), /Buck 模型/);
    assert.equal(await page.evaluate(() => activeConversation.messages.at(-1).mode), 'DeepSeek');
    assert.deepEqual(errors, []);
    console.log('PASS: English missing-key/failure dialogs, retry, query trace, cited source, no page errors');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
