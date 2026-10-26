// Real local backend/PyTorch; verifies UI settings reach the fitter and survive history.
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  try {
    const context=await browser.newContext({viewport:{width:1500,height:1050}});
    const page=await context.newPage(), errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto('http://127.0.0.1:8001');
    const ready=()=>page.locator('#send-chat:not([disabled])').waitFor({timeout:180000});
    await ready(); await page.locator('#load-demo').click(); await ready();
    await page.locator('#enter-identification').click();
    assert.equal(await page.locator('#fit-method option').count(),2);
    assert.equal(await page.locator('#fit-method').inputValue(),'pann');
    await page.locator('#fit-method').selectOption('weighted_integral');
    assert(await page.locator('#pann-settings').isHidden());
    await page.locator('#identify').click(); await ready();
    const integralResult=JSON.parse(await page.locator('#diagnostic-text').textContent());
    assert.equal(integralResult.conditions.method,'weighted_integral');
    assert.equal(integralResult.method.pann,undefined);
    await page.locator('#fit-method').selectOption('pann');
    assert(await page.locator('#download-report').isEnabled(),'Draft method change keeps the completed result available');
    assert(await page.locator('#pann-settings').isVisible());
    assert.equal(await page.locator('#pann-state').inputValue(),'joint');
    assert.equal(await page.locator('#pann-cycles').inputValue(),'0');
    assert.equal(await page.locator('#pann-epochs').inputValue(),'100');
    await page.locator('#fit-mode').selectOption('LC');
    await page.locator('#pann-cycles').selectOption('2');
    await page.locator('#pann-epochs').fill('10');
    await page.locator('#language-toggle').click();
    assert.equal(await page.locator('#pann-state option:checked').textContent(),'Joint estimation from noisy data (recommended)');
    await page.reload(); await ready();
    await page.locator('#enter-identification').click();
    assert.equal(await page.locator('#fit-method').inputValue(),'pann');
    assert.equal(await page.locator('#pann-cycles').inputValue(),'2');
    assert.equal(await page.locator('#pann-epochs').inputValue(),'10');
    await page.locator('#identify').click();
    await ready();
    assert((await page.locator('#job-status').textContent()).startsWith('Identification complete'));
    const diagnostics=JSON.parse(await page.locator('#diagnostic-text').textContent());
    assert.equal(diagnostics.method.pann.initial_state_mode,'joint');
    assert.equal(diagnostics.method.pann.window_cycles,2);
    assert.equal(diagnostics.method.pann.completed_epochs,10);
    assert.equal(diagnostics.method.pann.state_warmup_epochs,50);
    assert.equal(diagnostics.method.pann.estimated_state_scalars,56);
    assert.equal(diagnostics.conditions.mode,'LC');
    assert.equal(await page.locator('#result-r').textContent(),'6');
    await page.locator('#pann-state').selectOption('measured');
    assert(await page.locator('#download-report').isEnabled(),'Changing draft states preserves the completed result');
    const first=await page.locator('.history-item[aria-current=true]').getAttribute('data-conversation-id');
    await page.locator('#new-chat').click(); await ready();
    await page.locator(`.history-item[data-conversation-id="${first}"]`).click(); await ready();
    await page.locator('#enter-identification').click();
    assert.equal(await page.locator('#pann-state').inputValue(),'measured');
    await page.locator('#fit-method').selectOption('weighted_integral');
    await page.reload(); await ready();
    await page.locator('#enter-identification').click();
    assert.equal(await page.locator('#fit-method').inputValue(),'weighted_integral');
    assert(await page.locator('#pann-settings').isHidden());
    // Simulate a backend without torch: the standalone method still runs.
    await page.route('**/api/health',async route=>{
      const response=await route.fetch(), body=await response.json();
      body.pann_available=false; await route.fulfill({json:body});
    });
    await page.reload(); await ready();
    await page.locator('#enter-identification').click();
    assert(await page.locator('#fit-method option[value=pann]').evaluate(el=>el.disabled),
      await page.locator('#fit-method').evaluate(el=>el.outerHTML));
    await page.locator('#identify').click(); await ready();
    assert.equal(JSON.parse(await page.locator('#diagnostic-text').textContent()).conditions.method,'weighted_integral');
    // A legacy result remains its original method, separate from the migrated draft.
    await page.evaluate(()=>{
      for(const key of Object.keys(localStorage)){
        if(!key.startsWith('buck-history:'))continue;
        try{
          const record=JSON.parse(localStorage.getItem(key));
          if(record?.fields){record.fields['fit-method']='integral';localStorage.setItem(key,JSON.stringify(record));}
        }catch{}
      }
    });
    await context.route('**/api/jobs/*',async route=>{
      const response=await route.fetch(),body=await response.json();
      if(body.result){body.result.options.method='integral';body.result.method='integral';} await route.fulfill({json:body});
    });
    // Read the edited storage from a fresh tab, avoiding the old tab's save-on-unload.
    const restored=await context.newPage(); await restored.goto('http://127.0.0.1:8001');
    await restored.locator('#send-chat:not([disabled])').waitFor();
    assert.equal(await restored.locator('#fit-method').inputValue(),'weighted_integral');
    assert(await restored.locator('#download-report').isEnabled());
    assert.equal(await restored.evaluate(()=>state.result.options.method),'integral');
    assert.equal(await restored.evaluate(()=>resultMatchesCurrent(state.result)),false);
    await restored.close();
    for(const width of [1500,390]){
      await page.setViewportSize({width,height:1050});
      assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)));
    }
    await page.setViewportSize({width:1500,height:1050});
    await page.screenshot({path:'runtime/pann-settings.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('Both methods, real weighted/PANN fits, no-torch fallback, bilingual controls and legacy history passed');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
