/* Isolated archive and mocked model responses; never touches the user's backup. */
const {spawn}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs');
const root=path.resolve(__dirname,'..');
fs.mkdirSync(path.join(root,'.tmp'),{recursive:true});
const temp=fs.mkdtempSync(path.join(root,'.tmp/sync-review-'));
const port=18083,url=`http://127.0.0.1:${port}`;
const env={...process.env,DEEPSEEK_API_KEY:'',APP_ACCESS_TOKEN:'',APP_ENV:'development',
  CORS_ORIGINS:url,ARCHIVE_DIR:temp,AGENT_RUNTIME_DIR:temp,TEST_BASE_URL:url};
const server=spawn(path.join(root,'.venv/Scripts/python.exe'),
  ['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',String(port)],
  {cwd:root,env,windowsHide:true,stdio:'ignore'});
(async()=>{
  try{
    let ready=false;
    for(let n=0;n<60;n++){
      if(server.exitCode!==null)throw Error('Isolated backend exited');
      try{if((await fetch(url+'/api/health')).ok){ready=true;break;}}catch{}
      await new Promise(r=>setTimeout(r,500));
    }
    if(!ready)throw Error('Isolated backend did not start');
    const files=process.env.TEST_FILES?.split(',') || ['browser-sync-review.cjs','browser-incremental-sync.cjs','browser-sync.cjs',
      'browser-background-sync.cjs','browser-sync-metadata.cjs','browser-sync-typing.cjs'];
    for(const file of files){
      console.log('RUN '+file);
      await new Promise((resolve,reject)=>{
        const child=spawn(process.execPath,[path.join(__dirname,file)],{cwd:root,env,windowsHide:true,stdio:'inherit'});
        const timeout=setTimeout(()=>{child.kill();reject(Error(file+' timed out'));},180000);
        child.on('error',e=>{clearTimeout(timeout);reject(e);});
        child.on('exit',code=>{clearTimeout(timeout);code===0?resolve():reject(Error(file+' failed'));});
      });
    }
  }finally{server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
