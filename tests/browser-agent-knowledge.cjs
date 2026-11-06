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
    await require('./harness-browser-fixture.cjs').install(page);
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
    for(const failure of [
      {error_code:'provider_timeout',provider_error:'AI provider or Harness communication timed out before completion.'},
      {error_code:'output_token_limit',finish_reason:'max-tokens',provider_error:'AI generation reached its output-token limit, including thinking tokens, before completing the answer.'},
      {error_code:'citation_validation_failed',provider_error:'The generated answer cited knowledge sources that were not retrieved in this task.'},
      {error_code:'agent_budget_exceeded',budget_reason:'estimated_cost',
       provider_error:'AI request exceeded the estimated cost reservation (AGENT_COST_LIMIT_USD).',
       agent_budget:{thinking:true,model_calls:0,model_call_limit:14,time_limit_s:300,reserved_cost_usd:0,cost_limit_usd:0.5}}
    ]){
      response={mode:'provider_error',answer:'',sources:[],tool_calls:[],...failure};
      await ask();
      assert.equal(dialogs.at(-1),failure.provider_error);
      const stored=await page.evaluate(()=>readConversation(activeConversation.id).messages.at(-1).response);
      assert.equal(stored.error_code,failure.error_code);
      assert.equal(stored.budget_reason,failure.budget_reason);
      assert.equal(stored.finish_reason,failure.finish_reason);
      assert.deepEqual(stored.agent_budget,failure.agent_budget);
      if(failure.agent_budget)assert.doesNotMatch(await page.locator('.message.assistant').last().innerText(),/本轮本地费用估算|USD \$/);
      assert.equal(typeof stored.agent_task_id,'string');
    }
    await page.locator('#reasoning-mode').selectOption('fast');
    await page.evaluate(() => localStorage.setItem(`buck-plecs:${base}`,JSON.stringify({executable:'missing.exe'})));
    await page.route('**/api/plecs/settings', route => route.fulfill({status:422,json:{detail:'Invalid PLECS settings'}}));
    response = {mode:'deepseek',answer:'模型说明 [kb:buck-physics]',sources:[{id:'buck-physics',title:'Buck 模型'}],
      tool_calls:[{name:'search_knowledge',query:'Buck 模型',count:3,status:'已执行'}],job_id:null};
    await ask();
    assert.equal(dialogs.length, 6);
    assert.equal(requests.at(-1).reasoning_mode,"fast");
    await page.reload();
    await page.waitForFunction(() => state.connected && activeConversation && !historyLocked());
    assert.equal(await page.locator("#reasoning-mode").inputValue(),"fast");
    assert.match(await page.locator('.message.assistant').last().innerText(), /Buck 模型/);
    assert.equal(await page.locator('.message.assistant > .message-sources').count(),0);
    assert.deepEqual(await page.evaluate(()=>readConversation(activeConversation.id).messages.at(-1).response.sources),response.sources);
    assert.match(await page.locator('.message.assistant').last().innerText(),/\[kb:buck-physics\]/);
    assert.doesNotMatch(await page.locator('body').innerText(),/支持选择、粘贴或拖入图片|对话、实验记录和 CSV 支持浏览器与后端双端保存/);
    const restoredBudget=await page.evaluate(()=>readConversation(activeConversation.id).messages
      .find(m=>m.response?.error_code==='agent_budget_exceeded')?.response);
    assert.equal(restoredBudget.budget_reason,'estimated_cost');
    assert.equal(restoredBudget.agent_budget.model_call_limit,14);
    assert.equal(typeof restoredBudget.agent_task_id,'string');
    assert.equal(await page.evaluate(() => activeConversation.messages.at(-1).mode), 'DeepSeek');
    await page.locator('#language-toggle').click();
    assert.equal(await page.locator('.message-tools').filter({hasText:'Local estimate for this answer (including reservations, not a bill)'}).count(),0);
    assert.deepEqual(await page.locator('#reasoning-mode option').allTextContents(), ['Auto mode','Fast mode','Thinking mode']);
    assert.deepEqual(errors, []);
    console.log('PASS: HTTP/timeout/budget errors and saved diagnostics, retry, query trace, cited source, no page errors');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
