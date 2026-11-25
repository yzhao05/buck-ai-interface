/* Images are explicit message attachments, never implicit circuit/config changes. */
"use strict";
const chatImageLimit=360000;
function validChatImage(image) {
  return typeof image?.name==="string" && image.name.length<=200 && typeof image.data_url==="string" && image.data_url.length<=chatImageLimit && /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$/.test(image.data_url);
}
function chatImages() {
  try{return JSON.parse($("chat-images").value || "[]").filter(validChatImage).slice(0,3);}catch{return [];}
}
function setChatImages(images){$("chat-images").value=JSON.stringify(images);renderChatImages();}
function imagePreview(image){
  $("chat-image-preview").src=image.data_url;$("chat-image-preview").alt=image.name;
  $("chat-image-name").textContent=image.name;
  $("chat-image-dialog").showModal();
}
function imageThumbnail(image){
  const button=document.createElement("button");button.type="button";button.className="chat-image-thumbnail";
  window.BuckI18n.attr(button,"title","查看图片");window.BuckI18n.attr(button,"aria-label","查看图片");
  const img=document.createElement("img");img.src=image.data_url;img.alt=image.name;
  const name=document.createElement("span");name.textContent=image.name;button.append(img,name);
  button.onclick=()=>imagePreview(image);return button;
}
function renderMessageImages(article,images=[]){
  const valid=images.filter(validChatImage);if(!valid.length)return;
  const gallery=document.createElement("div");gallery.className="message-images";
  for(const image of valid)gallery.append(imageThumbnail(image));article.append(gallery);
}
function chatPdf(){
  try {
    const pdf=JSON.parse($("chat-pdf").value || "null");
    return typeof pdf?.name==="string" && typeof pdf.text==="string" && pdf.text ? pdf : null;
  } catch { return null; }
}
async function attachChatPdf(file){
  if(!file || historyLocked() || !activeConversation)return;
  if(file.size>8*1024*1024){notice("请选择不超过 8 MB 的 PDF。");return;}
  const target=activeConversation.id;
  state.busy=true;controls();
  try {
    const form=new FormData();form.append("file",file);
    const pdf=await api("/api/chat/pdf",{method:"POST",body:form});
    if(activeConversation?.id!==target)return;
    $("chat-pdf").value=JSON.stringify(pdf);
    renderChatImages();persistConversation();
  } catch(error){notice(error.message);}
  finally{state.busy=false;controls();}
}
function renderChatImages(){
  const list=$("chat-image-list");list.replaceChildren();
  const images=chatImages(),pdf=chatPdf();
  const count=images.length+(pdf ? 1 : 0);
  list.hidden=!count;
  if(pdf){
    const item=document.createElement("div");item.className="chat-image-item pdf-chip";
    const label=document.createElement("span");label.textContent="PDF · "+pdf.name;
    const remove=document.createElement("button");remove.type="button";remove.className="chat-image-remove";remove.textContent="×";
    window.BuckI18n.attr(remove,"aria-label","移除 PDF");
    remove.onclick=()=>{if(historyLocked())return;$("chat-pdf").value="";renderChatImages();persistConversation();};
    item.append(label,remove);list.append(item);
  }
  $("compact-chat-images").hidden=!count;
  $("compact-chat-images").textContent=`▧ ${count}`;
  images.forEach((image,index)=>{
    const item=document.createElement("div");item.className="chat-image-item";item.append(imageThumbnail(image));
    const remove=document.createElement("button");remove.type="button";remove.className="chat-image-remove";remove.textContent="×";
    window.BuckI18n.attr(remove,"aria-label","移除图片");window.BuckI18n.attr(remove,"title","移除图片");
    remove.onclick=()=>{if(historyLocked())return;setChatImages(chatImages().filter((_,i)=>i!==index));persistConversation();};
    item.append(remove);list.append(item);
  });
}
async function compressChatImage(file){
  if(!["image/png","image/jpeg","image/webp","image/gif"].includes(file.type) || file.size>8*1024*1024)
    throw new Error("请选择不超过 8 MB 的 PNG、JPG、GIF 或 WebP 图片。");
  let bitmap;
  try{
    bitmap=await createImageBitmap(file);
    if(bitmap.width*bitmap.height>40e6)throw new Error("图片尺寸过大，请缩小后上传。");
    const canvas=document.createElement("canvas"),ctx=canvas.getContext("2d");
    for(const limit of [1800,1400,1000,800]){
      const ratio=Math.min(1,limit/Math.max(bitmap.width,bitmap.height));
      canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
      ctx.fillStyle="white";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);
      const png=canvas.toDataURL("image/png");
      const data=png.length<=chatImageLimit ? png : canvas.toDataURL("image/jpeg",.86);
      if(data.length<=chatImageLimit)return {name:(file.name||"image.png").slice(0,200),data_url:data};
    }
    throw new Error("图片压缩后仍过大，请裁剪电路区域后重试。");
  }finally{bitmap?.close();}
}
async function attachChatFiles(files){
  if(historyLocked() || !activeConversation)return;
  const selected=[...files];if(!selected.length)return;
  // Reset even on a count-limit rejection so picking the same files can retry.
  $("chat-image-file").value="";
  if(chatImages().length+selected.length>3){notice("每次最多附加 3 张图片。");return;}
  state.busy=true;controls();
  try{
    const images=[];for(const file of selected)images.push(await compressChatImage(file));
    setChatImages([...chatImages(),...images]);persistConversation();
  }catch(error){notice(error.message.startsWith("图片") || error.message.startsWith("请选择") ? error.message : "无法读取图片，请选择有效的图片文件。");}
  finally{state.busy=false;$("chat-image-file").value="";controls();}
}
async function attachCircuitImage(){
  if(historyLocked() || !activeConversation)return;
  if(chatImages().length>=3){notice("每次最多附加 3 张图片。");return;}
  const uploaded=$("controller-image").value;
  if(validChatImage({name:"circuit.png",data_url:uploaded})){
    setChatImages([...chatImages(),{name:"circuit.png",data_url:uploaded}]);persistConversation();
  }else{
    const preview=$("circuit-preview"), svg=preview.querySelector("svg"), reference=preview.querySelector("img");
    if(!svg && !reference)return;
    const url=svg ? URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)],{type:"image/svg+xml;charset=utf-8"})) : reference.src;
    state.busy=true;controls();
    try{
      const img=new Image();img.src=url;await img.decode();
      const canvas=document.createElement("canvas");
      canvas.width=svg ? 1800 : img.naturalWidth;
      canvas.height=svg ? Math.round(1800*svg.viewBox.baseVal.height/svg.viewBox.baseVal.width) : img.naturalHeight;
      const ctx=canvas.getContext("2d");ctx.fillStyle="white";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(img,0,0,canvas.width,canvas.height);
      const file=await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));
      const image=await compressChatImage(new File([file],"default-circuit.png",{type:"image/png"}));
      setChatImages([...chatImages(),image]);persistConversation();
    }catch{notice("电路图读取失败，请上传电路截图后重试。");return;}
    finally{if(svg)URL.revokeObjectURL(url);state.busy=false;controls();}
  }
  if(!$("chat-input").value.trim())$("chat-input").value=tr("请分析这张电路图的接线与元件标注，并指出需要核对的地方。");
  persistConversation();$("chat-input").focus();notice("电路图已附加，点击发送后交给助手分析。",true);
}
const manualJobs=new Map();
const manualStages={
  upload:[1,"上传 PDF"], queued:[1,"等待后台处理"], read:[1,"读取 PDF 并检查已有记录"],
  extract:[2,"提取手册条目"], validate:[2,"校验条目字段和页码"], repair:[2,"根据校验错误重新提取条目"],
  page:[3,"读取曲线图页"], locate:[3,"定位图例、坐标轴和刻度"], trace:[3,"标定坐标轴并提取曲线"],
  part:[3,"核对料号来源页"], verify:[3,"复核曲线采样叠图"],
  reuse:[4,"核对已入库的手册"], save:[4,"写入共享知识库"]
};
function manualKey(){return activeConversation && JSON.stringify([base,conversationPrefix,activeConversation.id]);}
function manualStepText(step){
  const stage=manualStages[step.stage];
  return [stage?stage[0]+"/4":"",stage?tr(stage[1]):step.stage,step.page?"PDF p. "+step.page:"",step.detail].filter(Boolean).join(" · ");
}
function renderManualJob(){
  const job=manualJobs.get(manualKey()), panel=$("manual-progress");
  panel.hidden=!job;
  $("chat-attachment-choice").querySelector('option[value="manual"]').disabled=job?.status==="processing";
  if(!job)return;
  $("manual-filename").textContent=job.filename || "";
  const steps=job.steps || [];
  const rows=steps.map(step=>{
    const row=document.createElement("li");row.textContent=manualStepText(step);return row;
  });
  $("manual-steps").replaceChildren(...rows);
  const last=rows.at(-1)?.textContent || "";
  const status=job.status==="failed" ? [tr("手册导入失败"),last].filter(Boolean).join(" · ")
    : job.status==="disconnected" ? tr("读取导入进度失败")
    : job.status==="completed" ? tr("手册导入完成") : last || tr("正在读取导入进度");
  if($("manual-status").textContent!==status)$("manual-status").textContent=status;
  $("manual-reconnect").hidden=job.status!=="disconnected";
  const result=job.result;
  $("manual-outcome").textContent=result ? [
    tr(result.already_saved?"这份手册已在项目知识库中。":"手册已入库，可在新对话中查询。"),
    tr("已收录条目")+" "+result.entries+" · "+tr("可查值曲线")+" "+result.verified,
    ...(result.unreadable.length?[tr("以下曲线仅保存说明和页码："),...result.unreadable.map(r=>"p. "+r.page+" · "+r.title)]:[])
  ].join("\n") : tr(job.error || "");
}
async function watchManualJob(key,job){
  if(job.watching || !job.id || job.status!=="processing")return;
  job.watching=true;
  try{
    while(key===manualKey() && manualJobs.get(key)===job){
      const record=await api("/api/manuals/import/"+job.id,{owner:job.owner});
      if(!["processing","completed","failed"].includes(record.status))throw Error(tr("无法读取手册导入状态，请重新选择 PDF。"));
      Object.assign(job,{error:""},record);
      if(key!==manualKey() || manualJobs.get(key)!==job)return;
      renderManualJob();
      if(record.status!=="processing"){
        notice(job.error || $("manual-outcome").textContent,record.status==="completed");return;
      }
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
  }catch(error){
    job.status=error.status===404?"failed":"disconnected";job.error=error.message;
    if(key===manualKey())renderManualJob();
  }finally{job.watching=false;}
}
function restoreManualJob(){
  const key=manualKey();
  let job=manualJobs.get(key);
  if(key && !job){
    try{
      const saved=sessionStorage.getItem("manual-job:"+key);
      // Earlier versions stored just the job ID.
      const token=saved?.startsWith("{") ? JSON.parse(saved) : {id:saved};
      if(typeof token?.id==="string" && token.id){
        job={id:token.id,filename:typeof token.filename==="string"?token.filename:"",owner:activeConversation.id,status:"processing"};
        manualJobs.set(key,job);
      }
    }catch{/* storage may be unavailable or incomplete */}
  }
  if(job?.status==="disconnected")job.status="processing";
  renderManualJob();
  if(job){$("manual-progress").open=true;void watchManualJob(key,job);}
}
async function importManual(file){
  if(!file || historyLocked() || !activeConversation)return;
  if(file.size>8*1024*1024){notice("请选择不超过 8 MB 的 PDF。");return;}
  const key=manualKey();
  if(manualJobs.get(key)?.status==="processing")return;
  const job={owner:activeConversation.id,filename:file.name,status:"processing",steps:[{stage:"upload"}]};
  manualJobs.set(key,job);
  try{sessionStorage.removeItem("manual-job:"+key);}catch{/* in-memory progress remains available */}
  $("agent-progress").open=false;
  $("manual-progress").open=true;
  renderManualJob();
  notice();
  try{
    const form=new FormData();form.append("file",file);
    const reply=await api("/api/manuals/import",{method:"POST",body:form,owner:job.owner});
    job.id=reply.job_id;
    try{sessionStorage.setItem("manual-job:"+key,JSON.stringify({id:job.id,filename:job.filename}));}catch{/* optional reload recovery */}
    await watchManualJob(key,job);
  }catch(error){
    Object.assign(job,{status:"failed",error:error.message});
    if(key===manualKey()){renderManualJob();notice(error.message);}
  }
}
function setupChatImages(){
  $("manual-reconnect").onclick=restoreManualJob;
  document.addEventListener("buck-language-change",renderManualJob);
  $("manual-file").onchange=event=>{
    const file=event.target.files[0];event.target.value="";void importManual(file);
  };
  $("chat-attachment-choice").onchange=event=>{
    const choice=event.target.value;event.target.selectedIndex=0;
    if(choice==="manual")$("manual-file").click();
    else if(choice==="circuit")void attachCircuitImage();
    else $(choice==="pdf" ? "chat-pdf-file" : "chat-image-file").click();
  };
  $("chat-pdf-file").onchange=event=>{
    const file=event.target.files[0];event.target.value="";void attachChatPdf(file);
  };
  $("chat-image-file").onchange=event=>void attachChatFiles(event.target.files);
  $("attach-circuit-image").onclick=()=>void attachCircuitImage();
  $("close-chat-image").onclick=()=>$("chat-image-dialog").close();
  $("reuse-chat-image").onclick=()=>{
    if(historyLocked())return;
    if(chatImages().length>=3){notice("每次最多附加 3 张图片。");return;}
    const image={name:$("chat-image-name").textContent,data_url:$("chat-image-preview").src};
    if(!validChatImage(image))return;
    setChatImages([...chatImages(),image]);persistConversation();$("chat-image-dialog").close();$("chat-input").focus();
  };
  $("chat-image-dialog").addEventListener("close",()=>{$("chat-image-preview").removeAttribute("src");});
  $("chat-input").addEventListener("paste",event=>{
    const files=[...event.clipboardData.files].filter(file=>file.type.startsWith("image/"));
    if(files.length){event.preventDefault();void attachChatFiles(files);}
  });
  $("chat-form").addEventListener("dragover",event=>{if(event.dataTransfer.types.includes("Files"))event.preventDefault();});
  $("chat-form").addEventListener("drop",event=>{if(event.dataTransfer.files.length){event.preventDefault();
    const files=[...event.dataTransfer.files];
    if(files.length===1 && /\.pdf$/i.test(files[0].name))void attachChatPdf(files[0]);
    else void attachChatFiles(files);}});
}
