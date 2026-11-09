// Browser regression: credential persistence and isolation; no live API or secrets.
const {chromium} = require('../.tmp/browser/node_modules/playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const site = 'http://127.0.0.1:5501';
const first = 'https://first.example.test';
const second = 'https://second.example.test';
const credential = 'test-only-access-token-123456789';

(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const requests = [], errors = [], streamTokens = [];
  async function createContext(storageState) {
    const context = await browser.newContext({storageState});
    context.on('page',page => page.on('pageerror',e => errors.push(e.message)));
    await context.route('**/*',async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.pathname === '/api/health') {
        assert(!req.headers().authorization && !req.headers()['x-buck-authorization']);
        await route.fulfill({json:{status:'ok',chat_mode:'local',requires_token:true,pann_available:false,
          app_auth_header:url.origin===first?'X-Buck-Authorization':'Authorization'}});
      } else if (url.pathname==='/api/agent/tasks' && req.method()==='POST') {
        await route.fulfill({json:{id:'auth-check'}});
      } else if (url.pathname==='/api/agent/tasks/auth-check/events') {
        streamTokens.push(req.headers()['x-buck-authorization']);
        await route.fulfill({contentType:'application/x-ndjson',body:JSON.stringify({kind:'end',data:{state:'completed',result:{answer:'ok'}}})+'\n'});
      } else if(url.pathname === '/api/probe'){
        const header=url.origin===first?'x-buck-authorization':'authorization';
        requests.push({origin:url.origin,token:req.headers()[header] || ''});
        await route.fulfill({json:{}});
      } else if(url.pathname.startsWith('/api/')){
        await route.fulfill({json:{}});
      } else if(url.origin === site) {
        const name = url.pathname.endsWith('/') ? 'index.html' : url.pathname.replace(/^\/(another-project\/)?/, '');
        await route.fulfill({path:path.join(__dirname,'../frontend',name)});
      } else await route.abort();
    });
    return context;
  }
  async function ready(page,url=site+'/') {
    await page.goto(url);
    await page.getByText('后端已连接',{exact:true}).waitFor();
    await page.evaluate(()=>api('/api/probe'));
  }
  async function openSettings(page) { await page.locator('#open-settings').click(); }
  async function save(page) {
    await page.locator('#settings-form button[type=submit]').click();
    await page.locator('#settings-dialog').waitFor({state:'hidden'});
    await page.evaluate(()=>api('/api/probe'));
  }
  try {
    let context = await createContext(), page = await context.newPage();
    await ready(page);
    await openSettings(page);
    await page.locator('#api-url').fill(first);
    await page.locator('#access-token').fill(credential);
    await save(page);
    assert.equal(requests.at(-1).token,'Bearer '+credential);
    await page.evaluate(()=>BuckHarness.request({message:'check'}));
    assert.deepEqual(streamTokens,['Bearer '+credential]);
    await ready(page);
    assert.equal(requests.at(-1).token,'Bearer '+credential,'Reload restores the credential');
    const persisted = await context.storageState();
    await context.close();
    context = await createContext(persisted);
    page = await context.newPage();
    await ready(page);
    assert.equal(requests.at(-1).token,'Bearer '+credential,'A fresh browser session restores local storage');
    await openSettings(page);
    assert.equal(await page.locator('#access-token').inputValue(),credential);
    await page.locator('#api-url').fill(second);
    assert.equal(await page.locator('#access-token').inputValue(),'','Switching backend clears autofill');
    await save(page);
    assert.deepEqual(requests.at(-1),{origin:second,token:''});
    await openSettings(page);
    await page.locator('#api-url').fill(first);
    assert.equal(await page.locator('#access-token').inputValue(),credential);
    await save(page);
    await ready(page,site+'/another-project/');
    assert.equal(requests.at(-1).token,'','Other project paths do not inherit credentials');
    await ready(page);
    await openSettings(page);
    await page.locator('#forget-access-token').click();
    assert.equal(await page.locator('#access-token').inputValue(),'');
    await ready(page);
    assert.equal(requests.at(-1).token,'','Forgotten credential stays cleared after reload');
    await openSettings(page);
    await page.locator('#access-token').fill(credential);
    await save(page);
    await openSettings(page);
    await page.locator('#access-token').fill('');
    await save(page);
    await ready(page);
    assert.equal(requests.at(-1).token,'','Saving an empty token removes its remembered value');
    await page.route(first+'/api/health',route=>route.fulfill({contentType:'text/html',body:'<p>Studio project page</p>'}));
    assert.equal(await page.evaluate(()=>connect()),false);
    assert.match(await page.locator('#notice-text').innerText(),/实际运行应用/);
    assert.deepEqual(errors,[]);
    const result = {passed:true,reload:true,newSession:true,backendIsolation:true,projectIsolation:true,forget:true,emptyClears:true};
    await fs.mkdir(path.join(__dirname,'../runtime'),{recursive:true});
    await fs.writeFile(path.join(__dirname,'../runtime/access-token-verification.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exit(1);});
