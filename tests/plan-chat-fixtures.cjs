// Explicit fixtures for UI tests. Backend tests verify the DeepSeek tool call.
const assert=require('node:assert/strict');
const fixtures=new Map([
 ['请根据我上传的CASE16A_noise03_seed14.csv完成重建pwm下两种方式的参数识别',
  {filename:'CASE16A_noise03_seed14.csv',changes:{pwm_source:'controller',mode:'LC'}}],
 ['固定 R=7，运行两种方法',{filename:null,changes:{mode:'LC',resistance_ohm:7}}],
 ['固定 R=6，运行两种方法',{filename:null,changes:{mode:'LC',resistance_ohm:6}}],
 ['请使用 ambiguous.csv 完成两种方式的参数识别',{filename:'ambiguous.csv',changes:{}}],
]);
module.exports=async function installPlanChatFixtures(page){
 if(process.env.TEST_LIVE_DEEPSEEK==='1'){page.setDefaultTimeout(90000);return;}
 await page.route('**/api/chat',route=>{
  const body=route.request().postDataJSON(),fixture=fixtures.get(body.message);
  assert(fixture,`Missing plan fixture for: ${body.message}`);
  return route.fulfill({json:{mode:'deepseek',answer:'计划已解析。',job_id:null,sources:[],tool_calls:[],
   route:{task:'configuration',label:'辨识配置',mode:'rules',secondary_tasks:[]},
   ui_action:{id:body.ui_context.request_id,type:'identification_plan',parser:'deepseek',version:2,
    methods:['weighted_integral','pann'],...fixture}}});
 });
};
