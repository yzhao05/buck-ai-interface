/* Adapt existing domain UI fixtures to the task/event transport, test only. */
const installed=new WeakSet();
exports.waitForResponse=page=>page.waitForResponse(r=>/\/api\/agent\/tasks\/[^/]+\/events(?:\?|$)/.test(r.url()),{timeout:330000}).then(response=>({
  json:async()=>{
    const events=(await response.text()).trim().split('\n').map(line=>JSON.parse(line));
    return events.findLast(event=>event.kind==='end')?.data.result || events.findLast(event=>event.kind==='result')?.data;
  }
}));
exports.install=async page=>{
  if(installed.has(page))return;
  installed.add(page);
  const results=new Map();let sequence=0;
  await page.route('**/api/agent/tasks',async route=>{
    const body=route.request().postDataJSON(),headers=route.request().headers();
    const result=await page.evaluate(async({body,headers})=>{
      const response=await fetch(base.replace(/\/$/,'')+'/api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-Session-ID':headers['x-session-id'],...(headers.authorization?{Authorization:headers.authorization}:{})},body:JSON.stringify(body)});
      return response.json();
    },{body,headers});
    const id=`fixture-${++sequence}`;results.set(id,result);
    await route.fulfill({json:{id,state:'running'}});
  });
  await page.route('**/api/agent/tasks/*/events',async route=>{
    const id=route.request().url().split('/').at(-2),result=results.get(id);
    await route.fulfill({contentType:'application/x-ndjson',body:JSON.stringify({seq:1,kind:'result',data:result})+'\n'+JSON.stringify({kind:'end',data:{state:'completed',result}})+'\n'});
  });
};
