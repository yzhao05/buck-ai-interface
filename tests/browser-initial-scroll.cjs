// Exercise native scrollbars inside a Studio-sized iframe, before any data upload.
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('../.tmp/browser/node_modules/playwright-core');

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, ignoreDefaultArgs: ['--hide-scrollbars']
  });
  try {
    const page = await browser.newPage({viewport: {width: 1450, height: 760}});
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname === 'host.test') return route.fulfill({contentType:'text/html', body:
        '<style>body{margin:0;padding-top:72px}iframe{display:block;width:100%;height:calc(100dvh - 96px);border:0}</style><iframe src="https://localhost/"></iframe>'});
      if (url.pathname.startsWith('/api/')) return route.fulfill({json: url.pathname === '/api/health'
        ? {status:'ok',chat_mode:'unavailable',archive_enabled:false,pann_available:false} : {}});
      return route.fulfill({path:path.join(__dirname,'../frontend', url.pathname === '/' ? 'index.html' : url.pathname)});
    });
    await page.goto('https://host.test/');
    const app = page.frames().find(frame => frame.url().startsWith('https://localhost/'));
    await app.waitForFunction(() => state.connected && activeConversation);
    const sidebar = app.locator('#sidebar-panel');
    const chat = app.locator('#chat-messages');
    assert(await app.locator('#analysis-panel').isHidden());
    // A different actual header height used to extend the document past the frame.
    await app.locator('.topbar').evaluate(el => { el.style.height = '97px'; });
    for (const width of [1450, 1024]) {
      await page.setViewportSize({width, height:760});
      assert(await app.evaluate(() => document.scrollingElement.scrollHeight <= innerHeight + 1 &&
        Math.abs(document.getElementById('workspace').getBoundingClientRect().bottom - innerHeight) <= 1));
      assert(await sidebar.evaluate(el => el.scrollHeight > el.clientHeight));
    }
    const box = await sidebar.boundingBox();
    const gutter = await sidebar.evaluate(el => el.offsetWidth - el.clientWidth - el.clientLeft);
    assert(gutter > 2, 'Sidebar must expose its own native scrollbar');
    await page.mouse.move(box.x + box.width - gutter/2, box.y + 38);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width - gutter/2, box.y + 140, {steps:10});
    await page.mouse.up();
    assert(await sidebar.evaluate(el => el.scrollTop > 0), 'Dragging the sidebar thumb must scroll the sidebar');
    const left = await sidebar.evaluate(el => el.scrollTop);
    await chat.evaluate(el => { const item = document.createElement('div'); item.style.minHeight = '1800px'; el.append(item); });
    const right = await chat.boundingBox();
    const thumbX = right.x + right.width - (await chat.evaluate(el => el.offsetWidth - el.clientWidth))/2;
    await page.mouse.move(thumbX, right.y + 28);
    await page.mouse.down();
    await page.mouse.move(thumbX, right.y + 105, {steps:10});
    await page.mouse.up();
    const dragged = await chat.evaluate(el => el.scrollTop);
    assert(dragged > 0, 'Dragging the right thumb must scroll the chat');
    await chat.hover();
    await page.mouse.wheel(0, 330);
    await app.waitForFunction(top => document.getElementById('chat-messages').scrollTop > top, dragged);
    assert.equal(await sidebar.evaluate(el => el.scrollTop),left);
    assert.equal(await app.evaluate(() => scrollY),0);
    assert.equal(await page.evaluate(() => scrollY),0);
    await page.setViewportSize({width:390,height:760});
    assert(await app.evaluate(() => document.scrollingElement.scrollHeight > innerHeight), 'Stacked mobile layout must remain scrollable');
    console.log('PASS: initial iframe bounds, native sidebar drag, independent chat wheel, mobile page scrolling');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
