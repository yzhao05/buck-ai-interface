const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict'), fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1050}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const ready=()=>page.locator('#load-demo:not([disabled])').waitFor({timeout:60000});
 try {
  await page.goto('http://127.0.0.1:8001');await ready();
  await page.locator('#load-demo').click();await ready();await page.locator('#enter-identification').click();
  await page.locator('#fit-method').selectOption('weighted_integral');await page.locator('#end-ms').fill('2');
  await page.locator('#identify').click();await page.locator('#identify-complete-hint').waitFor();await ready();
  for(const language of ['zh','en']) {
   if(language==='en')await page.locator('#language-toggle').click();
   const jsonWait=page.waitForEvent('download', d=>d.suggestedFilename().endsWith('.json'));
   const pdfWait=page.waitForEvent('download', d=>d.suggestedFilename().endsWith('.pdf'));
   await page.locator('#download-report').click();
   const [json,pdf]=await Promise.all([jsonWait,pdfWait]);
   assert.equal(json.suggestedFilename().replace(/\.json$/,'.pdf'),pdf.suggestedFilename());
   await json.saveAs(`runtime/report-export-${language}.json`);await pdf.saveAs(`runtime/report-export-${language}.pdf`);
   assert(fs.readFileSync(`runtime/report-export-${language}.pdf`).subarray(0,5).equals(Buffer.from('%PDF-')));
   const report=JSON.parse(fs.readFileSync(`runtime/report-export-${language}.json`,'utf8'));
   assert(report.result.review_reasons.some(r=>r.code.endsWith('nrmse')));
   assert(report.result.chart && report.result.file_sha256);
   await page.locator('#download-report:not([disabled])').waitFor();
   assert(await page.locator('#report-pdf-link').isVisible());assert(await page.locator('#report-json-link').isVisible());
  }
  assert(!/[\u4e00-\u9fff]/.test(await page.locator('#report-downloads').innerText()));
  // Explicit links allow either file to be saved again if multiple downloads were blocked.
  const retry=page.waitForEvent('download');await page.locator('#report-pdf-link').click();
  assert((await retry).suggestedFilename().endsWith('.pdf'));
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.locator('#report-downloads').scrollIntoViewIfNeeded();await page.screenshot({path:'runtime/report-export-mobile.png'});
  // JSON remains downloadable when backend PDF generation fails.
  await page.route('**/api/reports/pdf',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'PDF test unavailable'})}));
  const fallback=page.waitForEvent('download');await page.locator('#download-report').click();await fallback;
  await page.locator('#report-export-status').filter({hasText:'PDF generation failed'}).waitFor();
  assert(await page.locator('#report-pdf-link').isHidden());assert(await page.locator('#report-json-link').isVisible());
  assert(await page.locator('#download-report').isEnabled());
  await page.unroute('**/api/reports/pdf');
  // Changing settings while export is pending must not attach an old PDF to new results.
  let release;const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/reports/pdf',async route=>{await gate;await route.fulfill({status:200,contentType:'application/pdf',body:'%PDF-test'});});
  const pending=page.waitForRequest('**/api/reports/pdf');
  await page.locator('#download-report').click();await pending;
  await page.evaluate(()=>{document.getElementById('vin').value='29';document.getElementById('vin').dispatchEvent(new Event('input'));});
  const finished=page.waitForResponse('**/api/reports/pdf');release();await finished;
  await page.waitForTimeout(150);
  assert(await page.locator('#report-downloads').isHidden());assert(await page.locator('#download-report').isDisabled());
  assert.deepEqual(errors,[]);
  console.log('PASS: PDF + JSON, matching names, bilingual export, manual download links, backend failure, mobile layout and stale-export isolation.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
