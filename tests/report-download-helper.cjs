// Existing content tests use the real directory-writing path in browser private
// storage, then explicitly download individual files for their content assertions.
async function downloadReportFiles(page){
 await page.evaluate(()=>{
  window.showDirectoryPicker=async()=>{
   const root=await navigator.storage.getDirectory();
   return root.getDirectoryHandle('test-report-exports',{create:true});
  };
 });
 await page.locator('#download-report').click();
 await page.waitForFunction(()=>!reportExportBusy);
 for(const id of ['report-json-link','report-pdf-link'])if(await page.locator('#'+id).isVisible())await page.locator('#'+id).click();
}
module.exports={downloadReportFiles};
