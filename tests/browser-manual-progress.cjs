const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('../.tmp/browser/node_modules/playwright-core');

(async () => {
  const browser = await chromium.launch({
    executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true
  });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let uploads = 0;
    let record = {status:'processing', steps:[{stage:'extract'}]};
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/manuals/import') return route.fulfill({json:{job_id:String(++uploads)}});
      if (url.pathname.startsWith('/api/manuals/import/')) return route.fulfill({json:record});
      if (url.pathname === '/api/health') return route.fulfill({json:{chat_mode:'deepseek',archive_enabled:false}});
      if (url.pathname.startsWith('/api/')) return route.fulfill({json:{}});
      return route.fulfill({path:path.join(__dirname,'../frontend',url.pathname === '/'?'index.html':url.pathname)});
    });
    const ready = () => page.waitForFunction(() => state.connected && activeConversation && !historyLocked());
    const upload = () => page.locator('#manual-file').setInputFiles({
      name:'fixture.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4')
    });
    await page.goto('http://127.0.0.1:5501/');
    await ready();
    const owner = await page.evaluate(() => activeConversation.id);
    await page.locator('#agent-progress').evaluate(el => { el.open=true; });
    await upload();
    await page.getByRole('status').filter({hasText:'2/4 · 提取手册条目'}).waitFor();
    assert.equal(await page.locator('#agent-progress').evaluate(el => el.open),false);
    await page.evaluate(() => newConversation());
    assert.equal(await page.locator('#manual-progress').isVisible(),false);
    record = {status:'failed',steps:[{stage:'extract'},{stage:'validate'}],
      error:'手册条目校验失败（已自动重试）：entries[0].conditions: Field required'};
    await page.evaluate(id => openConversation(id),owner);
    await page.locator('#manual-outcome').filter({hasText:'entries[0].conditions'}).waitFor();
    await page.locator('#language-toggle').click();
    assert.match(await page.locator('#manual-status').innerText(),/Manual import failed.*2\/4/);
    assert.match(await page.locator('#manual-outcome').innerText(),/after one automatic retry/);
    record = {status:'completed',steps:[{stage:'verify',page:3},{stage:'save'}],
      result:{entries:2,verified:1,unreadable:[],already_saved:false}};
    await upload();
    await page.locator('#manual-outcome').filter({hasText:'Entries saved 2'}).waitFor();
    await page.reload(); await ready();
    await page.locator('#manual-status').filter({hasText:'Manual import complete'}).waitFor();
    assert.match(await page.locator('#manual-steps').innerText(),/3\/4 .*PDF p. 3/);
    assert.equal(await page.locator('#manual-filename').textContent(),'fixture.pdf');
    assert.equal(uploads,2);
    assert.deepEqual(errors,[]);
    console.log('PASS: live manual steps, past chat isolation, validation errors, language switch, completion and reload');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
