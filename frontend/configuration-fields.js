/* Chat uses the same form controls and validation limits as manual edits. */
"use strict";
const configurationFields={
  method:['fit-method',1,'计算方法'],mode:['fit-mode',1,'辨识目标'],resistance_ohm:['fixed-r',1,'负载电阻'],
  vin:['vin',1,'输入电压'],frequency_hz:['frequency',.001,'开关频率 (kHz)'],train_fraction:['train-fraction',100,'训练区间占比'],
  epochs:['pann-epochs',1,'训练轮数'],pann_initial_state:['pann-state',1,'窗口初值'],pann_window_cycles:['pann-cycles',1,'窗口长度'],
  start_s:['start-ms',1000,'开始时间 (ms)'],end_s:['end-ms',1000,'结束时间 (ms)'],
  pwm_source:['pwm-source',1,'PWM 来源'],pwm_denoise:['pwm-denoise',1,'重建电压去噪'],pwm_filter_cycles:['pwm-filter-cycles',1,'滤波周期数']
};
const mappingFields={time:'map-time',current:'map-current',voltage:'map-voltage',pwm:'map-pwm',time_unit:'time-unit',current_unit:'current-unit',voltage_unit:'voltage-unit'};
let pendingConfigurationStart=null;
function executionSelectionQuestion(selection,methods,sources,{allowAuto=false}={}){
  const missing=[];
  if(!selection?.method_evidence?.trim() || !Array.isArray(selection.methods) || !methods.every(value=>selection.methods.includes(value)))
    missing.push(tr('计算方法：抗噪加权积分、PANN，还是两种都运行？'));
  if(!selection?.pwm_evidence?.trim() || !Array.isArray(selection.pwm_sources) || !(sources.every(value=>selection.pwm_sources.includes(value)) || (allowAuto&&selection.pwm_sources.includes('auto'))))
    missing.push(tr('PWM 来源：CSV 实测 PWM、控制器重建，还是明确采用自动判断？'));
  return missing.length?tr('开始前请先选择：')+'\n\n'+missing.map(item=>'- '+item).join('\n')+'\n\n'+tr('尚未启动辨识；请回复具体选项后继续。'):'';
}
function identificationReviewFeedback(){
  // Read the applied UI state, not the model's pre-application assumptions.
  const selected=id=>$(id).selectedOptions[0]?.textContent||$(id).value;
  const m=state.mapping;
  const mapping=!state.dataset?tr('请先导入 CSV，再核对列与单位并点击“应用并检查数据”。')
    :!m?tr('列映射尚未应用，请核对时间、电流、电压列及单位，然后点击“应用并检查数据”。')
    :tr('列映射已应用，请核对是否对应本次数据：')+[
      `${tr('时间')} = ${m.time} (${m.time_unit})`,`${tr('电流')} = ${m.current} (${m.current_unit})`,
      `${tr('电压')} = ${m.voltage} (${m.voltage_unit})`,`PWM = ${m.pwm||tr('未映射')}`
    ].join('；')+'。'+tr('如需更改，请修改后重新点击“应用并检查数据”；无误则无需重复应用。');
  const settings=[`${tr('辨识目标')} = ${selected('fit-mode')}`,`${tr('计算方法')} = ${selected('fit-method')}`,
    `Vin = ${$('vin').value} V`,`${tr('开关频率 (kHz)')} = ${$('frequency').value}`,
    `${tr('训练区间占比')} = ${$('train-fraction').value}%`,`${tr('PWM 来源')} = ${tr(pwmSource()==='controller'?'控制器重建':'实测 PWM')}`,
    `${tr('开始时间 (ms)')} = ${$('start-ms').value||tr('自动')}`,`${tr('结束时间 (ms)')} = ${$('end-ms').value||tr('自动')}`];
  if($('fit-mode').value==='LC')settings.push(`${tr('负载电阻')} = ${$('fixed-r').value} Ω`);
  if($('fit-method').value==='pann')settings.push(`${tr('训练轮数')} = ${$('pann-epochs').value}`);
  let requirement=tr('请核对这些设置是否与本次数据及实验工况一致。');
  if(pwmSource()==='controller' && $('controller-confirmed').value!=='yes')requirement+=' '+tr('控制器接线与参数尚未确认，请核对后在基本信息中确认电路配置。');
  if(state.dataset&&m&&pwmSource()==='measured'&&!m.pwm)requirement+=' '+tr('当前未映射实际 PWM 列，请补齐，或选择并确认控制器重建。');
  return '\n\n'+tr('开始前请核对以下两项：')+'\n\n1. **'+tr('列与单位映射')+'**：'+mapping+
    '\n\n2. **'+tr('实验设置')+'**：'+settings.join('；')+'。'+requirement+
    '\n\n'+tr('核对完成且数据就绪后，点击“开始参数辨识”，或明确回复“开始参数辨识”。本次不会自动启动。');
}
const convertedField=(value,scale)=>value===null?'':typeof value==='number'?String(Number((value*scale).toPrecision(15))):String(value);
function validConfigurationChanges(c){
  if(!c || Array.isArray(c) || typeof c!=='object')return false;
  for(const [key,value] of Object.entries(c)){
    if(key==='controller_confirmed'){if(typeof value!=='boolean')return false;continue;}
    if(key==='controller'){
      if(!value || typeof value!=='object' || Array.isArray(value))return false;
      const merged={...BuckCircuit.read(),...value};
      for(const [k,v] of Object.entries(value)){
        const spec=BuckCircuit.specs.find(s=>s[0]===k);
        if(!spec || typeof v!=='number'||!Number.isFinite(v)||v<spec[5]*spec[3]||v>spec[6]*spec[3])return false;
      }
      if(merged.duty_min>=merged.duty_max)return false;
      continue;
    }
    const spec=configurationFields[key];if(!spec)return false;
    const input=$(spec[0]),val=convertedField(value,spec[1]);
    if(input.tagName==='SELECT'){
      if(![...input.options].some(o=>o.value===val))return false;
    }else if(value===null){if(!['start_s','end_s'].includes(key))return false;}
    else {
      if(typeof value!=='number'||!Number.isFinite(value))return false;
      const probe=input.cloneNode();probe.disabled=false;probe.value=val;
      if(probe.value!==val||!probe.checkValidity())return false;
      if(key==='resistance_ohm'&&value<=.01)return false;
    }
  }
  return validTuningInterval(c);
}
function applyConfigurationChanges(c,{resetConfirmation=false}={}){
  const labels=[];
  const circuitChanged=['vin','frequency_hz','resistance_ohm'].some(k=>k in c&&Number($(configurationFields[k][0]).value)!==c[k]*configurationFields[k][1])
    || (c.controller&&Object.entries(c.controller).some(([k,v])=>BuckCircuit.read()[k]!==v));
  if(resetConfirmation || circuitChanged)$('controller-confirmed').value='no';
  for(const [key,value] of Object.entries(c)){
    if(key==='controller'){
      $('controller-parameters').value=JSON.stringify({...BuckCircuit.read(),...value});
      labels.push(...Object.entries(value).map(([k,v])=>`${k} = ${v}`));
    }else if(key==='controller_confirmed'){$('controller-confirmed').value=value?'yes':'no';labels.push(tr(value?'已确认电路配置':'尚未确认电路配置'));}
    else {const [id,scale,label]=configurationFields[key];$(id).value=convertedField(value,scale);labels.push(`${tr(label)} = ${$(id).value||tr('自动')}`);}
  }
  // An explicit confirmation refers to all changes in this command, independent of key order.
  if('controller_confirmed' in c)$('controller-confirmed').value=c.controller_confirmed?'yes':'no';
  ui($('train-label'),$('train-fraction').value+'%');
  if(('pwm_denoise' in c||'pwm_filter_cycles' in c)&&pwmSource()!=='controller')labels.push(tr('滤波设置已保存，仅在控制器 PWM 重建时使用'));
  if('epochs' in c&&$('fit-method').value!=='pann')labels.push(tr('训练轮数已保存，仅在 PANN 方法中使用'));
  return labels;
}
async function configurationCatalog({includeRemote=false}={}){
  const entries=new Map(), add=(item)=>{if(entries.size<300&&!entries.has(item.id))entries.set(item.id,item);};
  if(includeRemote){
    const remote=await historicalCsvEntries();
    entries.incomplete=remote.incomplete;
    for(const entry of remote.entries)add({id:'csv:'+entry.sha256,kind:'csv',name:entry.filename,entry});
  }
  const records=[...conversationCache.values()].filter(r=>!savedValue(conversationPrefix+'deleted:'+r.id));
  for(const record of records){
    const files=await BuckAttachments.list(conversationPrefix+record.id).catch(()=>[]);
    for(const file of [...files,record.attachment?.dataset].filter(Boolean)){
      if(!/^[a-f0-9]{64}$/.test(file.sha256)||typeof file.filename!=='string'||(file.epoch??record.file_epoch??0)!==(record.file_epoch||0))continue;
      add({id:'csv:'+file.sha256,kind:'csv',name:file.filename,entry:{conversationId:record.id,epoch:record.file_epoch||0,prefix:conversationPrefix,sha256:file.sha256,filename:file.filename}});
    }
    let library=[];try{library=JSON.parse(record.fields?.['controller-image-library']||'[]');}catch{}
    const images=[...(Array.isArray(library)?library:[]),{name:record.fields?.['controller-image-name']||'circuit.png',data_url:record.fields?.['controller-image']},
      ...(record.messages||[]).filter(m=>m.role==='user').flatMap(m=>m.images||[])];
    for(const image of images.filter(validChatImage)){
      const hash=await csvDigest(new Blob([image.data_url]));
      add({id:'circuit:'+hash,kind:'circuit',name:image.name,image,conversationId:record.id});
    }
  }
  for(const run of readConversation(activeConversation.id)?.experiments||[])add({id:run.id,kind:'result',name:experimentReportName(run)});
  return entries;
}
function publicConfigurationCatalog(catalog){return [...catalog.values()].map(({id,kind,name})=>({id,kind,name}));}
function validConfigurationExtras(action){
  const allowed={assets:['csv_id','circuit_id','result_id'],mapping:Object.keys(mappingFields),analysis:['truth_L_H','truth_C_F','reference_csv_id','reference_confirmed','calculate_error']};
  for(const [section,keys] of Object.entries(allowed)){
    const values=action[section]||{};
    if(typeof values!=='object'||Array.isArray(values)||Object.keys(values).some(k=>!keys.includes(k)))return false;
    for(const [key,value] of Object.entries(values)){
      if(['reference_confirmed','calculate_error'].includes(key)){if(typeof value!=='boolean')return false;}
      else if(key.startsWith('truth_')){if(value!==null&&!(typeof value==='number'&&Number.isFinite(value)&&value>0&&value<=1e30))return false;}
      else if(value!==null&&typeof value!=='string')return false;
      else if(value===null&&!['circuit_id','reference_csv_id','pwm'].includes(key))return false;
    }
  }
  return true;
}
async function prepareConfigurationExtras(action,context){
  if(!validConfigurationExtras(action))throw new Error('配置操作格式无效。');
  const assets=action.assets||{}, analysis=action.analysis||{}, patch=action.mapping||{};
  if(assets.result_id&&(assets.csv_id||Object.keys(patch).length||action.start_after_apply))throw new Error('查看历史结果与更换输入或开始新辨识请分别发送指令。');
  const find=(id,kind)=>{const item=context.catalog?.get(id);if(!item||item.kind!==kind)throw new Error('文件已失效或未上传，请重新选择文件。');return item;};
  const staged={};
  if(assets.csv_id){
    const item=find(assets.csv_id,'csv');
    staged.file=await historicalCsvFile(item.entry);
    const form=new FormData();form.append('file',staged.file);
    staged.dataset=await api('/api/datasets',{method:'POST',body:form});
    if(staged.dataset.sha256!==item.entry.sha256)throw new Error('文件内容已变化，请重新选择。');
  }
  if('circuit_id' in assets){
    const item=assets.circuit_id===null?null:find(assets.circuit_id,'circuit');
    if(item&&(!readConversation(item.conversationId)||savedValue(conversationPrefix+'deleted:'+item.conversationId)))throw new Error('电路图所属会话已删除，请重新选择。');
    staged.image=item?.image||null;
    staged.imageChanged=(staged.image?.data_url||'')!==$('controller-image').value;
    if(staged.image&&!validChatImage(staged.image))throw new Error('电路图已失效，请重新上传。');
  }
  if(assets.result_id){find(assets.result_id,'result');if(!readConversation(activeConversation.id)?.experiments?.some(r=>r.id===assets.result_id))throw new Error('结果已失效。');}
  if(staged.dataset||Object.keys(patch).length){
    const data=staged.dataset||state.dataset;if(!data)throw new Error('请先选择 CSV。');
    const previous=staged.dataset&&!state.dataset?{...mappingFromForm(),...data.suggested_mapping}:mappingFromForm();
    // Reuse named columns only when they exist in the selected CSV; otherwise require a mapping.
    staged.mapping={...previous,...patch};
    staged.mappingChanged=Object.entries(staged.mapping).some(([key,value])=>state.mapping?.[key]!==value);
    for(const key of ['time','current','voltage','pwm'])if(staged.mapping[key]!==null&&!data.columns.includes(staged.mapping[key]))throw new Error('目标 CSV 不包含指定的 '+key+' 列，请明确列映射后重试。');
    for(const [key,values] of Object.entries({time_unit:['s','ms','us','ns'],current_unit:['A','mA'],voltage_unit:['V','mV']}))if(!values.includes(staged.mapping[key]))throw new Error('列映射单位无效。');
    staged.summary=await api('/api/analyze',{method:'POST',body:{dataset_id:data.dataset_id,mapping:staged.mapping}});
  }
  if(Object.keys(analysis).length){
    if(!state.result&&!assets.result_id)throw new Error('请先选择或完成一次辨识，再设置真值或诊断参考。');
    if(staged.dataset||staged.mapping)throw new Error('更换数据/列映射后请先完成辨识，再设置真值或诊断参考。');
    if(analysis.reference_csv_id){
      const item=find(analysis.reference_csv_id,'csv'),file=await historicalCsvFile(item.entry),form=new FormData();form.append('file',file);
      staged.reference=await api('/api/datasets?reference=true',{method:'POST',body:form});
    }
    const existingRef=selectedDiagnosticReference();
    const referenceAvailable='reference_csv_id' in analysis?Boolean(staged.reference):Boolean(existingRef&&(!assets.result_id||existingRef.resultId===assets.result_id));
    if(analysis.reference_confirmed&&!referenceAvailable)throw new Error('请先指定诊断参考 CSV。');
    if(analysis.calculate_error){
      const truth=assets.result_id?readConversation(activeConversation.id)?.experiments?.find(r=>r.id===assets.result_id)?.ground_truth:null;
      const l='truth_L_H' in analysis?analysis.truth_L_H:assets.result_id?truth?.L_H:Number(state.truthDraft.l)/1e6;
      const c='truth_C_F' in analysis?analysis.truth_C_F:assets.result_id?truth?.C_F:Number(state.truthDraft.c)/1e6;
      if(!(l>0&&c>0))throw new Error('计算真值误差需要同时提供真实 L 和 C。');
    }
  }
  if(configurationFingerprint()!==context.fingerprint)throw new Error('会话、数据或设置已变化，本次操作未应用。请按当前界面重新发送指令。');
  return staged;
}
async function applyConfigurationExtras(action,staged){
  const assets=action.assets||{},analysis=action.analysis||{},labels=[];
  if(staged.dataset){
    state.dataset=staged.dataset;state.csvFile=staged.file;fillMappings(staged.dataset);
    $('file-info').hidden=false;$('file-name').textContent=staged.dataset.filename;ui($('file-rows'),`${staged.dataset.rows} 行 · CSV 已读取`);
    ui($('chat-context'),`已关联：${staged.dataset.filename}`);labels.push('CSV = '+staged.dataset.filename);
    await archiveCurrentCsv(staged.file);
  }
  if(staged.mapping){
    const preserveResult=!staged.dataset&&state.result&&Object.entries(staged.mapping).every(([key,value])=>state.mapping?.[key]===value);
    for(const [key,id] of Object.entries(mappingFields))$(id).value=staged.mapping[key]??'';
    state.mapping=staged.mapping;state.summary=staged.summary;state.chart=staged.summary.chart;state.zoom=null;
    if(!preserveResult)invalidateResult();displaySummary();showWaveform();
    if(preserveResult)displayResult({preserveZoom:true});else drawCharts();labels.push(tr('列映射已应用'));
  }
  if('image' in staged){
    BuckCircuit.rememberImage();if(staged.image)BuckCircuit.rememberImage(staged.image.data_url,staged.image.name);
    $('controller-image').value=staged.image?.data_url||'';$('controller-image-name').value=staged.image?.name||'';
    labels.push('电路图 = '+(staged.image?.name||tr('默认接线图')));
  }
  if(assets.result_id){await selectExperiment(assets.result_id,{fromChat:true,fetchFull:false});labels.push('结果 = '+assets.result_id);}
  for(const [key,field] of [['truth_L_H','l'],['truth_C_F','c']])if(key in analysis){
    state.truthDraft[field]=analysis[key]===null?'':convertedField(analysis[key],1e6);state.truthComparison=null;state.truthError='';labels.push(`${key} = ${analysis[key]??''}`);
  }
  if('reference_csv_id' in analysis){
    diagnosticReference=staged.reference?{owner:sessionId,resultId:state.resultId,base,dataset_id:staged.reference.dataset_id,filename:staged.reference.filename}:null;
    $('diagnostic-reference-confirm').checked=false;refreshDiagnosticReference();labels.push('参考 CSV = '+(staged.reference?.filename||tr('未选择')));
  }
  if('reference_confirmed' in analysis)$('diagnostic-reference-confirm').checked=analysis.reference_confirmed;
  if(analysis.calculate_error)calculateParameterError({fromChat:true});
  else if('truth_L_H' in analysis||'truth_C_F' in analysis){captureExperiment({updateTruth:true});displayTruthComparison();}
  if(staged.reference&&!$('diagnostic-reference-confirm').checked)labels.push('请确认：此参考是否来自同一实验、同一工况，仅去除了测量噪声？确认前不会用它做参考对照。');
  return labels;
}
