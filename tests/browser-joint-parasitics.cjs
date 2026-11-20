// Requires the local test backend. Generates an independent circuit fixture locally.
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict');
require('node:child_process').execFileSync('.venv/Scripts/python.exe',['-c',
 "import sys; sys.path.insert(0,'tests'); import pandas as pd; from pathlib import Path; from test_parasitics import circuit_data; Path('.tmp').mkdir(exist_ok=True); pd.DataFrame(circuit_data.__wrapped__(),columns=['time','current','voltage','pwm']).to_csv('.tmp/joint-browser.csv',index=False)"]);
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1500,height:1050}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(process.env.BUCK_TEST_URL||'http://127.0.0.1:8013/');
  await page.getByText('后端已连接',{exact:true}).waitFor();
  await page.locator('#language-toggle').click();
  await page.locator('#csv-file').setInputFiles('.tmp/joint-browser.csv');
  await page.waitForFunction(()=>state.dataset && !state.busy);
  await page.locator('#apply-mapping').click();
  await page.waitForFunction(()=>state.mapping && !state.busy);
  await page.locator('#enter-identification').click();
  await page.locator('#fit-method').selectOption('weighted_integral');
  await page.locator('#fit-mode').selectOption('LC_RL_RC');
  assert.equal(await page.locator('#include-parasitics').inputValue(),'true');
  assert(!(await page.locator('#include-parasitics option:checked').textContent()).includes('Known'));
  assert(await page.locator('#fixed-r-label').isVisible());
  assert(await page.locator('#identify').isEnabled());
  assert((await page.locator('#parasitic-hint').textContent()).includes('leave blank'));
  for(const values of [['',''],['0','0'],['0.25',''],['','0.12'],['0.25','0.12']]){
   await page.locator('#inductor-resistance').fill(values[0]);
   await page.locator('#capacitor-esr').fill(values[1]);
   const o=await page.evaluate(()=>options());
   assert.equal(o.inductor_resistance_ohm,values[0]===''?null:Number(values[0]));
   assert.equal(o.capacitor_esr_ohm,values[1]===''?null:Number(values[1]));
   assert(await page.locator('#identify').isEnabled());
  }
  await page.locator('#inductor-resistance').fill('-1');
  assert(await page.locator('#identify').isDisabled());
  await page.locator('#inductor-resistance').fill('0.25');
  await page.locator('#capacitor-esr').fill('');
  await page.locator('#identify').click();
  await page.locator('#job-status').filter({hasText:'Identification complete'}).waitFor({timeout:60000});
  assert.equal(await page.locator('#result-rl').textContent(),'0.25');
  assert(Math.abs(Number(await page.locator('#result-rc').textContent())-.12)<.005);
  assert((await page.locator('#result-rl-label').textContent()).endsWith('Fixed'));
  assert((await page.locator('#result-rc-label').textContent()).endsWith('Estimated'));
  assert(!/[\u3400-\u9fff]/.test(await page.locator('#results-card').innerText()));
  const summary=await page.evaluate(()=>chatExperiments()[0]);
  assert(summary.parameters.rC_ohm>0);
  await page.locator('#language-toggle').click();
  assert((await page.locator('#result-rl-label').textContent()).endsWith('固定'));
  await page.locator('#language-toggle').click();
  await page.locator('#pwm-source').selectOption('controller');
  assert.equal(await page.locator('#end-ms').inputValue(),'0.4','The suggested interval fits this recording');
  assert.equal(await page.locator('#fit-mode').inputValue(),'LC_RL_RC');
  await page.locator('#include-parasitics').selectOption('false');
  assert.equal(await page.locator('#fit-mode').inputValue(),'LC');
  await page.locator('#include-parasitics').selectOption('true');
  await page.locator('#fit-mode').selectOption('LC');
  assert(await page.locator('#identify').isDisabled(),'Known-resistance mode still requires both values');
  await page.locator('#fit-mode').selectOption('LC_RL_RC');
  await page.reload();
  await page.getByText('Backend connected',{exact:true}).waitFor();
  await page.waitForFunction(()=>archiveLastSuccess && !archiveRunning && !historyLocked() && state.mapping);
  assert.equal(await page.locator('#fit-mode').inputValue(),'LC_RL_RC');
  assert.equal(await page.locator('#capacitor-esr').inputValue(),'');
  await page.locator('#enter-identification').click();
  await page.locator('#fit-mode').selectOption('FULL_STAGE');
  assert.equal(await page.locator('#fit-method').inputValue(),'circuit_fit');
  assert.equal(await page.locator('#pwm-source').inputValue(),'measured');
  const reference=page.locator('#circuit-preview img');
  await reference.evaluate(img=>img.decode());
  assert((await reference.getAttribute('src')).includes('buck-with-parasitics.png'));
  for(const [id,value] of Object.entries({'stage-l':'250','inductor-resistance':'0.25','stage-cw':'0','stage-c':'70','capacitor-esr':'0.12','stage-esl':'0','stage-ron':'0'})){
   await page.locator('#'+id).fill(value);
  }
  await page.locator('#identify').click();
  await page.waitForFunction(()=>state.result?.method==='circuit_fit' && !historyLocked(),null,{timeout:60000});
  assert.equal(await page.locator('#full-stage-results > div').count(),8);
  assert.equal(await page.evaluate(()=>state.result.parameters.L_H),250e-6);
  await page.locator('#map-current').dispatchEvent('change');
  assert(await page.locator('#full-stage-results').isHidden());
  assert.equal(await page.locator('#full-stage-results').textContent(),'');
  assert.deepEqual(errors,[]);
  console.log('Joint mode: per-resistance rules, real fit, result labels, history, bilingual UI and restoration passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
