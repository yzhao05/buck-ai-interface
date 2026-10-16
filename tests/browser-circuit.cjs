const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1080}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const ready=()=>page.locator('#send-chat:not([disabled])').waitFor({timeout:60000});
 try {
  await page.goto('http://127.0.0.1:8001');await ready();
  const csv=fs.readFileSync('examples/plecs_buck_case16a.csv','utf8').split(/\r?\n/).filter(Boolean).map(s=>s.split(',').slice(0,3).join(',')).join('\n');
  await page.locator('#csv-file').setInputFiles({name:'circuit-test.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});await ready();
  await page.locator('#enter-identification').click();
  assert(await page.locator('#circuit-preview svg').isVisible());
  await page.locator('#edit-circuit').click();
  await page.locator('#circuit-drawing [data-parameter=R1_ohm]').click();
  assert.equal(await page.evaluate(()=>document.activeElement.id),'circuit-R1_ohm');
  await page.locator('#circuit-R1_ohm').fill('12');
  await page.locator('#circuit-C2_F').fill('1.3');
  await page.screenshot({path:'runtime/circuit-editor-chinese.png',fullPage:true});
  // Background archive sync must not dismiss the unsaved editor.
  await page.waitForTimeout(1600);
  assert(await page.locator('#circuit-dialog').isVisible());
  await page.locator('#save-circuit').click();
  assert.equal(await page.locator('#controller-confirmed').inputValue(),'no');
  await page.locator('#fit-method').selectOption('weighted_integral');
  await page.locator('#controller-confirmed').selectOption('yes');
  await page.locator('#identify').click();await ready();
  assert.notEqual(await page.locator('#result-l').textContent(),'—');
  const result=await page.evaluate(()=>state.result);
  assert.equal(result.diagnostics.controller.R1_ohm,12000);
  assert.equal(result.diagnostics.controller.C2_F,1.3e-9);
  assert.equal(result.options.controller.R1_ohm,12000);
  await page.reload();await ready();await page.locator('#enter-identification').click();
  assert.notEqual(await page.locator('#result-l').textContent(),'—','Nested controller parameters preserve restored results');
  await page.locator('#language-toggle').click();await page.locator('#edit-circuit').click();
  assert.equal(await page.locator('#circuit-R1_ohm').inputValue(),'12');
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#circuit-dialog').innerText()),'Editor copy must switch to English');
  await page.screenshot({path:'runtime/circuit-editor-english.png',fullPage:true});
  await page.locator('#circuit-duty_min').fill('0.95');
  await page.locator('#save-circuit').click();
  assert((await page.locator('#circuit-error').textContent()).includes('Minimum duty'));
  await page.locator('#circuit-duty_min').fill('0.1');
  await page.locator('#circuit-file').setInputFiles({name:'bad.png',mimeType:'image/png',buffer:Buffer.from('invalid')});
  await page.locator('#circuit-error').filter({hasText:'Cannot read'}).waitFor();
  const png=await page.evaluate(()=>{
   const c=document.createElement('canvas');c.width=500;c.height=240;const ctx=c.getContext('2d');
   ctx.fillStyle='white';ctx.fillRect(0,0,500,240);ctx.strokeRect(60,80,360,80);ctx.fillStyle='#087e73';ctx.font='24px sans-serif';ctx.fillText('Circuit reference',100,130);return c.toDataURL().split(',')[1];
  });
  await page.locator('#circuit-file').setInputFiles({name:'reference.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});
  await page.locator('#circuit-drawing img').waitFor();await page.locator('#save-circuit').click();
  assert(await page.locator('#circuit-preview img').isVisible());
  assert.equal(await page.locator('#result-l').textContent(),'—','Saving a reference resets results and confirmation');
  assert.equal(await page.locator('#controller-confirmed').inputValue(),'no');
  const savedImage=await page.locator('#controller-image').inputValue();
  await page.locator('#sync-now').click();await ready();
  const remote=await page.evaluate(async()=>{
   const a=await archiveRequest(`/conversations/${activeConversation.id}`);return a.record.fields;
  });
  assert.equal(remote['controller-image'],savedImage,'Reference image participates in conversation backup');
  assert.equal(JSON.parse(remote['controller-parameters']).R1_ohm,12000);
  await page.reload();await ready();await page.locator('#enter-identification').click();
  assert.equal(await page.locator('#controller-image').inputValue(),savedImage);
  assert(await page.locator('#circuit-preview img').isVisible());
  await page.locator('#show-default-circuit').click();assert(await page.locator('#circuit-preview svg').isVisible());
  await page.locator('#edit-circuit').click();assert.equal(await page.locator('#circuit-R1_ohm').inputValue(),'12','Restoring diagram retains parameters');
  for(const width of [900,390,320]){
   await page.setViewportSize({width,height:900});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   assert(await page.locator('#circuit-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth+1),'Dialog must contain horizontal diagram scrolling');
  }
  await page.screenshot({path:'runtime/circuit-editor-mobile.png',fullPage:true});
  await page.locator('#reset-controller').click();assert.equal(await page.locator('#circuit-R1_ohm').inputValue(),'11');
  await page.locator('#close-circuit').click();await page.locator('#edit-circuit').click();
  assert.equal(await page.locator('#circuit-R1_ohm').inputValue(),'12','Closing without saving discards draft');
  await page.locator('#close-circuit').click();
  const previousConversation=await page.evaluate(()=>activeConversation.id);
  await page.locator('#new-chat').click();await ready();
  assert.equal(await page.locator('#controller-image').inputValue(),'','A new conversation must not inherit the reference image');
  assert.equal(await page.locator('#controller-parameters').inputValue(),'','A new conversation must not inherit custom parameters');
  await page.evaluate(id=>openConversation(id),previousConversation);await ready();
  assert.equal(JSON.parse(await page.locator('#controller-parameters').inputValue()).R1_ohm,12000,'Switching conversations must restore custom settings');
  assert.deepEqual(errors,[]);
  console.log('PASS: circuit editing drives backend calculations; image validation/replacement, conversation sync/restore, confirmation reset, translations and mobile dialog.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
