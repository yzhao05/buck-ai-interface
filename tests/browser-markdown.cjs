const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict');
const source=[
 '## 一、事实（工具返回的客观数据）','',
 '**1. 辨识输出（method=pann，R 固定 6 Ω，未估计） **','',
 '| 量 | 值 |','| :--- | ---: |','| L | 59.9769 µH |','| C | 500.030 µF |','| quality | **需复核** |','',
 '### 二、下一步','', '1. 增加训练轮数','2. 保持其他参数不变','   - 对照窗口长度','',
 '> 波形误差不等于参数误差。','',
 '使用 `pwm_source=controller`，参考 [项目说明](https://github.com/yzhao05/buck-ai-interface)。','',
 '```python','print("L = 60 µH")','print("'+ 'long_value_'.repeat(25) +'")','```','',
 '~~旧的时间限制~~ 已移除。','',
 '```text','**literal heading **','<script>window.markdownAttack=1</script>','```',
].join('\n');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1100}});
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 const page=await context.newPage(),errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>requests.push(r.url()));
 const chats=[];
 await page.route('**/api/chat',async route=>{
  chats.push(route.request().postDataJSON());
  await route.fulfill({json:{answer:source,mode:'deepseek',tool_calls:[],sources:[]}});
 });
 const ready=()=>page.waitForFunction(()=>activeConversation && state.connected && !historyLocked());
 try {
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.locator('#chat-input').fill('**分析辨识结果**');await page.locator('#send-chat').click();await ready();
  const reply=page.locator('.message.assistant .message-content').last();
  assert.equal(await reply.locator('h2').textContent(),'一、事实（工具返回的客观数据）');
  assert.equal(await reply.locator('table tbody tr').count(),3);
  assert.match(await reply.locator('strong').first().textContent(),/^1\. 辨识输出/);
  assert.equal(await reply.locator('ol > li').count(),2);assert.equal(await reply.locator('blockquote').count(),1);
  assert.equal(await reply.locator('s,del').count(),1);assert.equal(await reply.locator('pre code').count(),2);
  assert.match(await reply.locator('pre code').last().textContent(),/\*\*literal heading \*\*/);
  assert.equal(await page.locator('.message.user strong').count(),0);
  const link=reply.locator('a');assert.equal(await link.getAttribute('target'),'_blank');assert.match(await link.getAttribute('rel'),/noopener/);
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).messages.at(-1).content),source);
  await reply.locator('.markdown-copy').first().click();
  assert.equal((await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n'),await reply.locator('pre code').first().textContent());
  await reply.locator('h2').scrollIntoViewIfNeeded();
  await page.waitForTimeout(200);
  await page.screenshot({path:'runtime/markdown-desktop.png'});
  await page.reload();await ready();
  assert.equal(await page.locator('.message.assistant table').count(),1);
  assert.equal(await page.evaluate(()=>readConversation(activeConversation.id).messages.at(-1).content),source);
  const id=await page.locator('.history-item[aria-current=true]').getAttribute('data-conversation-id');
  await page.locator('#new-chat').click();await ready();
  await page.locator(`.history-item[data-conversation-id="${id}"]`).click();await ready();
  assert.equal(await page.locator('.message.assistant h2').count(),1);
  await page.locator('#chat-input').fill('继续分析');await page.locator('#send-chat').click();await ready();
  assert.equal(chats.at(-1).history.find(m=>m.role==='assistant').content,source);
  await page.locator('#language-toggle').click();
  assert.equal(await reply.locator('.markdown-copy').first().textContent(),'Copy code');
  assert.equal(await reply.locator('h2').textContent(),'一、事实（工具返回的客观数据）'); // Do not translate model/user content.
  await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('denied');}}}));
  await reply.locator('.markdown-copy').first().click();
  assert.equal(await reply.locator('.markdown-copy').first().textContent(),'Copy manually');
  assert.equal((await page.evaluate(()=>window.getSelection().toString())).trimEnd(),(await reply.locator('pre code').first().textContent()).trimEnd());
  const malicious='<script>window.markdownAttack=1</script>\n\n<img src="https://tracker.invalid/a" onerror="window.markdownAttack=2">\n\n[bad](javascript:alert(1)) [bad](data:text/html,evil) [bad](file:///etc/passwd) ![image](https://tracker.invalid/b)\n\n<svg onload="window.markdownAttack=3"></svg>\n\n<iframe srcdoc="evil"></iframe>';
  await page.evaluate(text=>message('assistant',text),malicious);
  const unsafe=page.locator('.message.assistant .message-content').last();
  assert.equal(await unsafe.locator('script,img,svg,iframe,object,style').count(),0);
  assert(await unsafe.locator('a').evaluateAll(links=>links.every(a=>/^(https?:|mailto:)/i.test(a.getAttribute('href')))));
  assert.equal(await page.evaluate(()=>window.markdownAttack),undefined);
  assert(!requests.some(url=>url.includes('tracker.invalid')));
  // A wide table and code block scroll internally rather than widening the page.
  await page.evaluate(()=>message('assistant','| A | B | C | D |\n|---|---|---|---|\n| '+ '1234567890'.repeat(20)+' | 2 | 3 | 4 |'));
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  const scroll=page.locator('.markdown-table-wrap').last();
  assert(await scroll.evaluate(el=>el.scrollWidth>el.clientWidth));
  assert(await page.locator('.markdown-code-block pre').first().evaluate(el=>el.scrollWidth>el.clientWidth));
  await page.locator('.message.assistant h2').first().scrollIntoViewIfNeeded();await page.waitForTimeout(200);
  await page.screenshot({path:'runtime/markdown-mobile.png'});
  assert.deepEqual(errors,[]);
  console.log('PASS: Markdown blocks/inlines, spacing typo, literal code, safe links/HTML/images, source history, reload/switch, bilingual controls, code copy and mobile overflow.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
