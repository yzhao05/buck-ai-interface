const {downloadReportFiles}=require('./report-download-helper.cjs');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[],chats=[];let fits=0;
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url().endsWith('/api/identify'))fits++;if(r.url().endsWith('/api/chat'))chats.push(r.postDataJSON());});
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
 const choose=async id=>{await page.locator('#toggle-result-library').click();await page.locator(`[data-result-id="${id}"]`).click();await ready();};
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.locator('#load-demo').click();await ready();await page.locator('#enter-identification').click();
  await page.locator('#toggle-result-library').click();assert.match(await page.locator('#result-library-status').innerText(),/暂无/);await page.keyboard.press('Escape');
  await page.locator('#fit-method').selectOption('weighted_integral');await page.locator('#fit-mode').selectOption('LCR');
  await page.locator('#identify').click();await page.waitForFunction(()=>state.result&&!state.job);await ready();
  await page.locator('#truth-l').fill('60');await page.locator('#truth-c').fill('500');await page.locator('#calculate-parameter-error').click();
  const first=await page.evaluate(()=>({id:state.resultId,p:state.result.parameters,truth:parameterComparison(),owner:activeConversation.id}));
  await page.locator('#fit-mode').selectOption('LC');await page.locator('#fixed-r').fill('6');
  await page.locator('#identify').click();await page.waitForFunction(id=>state.resultId&&state.resultId!==id&&!state.job,first.id);await ready();
  const second=await page.evaluate(()=>({id:state.resultId,p:state.result.parameters}));
  await page.locator('#vin').fill('31');
  await page.locator('#toggle-result-library').click();
  assert.deepEqual(await page.locator('.result-library-item').evaluateAll(items=>items.map(e=>e.dataset.resultId)),[second.id,first.id]);
  const filename=await page.locator(`[data-result-id="${first.id}"] strong`).innerText();assert(filename.endsWith('.json'));
  await page.keyboard.press('ArrowDown');assert.equal(await page.evaluate(()=>document.activeElement.dataset.resultId),first.id);
  await page.keyboard.press('Enter');await ready();
  assert.deepEqual(await page.evaluate(()=>state.result.parameters),first.p);assert.equal(await page.locator('#vin').inputValue(),'31');
  assert.equal(await page.locator('#fit-mode').inputValue(),'LC');assert.deepEqual(await page.evaluate(()=>parameterComparison()),first.truth);
  await page.locator('#ask-error').click();await page.locator('#analyze-here').click();await ready();
  assert.equal(chats.at(-1).saved_result.id,first.id);assert.equal(chats.at(-1).dataset_id,null);assert.equal(chats.at(-1).options.mode,'LCR');
  assert(!JSON.stringify(chats.at(-1).saved_result).includes('"chart"'));
  assert(!await page.locator('.message.assistant').last().innerText().then(t=>t.includes('Saved result')));
  // An expired job still has a durable result, including its own truth comparison.
  await choose(second.id);assert.deepEqual(await page.evaluate(()=>state.result.parameters),second.p);assert.equal(await page.evaluate(()=>parameterComparison()),null);
  // The selected result can belong to a different CSV than the current import.
  await page.evaluate(async()=>{const file=new File([await state.csvFile.arrayBuffer()], 'another-experiment.csv', {type:'text/csv'});await importData(file);});await ready();
  await page.route('**/api/jobs/*',route=>route.fulfill({status:404,json:{detail:'expired test cache'}}));
  await choose(first.id);assert(await page.locator('#result-archive-note').isVisible());assert.equal(await page.evaluate(()=>state.result.chart),undefined);
  assert.equal(await page.evaluate(()=>state.dataset.filename),'another-experiment.csv');
  assert.notEqual(await page.evaluate(()=>analysisDescriptor().filename),'another-experiment.csv');
  assert(await page.evaluate(()=>JSON.stringify(state.chart)===JSON.stringify(state.summary.chart)));
  await page.reload();await ready();await page.waitForFunction(id=>state.savedRun?.id===id,first.id);
  assert.equal(await page.locator('#vin').inputValue(),'31');assert.deepEqual(await page.evaluate(()=>state.result.parameters),first.p);
  const downloads=[];page.on('download',d=>downloads.push(d));await downloadReportFiles(page);
  await page.waitForFunction(()=>!reportExportBusy);await page.waitForTimeout(300);
  assert.equal(downloads.length,2);assert.deepEqual(downloads.map(d=>d.suggestedFilename()).sort(),[filename,filename.replace('.json','.pdf')].sort());
  const json=downloads.find(d=>d.suggestedFilename().endsWith('.json'));await json.saveAs('runtime/result-library-report.json');
  assert.deepEqual(JSON.parse(fs.readFileSync('runtime/result-library-report.json','utf8')).result.parameters,first.p);
  await page.locator('#language-toggle').click();await page.locator('#toggle-result-library').click();
  assert.equal(await page.locator('#result-library-status').innerText(),'Completed results, newest first');
  assert(!/[\u4e00-\u9fff]/.test(await page.locator('#result-library-menu').innerText()));
  await page.screenshot({path:'runtime/result-library-desktop.png'});
  await page.setViewportSize({width:390,height:844});await page.locator('#result-library-menu').scrollIntoViewIfNeeded();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:'runtime/result-library-mobile.png'});
  await page.keyboard.press('Escape');
  // Copying the snapshot to a new conversation works without its CSV or cached job.
  await page.locator('#ask-error').click();await page.locator('#analyze-new').click();
  await page.waitForFunction(id=>activeConversation.id!==id&&activeConversation.messages.length===2&&!state.chatBusy,first.owner);await ready();
  assert.deepEqual(await page.evaluate(()=>state.result.parameters),first.p);assert.equal(await page.evaluate(()=>state.dataset),null);
  assert.equal(chats.at(-1).saved_result.id,await page.evaluate(()=>state.resultId));assert.equal(fits,2);
  assert.equal(await page.locator('#result-library-list button').count(),1,'Only the new conversation snapshot is listed');
  assert(!await page.locator('.message.assistant').last().innerText().then(t=>t.includes('请求未完成')));
  assert.deepEqual(errors,[]);
  console.log('PASS: current-conversation results, newest-first names, keyboard, selection/truth, left drafts, snapshot analysis, expired cache/reload, PDF+JSON, bilingual/mobile, new-conversation analysis, no refit');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
