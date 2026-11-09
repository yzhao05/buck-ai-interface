// Local UI + real search/upload/analyze APIs; only the upstream AI is scripted.
const assert=require('node:assert/strict');
const {chromium}=require('../.tmp/browser/node_modules/playwright-core');
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1650,height:850}});
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(process.env.TEST_BASE_URL);
    const ready=()=>page.waitForFunction(()=>state.connected&&activeConversation&&!historyLocked()&&!archiveRunning);
    await ready();
    await page.locator('#open-settings').click();
    await page.locator('#plecs-model-directory').fill(process.env.TEST_MODEL_DIR);
    await page.locator('#settings-form button[type=submit]').click();
    await page.waitForFunction(()=>!document.querySelector('#settings-dialog').open,null,{timeout:8000}).catch(async error=>{
      throw new Error((await page.locator('#settings-status').innerText()) || error.message);
    });
    await ready();
    // The unopened results pane must not make the document follow the long sidebar.
    await page.evaluate(()=>{
      const filler=document.createElement('div');filler.style.height='1800px';
      document.querySelector('.sidebar').append(filler);
      document.querySelector('.sidebar').scrollTop=180;
    });
    assert(await page.evaluate(()=>document.querySelector('.sidebar').scrollTop>0 && document.documentElement.scrollHeight<=innerHeight+1));
    await page.locator('#chat-input').fill('加载实验示例case16a noise03 seed14');
    await page.locator('#send-chat').click();
    await ready();
    const dataset=await page.evaluate(()=>({name:state.dataset?.filename,rows:state.dataset?.rows,mapping:state.mapping,summary:!!state.summary,
      job:state.job, result:state.result, reply:activeConversation.messages.at(-1)?.content}));
    assert.equal(dataset.name,'CASE16A_noise03_seed14.csv',dataset.reply);
    assert(dataset.rows>30 && dataset.mapping.time && dataset.summary && !dataset.job && !dataset.result);
    const outcome=async()=>assert.equal((await page.locator('#agent-preview').innerText()).trim(),
      (await page.locator('.message.assistant .message-content').last().innerText()).trim());
    await outcome();
    await page.route('**/api/local-csv/file',route=>route.fulfill({status:422,json:{detail:'CSV changed; search again'}}));
    await page.locator('#chat-input').fill('重新加载实验示例case16a noise03 seed14');
    await page.locator('#send-chat').click();
    await ready();
    await outcome();
    assert.match(await page.locator('#agent-preview').innerText(),/未能应用配置操作/);
    assert.equal(await page.locator('#agent-status').textContent(),'界面操作失败');
    await page.evaluate(()=>BuckHarness.recover());
    await outcome();
    await page.unroute('**/api/local-csv/file');
    const retryRequest=page.waitForRequest(req=>req.url().endsWith('/api/agent/tasks') && req.method()==='POST');
    await page.locator('#chat-input').fill('再次从本地加载这份 CSV');
    await page.locator('#send-chat').click();
    const prior=(await retryRequest).postDataJSON().ui_context.previous_local_csv_action;
    assert.match(prior.browser_report,/未能应用配置操作/);
    assert.equal(prior.filename,dataset.name);
    await ready();
    await outcome();
    assert.equal(await page.locator('#agent-status').textContent(),'界面处理结果');
    const displayed=await page.evaluate(()=>state.dataset.dataset_id);
    await page.route('**/api/analyze',route=>route.fulfill({status:422,json:{detail:'test mapping failure'}}));
    for(let attempt=0;attempt<2;attempt++){
      await page.locator('#chat-input').fill('再次导入并核对列映射');
      await page.locator('#send-chat').click();
      await ready();
      assert.match(await page.locator('#agent-preview').innerText(),/test mapping failure/);
      assert(await page.evaluate(async id=>state.dataset.dataset_id===id && (await api('/api/datasets/'+id)).dataset_id===id,displayed));
    }
    await page.unroute('**/api/analyze');
    for(const width of [1650,1000]){
      await page.setViewportSize({width,height:850});
      const independent=await page.evaluate(()=>{
        const sidebar=document.querySelector('.sidebar'),chat=document.querySelector('#chat-messages'),result=document.querySelector('#analysis-panel');
        for(const region of [chat,result]){
          const filler=document.createElement('div');filler.style.cssText='height:1800px;min-height:1800px';region.append(filler);
        }
        for(const region of [sidebar,chat,result])region.style.scrollBehavior='auto';
        sidebar.scrollTop=120;chat.scrollTop=140;result.scrollTop=160;
        return [sidebar.scrollTop,chat.scrollTop,result.scrollTop,window.scrollY,document.documentElement.scrollHeight-innerHeight];
      });
      assert.deepEqual(independent,[120,140,160,0,0],String(width));
    }
    assert.deepEqual(errors,[]);
    console.log('PASS: saved folder -> Agent search -> CSV imported and mapped, no fit; independent pane scroll at both widths;',dataset.rows,'rows');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
