const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1050}}),errors=[],downloads=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('download',d=>downloads.push(d));
 const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
 const finished=()=>page.waitForFunction(()=>!reportExportBusy);
 const picker=()=>page.evaluate(()=>{window.showDirectoryPicker=async options=>{
  window.testPickerOptions=options;window.testPickerActive=navigator.userActivation.isActive;
  const root=await navigator.storage.getDirectory();return root.getDirectoryHandle('chosen-reports',{create:true});
 };});
 try{
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.locator('#load-demo').click();await ready();await page.locator('#enter-identification').click();
  await page.locator('#fit-method').selectOption('weighted_integral');await page.locator('#end-ms').fill('2');
  await page.locator('#identify').click();await page.waitForFunction(()=>state.result&&!state.job);await ready();
  await picker();
  for(const language of ['zh','en']){
   if(language==='en')await page.locator('#language-toggle').click();
   await page.locator('#download-report').click();await finished();
   assert(await page.evaluate(()=>testPickerActive),'Picker must be called inside the original click activation');
   assert.equal(await page.evaluate(()=>testPickerOptions.mode),'readwrite');
   assert.match(await page.locator('#report-export-status').innerText(),language==='en'?/Report saved to/:/报告已保存到/);
   await page.locator('#report-downloads').scrollIntoViewIfNeeded();
   await page.screenshot({path:`runtime/report-folder-${language}.png`});
  }
  assert.equal(downloads.length,0,'Folder export must not also download loose files');
  const folders=await page.evaluate(async()=>{
   const root=await navigator.storage.getDirectory(),parent=await root.getDirectoryHandle('chosen-reports'),out=[];
   for await(const [name,handle] of parent.entries()){
    const files=[];for await(const [file,h] of handle.entries()){
     const blob=await h.getFile();files.push({name:file,prefix:await blob.slice(0,5).text(),json:file.endsWith('.json')?JSON.parse(await blob.text()):null});
    }out.push({name,files});
   }return out.sort((a,b)=>a.name.localeCompare(b.name));
  });
  assert.equal(folders.length,2);assert.equal(folders[1].name,folders[0].name+'_2');
  for(const folder of folders){
   assert.deepEqual(folder.files.map(f=>f.name).sort(),[folder.name+'.json',folder.name+'.pdf']);
   assert.equal(folder.files.find(f=>f.name.endsWith('.pdf')).prefix,'%PDF-');
   const report=folder.files.find(f=>f.json).json;
   assert(report.result.file_sha256&&report.result.chart);
   assert(report.result.review_reasons.some(r=>r.code.endsWith('nrmse')));
  }
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#report-downloads').innerText()));
  // Unsupported browsers explain save-location settings before any download.
  async function expectPair(action){
   const count=downloads.length;
   const jsonWait=page.waitForEvent('download',d=>d.suggestedFilename().endsWith('.json'));
   const pdfWait=page.waitForEvent('download',d=>d.suggestedFilename().endsWith('.pdf'));
   await action();const [json,pdf]=await Promise.all([jsonWait,pdfWait]);await finished();
   assert.equal(downloads.length,count+2);
   assert.equal(json.suggestedFilename(),folders[0].name+'.json');
   assert.equal(pdf.suggestedFilename(),folders[0].name+'.pdf');
   await json.saveAs('runtime/report-fallback.json');await pdf.saveAs('runtime/report-fallback.pdf');
   assert(JSON.parse(fs.readFileSync('runtime/report-fallback.json','utf8')).result.file_sha256);
   assert.equal(fs.readFileSync('runtime/report-fallback.pdf').subarray(0,5).toString(),'%PDF-');
  }
  await page.evaluate(()=>window.showDirectoryPicker=undefined);
  let fallbackRequests=0;
  const countFallback=r=>{if(r.url().endsWith('/api/reports/pdf'))fallbackRequests++;};page.on('request',countFallback);
  const beforeFallback=downloads.length;
  await page.locator('#download-report').click();
  assert(await page.locator('#report-location-dialog').isVisible());
  assert.match(await page.locator('#report-location-reason').innerText(),/does not provide folder selection/);
  assert.match(await page.locator('#report-location-dialog').innerText(),/Firefox: Settings/);
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#report-location-dialog').innerText()));
  assert.equal(downloads.length,beforeFallback);assert.equal(fallbackRequests,0);
  await page.keyboard.press('Escape');assert(await page.locator('#report-location-dialog').isHidden());
  assert.equal(downloads.length,beforeFallback);
  await page.locator('#language-toggle').click();await page.locator('#download-report').click();
  assert.match(await page.locator('#report-location-title').innerText(),/报告保存位置/);
  await page.locator('#close-report-location').click();await page.locator('#language-toggle').click();
  await page.locator('#download-report').click();
  await expectPair(()=>page.locator('#confirm-report-files').click());
  page.off('request',countFallback);assert.equal(fallbackRequests,1);
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#report-downloads').innerText()));
  assert.match(await page.locator('#report-export-status').innerText(),/browser download settings/);
  // Insecure pages get their own actionable explanation, even if an API exists.
  await picker();await page.evaluate(()=>{
   window.testSecureContextDescriptor=Object.getOwnPropertyDescriptor(window,'isSecureContext');
   Object.defineProperty(window,'isSecureContext',{value:false,configurable:true});
  });
  await page.locator('#download-report').click();
  assert.match(await page.locator('#report-location-reason').innerText(),/not in a secure context/);
  await page.locator('#close-report-location').click();await page.evaluate(()=>{
   if(window.testSecureContextDescriptor)Object.defineProperty(window,'isSecureContext',window.testSecureContextDescriptor);
   else delete window.isSecureContext;
   delete window.testSecureContextDescriptor;
  });
  // User cancellation causes no PDF request, filesystem writes or download.
  let pdfRequests=0;const count=r=>{if(r.url().endsWith('/api/reports/pdf'))pdfRequests++;};page.on('request',count);
  await page.evaluate(()=>window.showDirectoryPicker=async()=>{throw new DOMException('Cancelled','AbortError');});
  const before=downloads.length;await page.locator('#download-report').click();await finished();
  assert.match(await page.locator('#report-export-status').innerText(),/Export cancelled/);
  assert.equal(pdfRequests,0);assert.equal(downloads.length,before);
  // Folder permission errors offer ordinary file downloads.
  await page.evaluate(()=>window.showDirectoryPicker=async()=>{throw new DOMException('Denied','NotAllowedError');});
  await page.locator('#download-report').click();await finished();assert.match(await page.locator('#report-export-status').innerText(),/Cannot access/);
  await expectPair(()=>page.locator('#download-report-files').click());
  // Failed folder writes fall back to two downloads, with a partial-write warning.
  await page.evaluate(()=>window.showDirectoryPicker=async()=>({name:'blocked',getDirectoryHandle:async()=>{throw new DOMException('Write aborted','AbortError');}}));
  await expectPair(()=>page.locator('#download-report').click());
  assert.match(await page.locator('#report-export-status').innerText(),/Folder saving was incomplete/);
  assert(await page.locator('#report-json-link').isVisible());assert(await page.locator('#report-pdf-link').isVisible());
  // PDF failure keeps JSON as an explicit download; no incomplete folder is made.
  await picker();
  await page.route('**/api/reports/pdf',r=>r.fulfill({status:503,json:{detail:'PDF test unavailable'}}));
  await page.locator('#download-report').click();await finished();
  assert.match(await page.locator('#report-export-status').innerText(),/PDF generation failed/);
  assert(await page.locator('#report-pdf-link').isHidden());assert(await page.locator('#report-json-link').isVisible());
  await page.unroute('**/api/reports/pdf');
  // Switching results while generating a PDF must discard the old export.
  let release;const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/reports/pdf',async r=>{await gate;await r.fulfill({contentType:'application/pdf',body:'%PDF-test'});});
  const pending=page.waitForRequest('**/api/reports/pdf');await page.locator('#download-report').click();await pending;
  await page.evaluate(()=>invalidateResult());const response=page.waitForResponse('**/api/reports/pdf');release();await response;
  await page.waitForTimeout(150);assert(await page.locator('#report-downloads').isHidden());
  assert.equal(await page.evaluate(async()=>{
   const root=await navigator.storage.getDirectory(),parent=await root.getDirectoryHandle('chosen-reports');let n=0;for await(const _ of parent.entries())n++;return n;
  }),2,'Failed and stale exports must not create report folders');
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.deepEqual(errors,[]);
  console.log('PASS: chosen directory, real file writes, matching names, collision suffixes, bilingual save-location guidance, no silent fallback, insecure context, two-file fallback, cancellation, denied access, partial-write warning, PDF failure, stale isolation and mobile width.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
