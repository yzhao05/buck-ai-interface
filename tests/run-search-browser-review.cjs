/* Isolated local backend; tests mock model responses and never need an API key. */
const {spawn, execFileSync} = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const root = path.resolve(__dirname, '..');
fs.mkdirSync(path.join(root, '.tmp'), {recursive:true});
const temp = fs.mkdtempSync(path.join(root, '.tmp/search-review-'));
const port = 18081;
const url = `http://127.0.0.1:${port}`;
const env = {...process.env, DEEPSEEK_API_KEY:'', APP_ACCESS_TOKEN:'', APP_ENV:'development',
  CORS_ORIGINS:url, AGENT_RUNTIME_DIR:temp, BUCK_TEST_URL:url};
const server = spawn(path.join(root, '.venv/Scripts/python.exe'),
  ['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',String(port)],
  {cwd:root, env, windowsHide:true, stdio:'ignore'});
async function run(file) {
  await new Promise((resolve,reject)=>{
    const child=spawn(process.execPath,[path.join(__dirname,file)],{cwd:root,env,windowsHide:true,stdio:'inherit'});
    child.on('error',reject);
    child.on('exit',code=>code===0?resolve():reject(Error(`${file} exited ${code}`)));
  });
}
(async()=>{
  try {
    let ready=false;
    for(let i=0;i<60;i++){
      if(server.exitCode!==null)throw Error('Review backend exited before readiness');
      try{if((await fetch(url+'/api/health')).ok){ready=true;break;}}catch{}
      await new Promise(r=>setTimeout(r,500));
    }
    if(!ready)throw Error('Review backend did not become ready');
    for(const file of ['browser-analysis-history.cjs','browser-current-analysis.cjs','browser-agent-knowledge.cjs'])await run(file);
  } finally {
    if(server.pid)try{execFileSync('taskkill',['/PID',String(server.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}catch{}
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
