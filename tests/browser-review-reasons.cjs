const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict'),fs=require('node:fs');
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
  assert.equal(await page.locator('#result-badge').textContent(),'需复核');
  assert.match(await page.locator('#review-reasons').innerText(),/归一化 RMSE: .+% > 5%/);
  const downloadPromise=page.waitForEvent('download');await page.locator('#download-report').click();
  const download=await downloadPromise;await download.saveAs('runtime/review-reasons-report.json');
  const report=JSON.parse(fs.readFileSync('runtime/review-reasons-report.json','utf8'));
  assert(report.result.review_reasons.some(r=>r.code.endsWith('nrmse')));
  // Old result compatibility and a small-RMSE PANN budget-only fixture.
  await page.evaluate(()=>{
   state.result={...state.result,method:'pann',review_reasons:undefined,metrics:{...state.result.metrics,current_nrmse_pct:.02,voltage_nrmse_pct:.01},diagnostics:{...state.result.diagnostics,parameter_at_bound:false,CR_condition_number:10,pann:{stop_reason:'time_budget',best_at_budget_end:true,epochs:100,completed_epochs:72,best_epoch:72}}};
   displayResult();
  });
  assert.equal(await page.locator('#review-reason-list li').count(),2);
  assert.match(await page.locator('#review-reasons').innerText(),/72 \/ 100/);
  assert(!/归一化 RMSE/.test(await page.locator('#review-reason-list').innerText()));
  await page.locator('#language-toggle').click();
  const english=await page.locator('#review-reasons').innerText();
  assert.match(english,/training time budget/);assert.match(english,/final training evaluation/);assert(!/[\u4e00-\u9fff]/.test(english));
  await page.locator('#review-reasons').scrollIntoViewIfNeeded();await page.screenshot({path:'runtime/review-reasons-english.png'});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.evaluate(()=>{state.result={...state.result,quality:'模型内拟合一致',review_reasons:[]};displayResult();});
  assert(await page.locator('#review-reasons').isHidden());
  await page.evaluate(()=>{state.result={...state.result,quality:'需复核',review_reasons:[],diagnostics:{}};displayResult();});
  assert.match(await page.locator('#review-reasons').innerText(),/no identifiable review reason/);
  await page.locator('#vin').fill('29');assert(await page.locator('#review-reasons').isHidden());
  assert.deepEqual(errors,[]);
  console.log('PASS: actual quality triggers and exported evidence, old result fallback, PANN budget-only reasons, bilingual/mobile display, unknown and invalidated results.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
