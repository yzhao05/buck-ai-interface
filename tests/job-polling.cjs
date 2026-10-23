// Exercise real watchJob control flow with immediate mocked network responses.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('frontend/app.js','utf8');
const watch=source.slice(source.indexOf('async function watchJob('),source.indexOf('\nfunction resultReviewReasons('));
async function run(finalStatus) {
 let polls=0, pauses=0, displayed=0, message='';
 const state={job:null};
 const sandbox={state, Date, persistConversation(){}, controls(){}, $(){return {};},ui(){},
  resultMatchesCurrent(){return true;},displayResult(){displayed++;},invalidateResult(){},notice(text){message=text;},
  pause:async()=>{pauses++;},api:async()=>++polls<=1001?{status:'running',stage:'pann_training'}:
    {status:finalStatus,error:'test failure',result:{elapsed_s:7200}}};
 vm.createContext(sandbox);vm.runInContext(watch,sandbox);
 await sandbox.watchJob('long-job');
 assert.equal(polls,1002);assert.equal(pauses,1001);assert.equal(state.job,null);
 if(finalStatus==='complete'){assert.equal(displayed,1);assert.equal(state.result.elapsed_s,7200);assert.equal(message,'');}
 else {assert.equal(displayed,0);assert.equal(message,'test failure');}
}
(async()=>{await run('complete');await run('failed');console.log('PASS: polling beyond 600 attempts until completion or failure, with state cleanup.');})().catch(e=>{console.error(e);process.exitCode=1;});
