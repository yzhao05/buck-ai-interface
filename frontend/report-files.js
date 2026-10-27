/* Local report packaging. No report data is sent to another service. */
"use strict";
function reportFolderUnavailableReason(){
  if(!window.isSecureContext)return "当前页面不是安全环境，无法选择保存文件夹。请通过 HTTPS 或 localhost 打开网页。";
  if(typeof window.showDirectoryPicker!=='function')return "当前浏览器未提供文件夹选择功能。可尝试在桌面版 Chrome 或 Edge 中打开此网页。";
  let crossOriginFrame=false;
  try{crossOriginFrame=window.top.location.origin!==window.location.origin;}catch{crossOriginFrame=true;}
  if(crossOriginFrame)return "当前网页嵌在其他页面中，文件夹选择可能被限制。请在独立标签页打开此网页后重试。";
  return "";
}
function checkReportExport(isCurrent){if(!isCurrent())throw new DOMException('Export superseded','AbortError');}
async function saveReportFolder(parent,stem,json,pdf,isCurrent){
  let name,folder;
  for(let index=0;index<10000;index++){
    checkReportExport(isCurrent);name=index?`${stem}_${index+1}`:stem;
    try{await parent.getDirectoryHandle(name);}
    catch(error){
      if(error.name==='NotFoundError'){
        checkReportExport(isCurrent);folder=await parent.getDirectoryHandle(name,{create:true});break;
      }
      if(error.name!=='TypeMismatchError')throw error;
    }
  }
  if(!folder)throw new Error('无法创建新的报告文件夹。');
  for(const [extension,blob] of [['json',json],['pdf',pdf]]){
    checkReportExport(isCurrent);
    const handle=await folder.getFileHandle(`${name}.${extension}`,{create:true});
    checkReportExport(isCurrent);const writable=await handle.createWritable();
    try{checkReportExport(isCurrent);await writable.write(blob);checkReportExport(isCurrent);await writable.close();}
    catch(error){try{await writable.abort();}catch{}throw error;}
  }
  checkReportExport(isCurrent);return name;
}
