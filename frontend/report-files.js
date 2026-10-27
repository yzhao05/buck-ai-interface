/* Local report packaging. No report data is sent to another service. */
"use strict";
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
