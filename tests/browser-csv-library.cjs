const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1050}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let fits=0;page.on('request',r=>{if(r.url().endsWith('/api/identify'))fits++;});
 const ready=()=>page.locator('#send-chat:not([disabled])').waitFor({timeout:60000});
 const openMenu=async()=>{await ready();await page.locator('#toggle-csv-library').click();await ready();};
 const closeMenu=async()=>{await page.keyboard.press('Escape');};
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  await openMenu();assert((await page.locator('#csv-library-status').textContent()).includes('暂无历史'));await closeMenu();
  const raw=fs.readFileSync('examples/plecs_buck_case16a.csv','utf8');
  const upload=async(name,text)=>{await page.locator('#csv-file').setInputFiles({name,mimeType:'text/csv',buffer:Buffer.from(text)});await ready();};
  await upload('first.csv',raw);
  assert(await page.locator('#analysis-panel').isVisible());
  assert(await page.locator('#chart-container').isVisible());
  assert(await page.locator('#identification-form').isHidden());
  assert(await page.locator('#results-card').isHidden());
  assert(await page.locator('#current-chart path').count()>0);
  const source=await page.evaluate(()=>({id:activeConversation.id,sha:state.dataset.sha256,prefix:conversationPrefix}));
  await upload('second.csv',raw.replaceAll('12.', '12.1'));
  const secondSha=await page.evaluate(()=>state.dataset.sha256);assert.notEqual(secondSha,source.sha);
  await page.locator('#sync-now').click();await ready();
  await page.locator('#new-chat').click();await ready();
  const destination=await page.evaluate(()=>activeConversation.id);
  await openMenu();assert.equal(await page.locator('.csv-library-item').count(),2,'Include replaced files in a conversation');
  const first=()=>page.locator(`.csv-library-item[data-conversation-id="${source.id}"][data-sha256="${source.sha}"]`);
  await first().click();await ready();
  assert.equal(await page.evaluate(()=>activeConversation.id),destination,'Load into current chat without switching');
  assert.equal(await page.locator('#file-name').textContent(),'first.csv');
  assert(await page.locator('#chart-container').isVisible());
  await page.locator('#sync-now').click();await ready();
  // Remove both local source blobs: the menu must still discover the old,
  // no-longer-attached first file from the server archive.
  await page.evaluate(async source=>{clearTimeout(archiveTimer);archiveApplying=true;await BuckAttachments.remove(source.prefix+source.id);},source);
  await openMenu();assert.equal(await page.locator('.csv-library-item').count(),3);
  const second=()=>page.locator(`.csv-library-item[data-conversation-id="${source.id}"][data-sha256="${secondSha}"]`);
  await second().click();await ready();
  assert.equal(await page.locator('#file-name').textContent(),'second.csv','Remote-only history restores into current chat');
  assert.equal(await page.evaluate(()=>activeConversation.id),destination);
  await page.locator('#language-toggle').click();await openMenu();
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#csv-library-menu').innerText()));
  await page.screenshot({path:'runtime/csv-library-desktop.png',fullPage:true});
  for(const width of [900,390,320]){
   await page.setViewportSize({width,height:900});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  }
  await page.screenshot({path:'runtime/csv-library-mobile.png',fullPage:true});
  await page.setViewportSize({width:1500,height:1050});
  // A deletion after listing must not resurrect a source CSV or replace the
  // valid current file. It must disappear from the next menu refresh.
  await page.evaluate(id=>archiveRequest(`/conversations/${id}`,{method:'DELETE'}),source.id);
  await first().click();await ready();
  assert.equal(await page.locator('#file-name').textContent(),'second.csv');
  assert((await page.locator('#notice-text').textContent()).includes('deleted'));
  await openMenu();assert.equal(await page.locator(`.csv-library-item[data-conversation-id="${source.id}"]`).count(),0);await closeMenu();
  await page.evaluate(()=>{archiveApplying=false;});
  await page.locator('#sync-now').click();await ready();
  await page.reload();await ready();
  assert.equal(await page.locator('#file-name').textContent(),'second.csv');
  assert(await page.locator('#chart-container').isVisible());
  await page.locator('#clear-data').click();await ready();
  await openMenu();assert.equal(await page.locator('.csv-library-item').count(),0,'Clear removes all retained files of this conversation');await closeMenu();
  // Generic columns require a manual correction. Success must open the
  // waveform preview even when the original automatic mapping failed.
  const reordered='signal1,signal2,signal3\n'+raw.trim().split(/\r?\n/).slice(1).map(row=>{
   const values=row.split(',');return [values[1],values[0],values[2]].join(',');
  }).join('\n');
  await upload('needs-mapping.csv',reordered);
  assert.equal(await page.locator('#stat-points').textContent(),'Data import or validation failed');
  assert(await page.locator('#analysis-panel').isHidden());
  await page.locator('#map-time').selectOption('signal2');
  await page.locator('#map-current').selectOption('signal1');
  await page.locator('#apply-mapping').click();await ready();
  assert(await page.locator('#chart-container').isVisible(),'Corrected mappings must automatically show waveforms');
  assert(await page.locator('#identification-form').isHidden());
  assert.equal(fits,0,'Uploading/reusing CSVs must not start identification');
  assert.deepEqual(errors,[]);
  console.log('PASS: automatic waveform, replaced-file history, current-chat reuse, remote-only recovery, deletion/clear protection, reload, bilingual menu and responsive layout.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
