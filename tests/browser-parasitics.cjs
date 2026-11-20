const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict');
(async()=>{
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const page=await browser.newPage({viewport:{width:1500,height:1080}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning&&!archiveBootstrap,{},{timeout:90000});
try{
 await page.goto(process.env.BUCK_TEST_URL || 'http://127.0.0.1:8018');await ready();
 await page.locator('#enter-identification').click();
 assert.equal(await page.locator('#include-parasitics').inputValue(),'false');
 await page.locator('#load-demo').click();await ready();
 await page.locator('#fit-method').selectOption('weighted_integral');
 await page.locator('#pwm-source').selectOption('measured');
 await page.locator('#include-parasitics').selectOption('true');
 await page.locator('#fit-mode').selectOption('LC'); // Exercise the known-resistance model.
 assert(await page.locator('#identify').isDisabled());
 assert(await page.locator('#parasitic-error').isVisible());
 await page.locator('#inductor-resistance').fill('0.25');
 assert(await page.locator('#identify').isDisabled());
 await page.locator('#capacitor-esr').fill('0.12');
 assert.equal(await page.evaluate(()=>options().inductor_resistance_ohm),.25);
 assert.equal(await page.evaluate(()=>options().capacitor_esr_ohm),.12);
 await page.locator('#pwm-source').selectOption('controller');
 await page.locator('#edit-circuit').click();
 await page.locator('#circuit-drawing [data-parameter="inductor-resistance"]').click();
 assert.equal(await page.evaluate(()=>document.activeElement.id),'circuit-inductor-resistance');
 await page.locator('#circuit-inductor-resistance').fill('0.3');
 await page.locator('#circuit-capacitor-esr').fill('0.15');
 await page.screenshot({path:'runtime/parasitic-circuit-zh.png'});
 await page.locator('#save-circuit').click();
 assert.equal(await page.locator('#inductor-resistance').inputValue(),'0.3');
 assert.equal(await page.locator('#capacitor-esr').inputValue(),'0.15');
 await page.locator('#language-toggle').click();
 await page.locator('#edit-circuit').click();
 assert(!/[\u3400-\u9fff]/.test(await page.locator('#circuit-dialog').innerText()));
 await page.screenshot({path:'runtime/parasitic-circuit-en.png'});
 await page.locator('#close-circuit').click();
 await page.reload();await ready();
 await page.locator('#enter-identification').click();
 assert.equal(await page.locator('#include-parasitics').inputValue(),'true');
 assert.equal(await page.locator('#capacitor-esr').inputValue(),'0.15');
 await page.locator('#include-parasitics').selectOption('false');
 assert.equal(await page.evaluate(()=>options().capacitor_esr_ohm),null);
 assert((await page.locator('#circuit-preview').textContent()).includes('rC = 0'));
 await page.locator('#include-parasitics').selectOption('true');
 await page.locator('#fit-mode').selectOption('LC'); // Exercise the known-resistance model.
 await page.locator('#inductor-resistance').fill('0');await page.locator('#capacitor-esr').fill('0');
 assert.equal(await page.evaluate(()=>options().capacitor_esr_ohm),0);
 await page.locator('#pwm-source').selectOption('measured');
 await page.screenshot({path:'runtime/parasitic-settings.png'});
 await page.locator('#start-ms').fill('0');await page.locator('#end-ms').fill('0.8');
 const rejection=await page.evaluate(async()=>{
   try{await api('/api/identify',{method:'POST',body:{dataset_id:state.dataset.dataset_id,mapping:state.mapping,options:{...options(),capacitor_esr_ohm:null}}});return 200;}
   catch(error){return error.status;}
 });
 assert.equal(rejection,422,'Backend must reject missing ESR too');
 await page.locator('#identify').click();await ready();
 const result=await page.evaluate(()=>state.result);
 assert(result && result.options.include_parasitics);
 assert.equal(result.options.capacitor_esr_ohm,0);
 assert.equal(result.diagnostics.power_stage_model,'known_series_resistances');
 assert.deepEqual(errors,[]);
 console.log('Parasitics browser checks passed: required fields, diagram sync, bilingual layout, persistence, zero and disabled modes.');
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
