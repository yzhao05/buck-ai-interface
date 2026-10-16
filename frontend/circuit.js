/* Fixed circuit topology with editable numerical parameters and a reference image. */
"use strict";
window.BuckCircuit = (() => {
  const el = id => document.getElementById(id), I = window.BuckI18n;
  // key, label, display unit, SI multiplier, default, display min, display max
  const specs = [
    ["R1_ohm","R1","kΩ",1e3,11,.001,1e6], ["R2_ohm","R2","kΩ",1e3,85,.001,1e6],
    ["R3_ohm","R3","kΩ",1e3,120,.001,1e6], ["R4_ohm","R4","kΩ",1e3,47,.001,1e6],
    ["C2_F","C2","nF",1e-9,1.1,.001,1e6], ["C3_F","C3","nF",1e-9,2.7,.001,1e6],
    ["opamp_gain","运放增益","",1,1e6,1,1e8], ["reference_V","参考电压","V",1,5,0,2000],
    ["pwm_gain","调制增益","",1,.25,.000001,100], ["duty_min","占空比下限","",1,.1,0,.999999],
    ["duty_max","占空比上限","",1,.9,.000001,1], ["carrier_phase_at_t0","载波初始相位","周期",1,0,0,.999999],
    ["initial_c2_V","C2 初始电压","V",1,0,-2000,2000], ["initial_c3_V","C3 初始电压","V",1,0,-2000,2000],
    ["frequency","开关频率","kHz",1,100,.1,2000], ["vin","输入电压","V",1,28,.1,2000],
    ["fixed-r","已知负载电阻","Ω",1,6,.011,100000]
  ];
  let draftImage = "", uploading = false, renderKey = "", generation = 0;
  const number = value => Number.isFinite(Number(value)) ? Number(Number(value).toPrecision(6)).toString() : "—";
  function read() {
    let saved = {};
    try { saved = JSON.parse(el("controller-parameters").value || "{}"); } catch {}
    return Object.fromEntries(specs.slice(0,14).map(([key,,,scale,value])=>[key,Number(Number(saved?.[key] ?? value*scale).toPrecision(15))]));
  }
  function validImage(value) { return value.length <= 360000 && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value); }
  function node(tag, attrs={}, text) {
    const n = document.createElementNS("http://www.w3.org/2000/svg",tag);
    Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function draw(values, interactive=false) {
    const svg=node("svg",{viewBox:"0 0 960 650",role:"img","aria-label":I.translate("Buck 功率级与 RC 控制电路")});
    const wire=(d,attrs={})=>svg.append(node("path",{d,fill:"none",stroke:"#42666a","stroke-width":2.5,...attrs}));
    const txt=(x,y,t,attrs={})=>svg.append(node("text",{x,y,fill:"#34585d","font-size":16,...attrs},t));
    const dot=(x,y)=>svg.append(node("circle",{cx:x,cy:y,r:4,fill:"#42666a"}));
    const ground=(x,y)=>wire(`M${x} ${y}v8 m-15 0h30 m-25 6h20 m-15 6h10`);
    const resistor=(x,y,w=62)=>{wire(`M${x} ${y}h12 m0 -8h${w-24}v16h-${w-24}z m${w-24} 8h12`);};
    const verticalResistor=(x,y)=>{wire(`M${x} ${y}v12 m-8 0h16v36h-16z m8 36v12`);};
    const capacitor=(x,y)=>wire(`M${x} ${y}h20 m0 -13v26 m10 -26v26 m0 -13h20`);
    const label=(x,y,key,prefix,unit="")=>{
      const text=prefix+number(values[key])+(unit ? " "+unit : "");
      if (!interactive) { txt(x,y,text,{fill:"#087e73"}); return; }
      const g=node("g",{role:"button",tabindex:0,"data-parameter":key,"aria-label":I.translate("修改参数")+": "+text,class:"circuit-parameter"});
      g.append(node("rect",{x:x-5,y:y-20,width:Math.max(70,text.length*9+10),height:29,rx:5,fill:"#e8f5f1"}));
      g.append(node("text",{x,y,fill:"#087e73","font-size":16},text));
      const focus=()=>{const input=el("circuit-"+key);input.focus();input.select();input.scrollIntoView({block:"nearest",behavior:"smooth"});};
      g.onclick=focus;g.onkeydown=e=>{if(e.key==="Enter" || e.key===" "){e.preventDefault();focus();}};
      svg.append(g);
    };
    txt(25,28,I.translate("功率级 · 固定负载"),{"font-size":18,"font-weight":600});
    wire("M65 85V65H180 M230 65H310 M430 65H830 M65 135V200H830 M550 65V110 M550 150V200 M735 65V95 M735 155V200 M275 65V115 M275 155V200");
    svg.append(node("circle",{cx:65,cy:110,r:25,fill:"white",stroke:"#42666a","stroke-width":2.5}));
    txt(59,104,"+");txt(59,126,"−");label(23,245,"vin","Vin = ","V");
    wire("M180 65l43 -21 M230 65h5");dot(180,65);dot(230,65);txt(235,49,"T1");
    wire("M310 65q15 -33 30 0q15 -33 30 0q15 -33 30 0q15 -33 30 0");
    txt(330,28,"L · "+I.translate("待辨识"),{fill:"#bf8533"});
    wire("M275 115v8 m-13 0h26 M262 155l13 -32l13 32z");txt(236,180,"D1");
    wire("M530 110h40 M530 125h40 M550 125v25");
    txt(579,137,"C · "+I.translate("待辨识"),{fill:"#bf8533"});
    verticalResistor(735,95);label(765,132,"fixed-r","R = ","Ω");
    ground(450,200);dot(275,65);dot(550,65);dot(735,65);txt(844,70,"vo");txt(445,51,"iL →");
    txt(25,287,I.translate("控制级 · 接线固定"),{"font-size":18,"font-weight":600});
    // vo -- R1 -- va -- (R2 || C2) -- vm; vm -- R4 -- ground.
    wire("M40 390H105 M177 390H245 M245 350V425 M245 350H285 M357 350H410 M410 350V425 M245 425H295 M345 425H410 M410 390H590");
    txt(30,380,"vo");resistor(105,390,72);label(93,365,"R1_ohm","R1 = ","kΩ");
    resistor(285,350,72);label(266,328,"R2_ohm","R2 = ","kΩ");
    capacitor(295,425);label(265,462,"C2_F","C2 = ","nF");dot(245,390);dot(410,390);txt(217,411,"va");txt(456,380,"vm");
    wire("M480 390V445 M480 505V515");verticalResistor(480,445);ground(480,515);label(500,489,"R4_ohm","R4 = ","kΩ");
    // vm -- C3 -- vb -- R3 -- vop (negative feedback).
    wire("M480 390V300H535 M585 300H663 M735 300H800V420 M735 420H825");
    capacitor(535,300);resistor(663,300,72);label(522,278,"C3_F","C3 = ","nF");label(653,278,"R3_ohm","R3 = ","kΩ");txt(612,320,"vb");dot(480,390);dot(800,420);
    svg.append(node("path",{d:"M590 355L735 420L590 485Z",fill:"#f2f8f7",stroke:"#42666a","stroke-width":2.5}));
    txt(602,397,"−");txt(602,458,"+");label(616,429,"opamp_gain","A = ");
    wire("M545 455H590");txt(539,449,"Vref",{"font-size":13});label(561,531,"reference_V","Vref = ","V");txt(804,410,"vop");
    label(26,510,"initial_c2_V","C2(0) = ","V");label(237,510,"initial_c3_V","C3(0) = ","V");
    wire("M825 420H925V565H880",{"stroke-dasharray":"5 5"});
    svg.append(node("rect",{x:42,y:557,width:838,height:70,rx:9,fill:"#f5faf9",stroke:"#b9d9d3"}));
    txt(57,580,"T1 ← PWM ← "+I.translate("限幅")+" ← "+I.translate("调制增益"));
    label(56,609,"frequency","fs = ","kHz");label(221,609,"pwm_gain","K = ");label(350,609,"duty_min","Dmin = ");label(503,609,"duty_max","Dmax = ");label(662,609,"carrier_phase_at_t0","phase = ");
    // Gate/control link uses net labels; it is not a conducting power wire.
    txt(206,96,"PWM",{"font-size":13});
    return svg;
  }
  function valuesFromSaved() {
    const saved=read();
    return Object.fromEntries(specs.map(([key,,,scale,value])=>[key,key in saved ? saved[key]/scale : Number(el(key)?.value || value)]));
  }
  function draftValues() { return Object.fromEntries(specs.map(([key])=>[key,Number(el("circuit-"+key).value)])); }
  function fillDrawing(target, values, image, interactive) {
    target.replaceChildren();
    if (validImage(image)) {
      const img=document.createElement("img");img.src=image;img.alt=I.translate("用户上传的电路参考图");target.append(img);
    } else target.append(draw(values,interactive));
  }
  function render(force=false) {
    const values=valuesFromSaved(), image=el("controller-image").value;
    const key=JSON.stringify([values,image,I.language]);
    if (!force && key===renderKey) return;
    renderKey=key;
    fillDrawing(el("circuit-preview"),values,image,false);
    el("show-default-circuit").hidden=!image;
    I.text(el("circuit-caption"),image ? "已上传参考图 · 计算仍使用已填写参数" : "默认接线图 · 点击放大并编辑参数");
  }
  function renderDraft() { fillDrawing(el("circuit-drawing"),draftValues(),draftImage,true); }
  function open() {
    const values=valuesFromSaved();
    specs.forEach(([key])=>el("circuit-"+key).value=values[key]);
    draftImage=validImage(el("controller-image").value) ? el("controller-image").value : "";
    I.text(el("circuit-error"),"");renderDraft();el("circuit-dialog").showModal();
  }
  const fields=el("circuit-fields");
  for (const [key,label,unit,,value,min,max] of specs) {
    const wrap=document.createElement("label");wrap.append(I.node(label));if(unit)wrap.append(document.createTextNode(" / "),I.node(unit));
    const input=document.createElement("input");Object.assign(input,{type:"number",id:"circuit-"+key,value:String(value),min:String(min),max:String(max),step:"any",required:true});
    input.oninput=renderDraft;wrap.append(input);fields.append(wrap);
  }
  el("edit-circuit").onclick=el("circuit-preview").onclick=open;
  el("close-circuit").onclick=()=>el("circuit-dialog").close();
  el("circuit-dialog").addEventListener("close",()=>{generation++;draftImage="";uploading=false;el("save-circuit").disabled=false;el("circuit-file").value="";});
  el("upload-circuit").onclick=()=>{open();el("circuit-file").click();};
  el("circuit-upload-edit").onclick=()=>el("circuit-file").click();
  el("circuit-default-edit").onclick=()=>{draftImage="";renderDraft();};
  el("show-default-circuit").onclick=()=>{
    el("controller-image").value="";render();document.dispatchEvent(new Event("buck-circuit-change"));
  };
  el("reset-controller").onclick=()=>{specs.forEach(([key,,,,value])=>el("circuit-"+key).value=value);renderDraft();};
  el("circuit-file").onchange=async event=>{
    const file=event.target.files[0];if(!file)return;
    const ticket=++generation;
    uploading=true;el("save-circuit").disabled=true;I.text(el("circuit-error"),"");
    let bitmap;
    try {
      if(!["image/png","image/jpeg","image/webp"].includes(file.type)||file.size>5*1024*1024)throw new Error("请选择不超过 5 MB 的 PNG、JPG 或 WebP 图片。");
      bitmap=await createImageBitmap(file);
      if(bitmap.width*bitmap.height>40e6)throw new Error("图片尺寸过大，请缩小后上传。");
      const canvas=document.createElement("canvas"),ctx=canvas.getContext("2d");
      let data="";
      for(const limit of [1600,1200,900]) {
        const ratio=Math.min(1,limit/Math.max(bitmap.width,bitmap.height));
        canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
        ctx.fillStyle="white";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);
        data=canvas.toDataURL("image/jpeg",.82);
        if(data.length<=360000)break;
      }
      if(!validImage(data))throw new Error("图片压缩后仍过大，请裁剪电路区域后重试。");
      if(ticket===generation && el("circuit-dialog").open){draftImage=data;renderDraft();}
    } catch(error) {if(ticket===generation)I.text(el("circuit-error"),error.message.startsWith("图片")||error.message.startsWith("请选择") ? error.message : "无法读取图片，请选择有效的 PNG、JPG 或 WebP 文件。");}
    finally{bitmap?.close();if(ticket===generation){uploading=false;el("save-circuit").disabled=false;}el("circuit-file").value="";}
  };
  el("circuit-form").onsubmit=event=>{
    event.preventDefault();if(uploading)return;
    const values=draftValues();
    if(values.duty_min>=values.duty_max){I.text(el("circuit-error"),"占空比下限必须小于上限。");return;}
    const params=Object.fromEntries(specs.slice(0,14).map(([key,,,scale])=>[key,Number((values[key]*scale).toPrecision(15))]));
    el("controller-parameters").value=JSON.stringify(params);el("controller-image").value=draftImage;
    for(const id of ["frequency","vin","fixed-r"])el(id).value=values[id];
    el("circuit-dialog").close();render();document.dispatchEvent(new Event("buck-circuit-change"));
  };
  document.addEventListener("buck-language-change",()=>{render(true);if(el("circuit-dialog").open)renderDraft();});
  function setBusy(busy) {
    for(const id of ["edit-circuit","circuit-preview","upload-circuit","show-default-circuit"])el(id).disabled=busy;
    if(busy && el("circuit-dialog").open)el("circuit-dialog").close();
  }
  return {read,render,setBusy};
})();
