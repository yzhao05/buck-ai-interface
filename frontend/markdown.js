/* Render assistant Markdown; keep the original source in conversation records. */
"use strict";
window.BuckMarkdown = (() => {
  const md = window.markdownit({html:false, linkify:true, breaks:true, typographer:false, maxNesting:20});
  const validate = md.validateLink.bind(md);
  md.validateLink = url => validate(url) && /^(?:https?:|mailto:)/i.test(url);
  const renderLink = md.renderer.rules.link_open || ((tokens, index, options, env, self) => self.renderToken(tokens,index,options));
  md.renderer.rules.link_open = (tokens,index,options,env,self) => {
    tokens[index].attrSet("target","_blank");
    tokens[index].attrSet("rel","noopener noreferrer");
    return renderLink(tokens,index,options,env,self);
  };
  // Assistant-supplied images are not fetched. Preserve the descriptive text.
  md.renderer.rules.image = (tokens,index) => md.utils.escapeHtml(tokens[index].content || "[image]");
  // Handle the model's common standalone '**heading **' typo. Only inline
  // block content is normalized, never fenced/indented code or escaped text.
  md.core.ruler.after("block","assistant_bold_spacing", state => {
    for (const token of state.tokens) {
      if (token.type === "inline") token.content = token.content.replace(/^\*\*([^\s*`\\][^*`\\\n]*?\S)[ \t]+\*\*$/gm,"**$1**");
    }
  });

  const citationPattern = /\[kb:[^\]\s]+\]/g;
  const citationHeading = /^(?:依据|来源|引用|证据|references?|sources?|citations?|evidence)[:：]?$/i;
  const withoutCitations = text => text.replace(citationPattern, "");
  const onlyCitations = text => withoutCitations(text)!==text && /^[\s,，;；、。.—–\-()（）:：]*$/.test(
    withoutCitations(text).replace(/^(?:依据|来源|引用|证据|references?|sources?|citations?|evidence)\s*[:：]?/i,""));

  // Only known application status annotations, never arbitrary parentheses,
  // physical parameters or code examples. Match across inline formatting.
  const stateAssignment = String.raw`(?:identification_enabled|controller_confirmed|result_loaded)\s*=\s*(?:true|false)`;
  const stateNote = new RegExp(String.raw`\(\s*${stateAssignment}(?:\s*[,，;；]\s*${stateAssignment})*\s*\)|（\s*${stateAssignment}(?:\s*[,，;；]\s*${stateAssignment})*\s*）`, 'g');
  const standaloneState = new RegExp(`^\\s*${stateAssignment}\\s*$`);
  function hideStateNotes(element) {
    for(const block of element.querySelectorAll('p,li,td,th,h1,h2,h3,h4,h5,h6')){
      const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT), nodes=[];
      let text='';
      while(walker.nextNode()){
        const node=walker.currentNode, parent=node.parentElement;
        const protectedText=parent.closest('pre,a') || (parent.closest('code') && !standaloneState.test(parent.closest('code').textContent));
        nodes.push({node,start:text.length,end:text.length+node.textContent.length});
        text+=protectedText?'\uFFFC'.repeat(node.textContent.length):node.textContent;
      }
      for(const match of [...text.matchAll(stateNote)].reverse()){
        const start=match.index,end=start+match[0].length;
        const first=nodes.find(item=>item.start<=start && start<item.end);
        const last=nodes.find(item=>item.start<end && end<=item.end);
        const range=document.createRange();
        range.setStart(first.node,start-first.start);
        range.setEnd(last.node,end-last.start);
        range.deleteContents();
      }
    }
  }

  function hideInternalMetadata(element, streaming) {
    hideStateNotes(element);
    for(const table of element.querySelectorAll('table')){
      const header=table.tHead?.rows[0], rows=[...table.tBodies].flatMap(body=>[...body.rows]);
      if(!header)continue;
      for(let column=header.cells.length-1;column>=0;column--){
        if(!citationHeading.test(header.cells[column].textContent.trim()))continue;
        const cells=rows.map(row=>row.cells[column]);
        if(cells.some(cell=>!cell))continue;
        const texts=cells.map(cell=>cell.textContent.trim());
        const hasCitations=texts.some(text=>withoutCitations(text)!==text);
        if((hasCitations || streaming) && texts.every(text=>!text || /^[-—–]+$/.test(text) || onlyCitations(text))){
          for(const row of [header,...rows])row.deleteCell(column);
        }
      }
      if(!header.cells.length)table.remove();
    }
    // Remove source-only notes, never substantive explanations or code blocks.
    for(const heading of element.querySelectorAll('h1,h2,h3,h4,h5,h6')){
      if(!citationHeading.test(heading.textContent.trim()))continue;
      const next=heading.nextElementSibling;
      if(next?.matches('p,ul,ol') && !next.querySelector('pre') && onlyCitations(next.textContent))heading.remove();
    }
    for(const note of element.querySelectorAll('p,li')){
      if(!note.closest('pre') && onlyCitations(note.textContent))note.remove();
    }
    for(const list of element.querySelectorAll('ul,ol'))if(!list.children.length)list.remove();
    const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT), nodes=[];
    while(walker.nextNode())nodes.push(walker.currentNode);
    for(const node of nodes){
      if(node.parentElement.closest('pre'))continue;
      if(node.parentElement.closest('code') && !onlyCitations(node.textContent))continue;
      node.textContent=withoutCitations(node.textContent);
    }
  }

  function render(element, source, {streaming=false}={}) {
    // Hold a split citation prefix until the next streamed chunk arrives.
    const raw = streaming ? String(source ?? "").replace(/\[(?:k(?:b(?::[^\]\n]*)?)?)?$/, "") : String(source ?? "");
    element.classList.add("markdown-body");
    try {
      // Safe parser configuration above: no raw HTML, unsafe URLs or image loads.
      element.innerHTML = md.render(raw);
      hideInternalMetadata(element, streaming);
    } catch {
      element.textContent = withoutCitations(raw).replace(stateNote,'');
      element.classList.remove("markdown-body");
      return;
    }
    for (const table of element.querySelectorAll("table")) {
      const wrapper = document.createElement("div");
      wrapper.className = "markdown-table-wrap"; wrapper.tabIndex = 0;
      wrapper.setAttribute("role","region");
      window.BuckI18n.attr(wrapper,"aria-label","回复表格，可左右滚动");
      table.before(wrapper); wrapper.append(table);
    }
    for (const pre of element.querySelectorAll("pre")) {
      const code = pre.querySelector("code");
      if (!code) continue;
      const wrapper = document.createElement("div"), toolbar = document.createElement("div");
      wrapper.className = "markdown-code-block"; toolbar.className = "markdown-code-toolbar";
      const label = document.createElement("span");
      const language = [...code.classList].find(name=>name.startsWith("language-"))?.slice(9);
      if (language) label.textContent = language;
      else window.BuckI18n.text(label,"纯文本");
      const button = document.createElement("button");
      button.type = "button"; button.className = "markdown-copy";
      window.BuckI18n.text(button,"复制代码");
      window.BuckI18n.attr(button,"aria-label","复制代码");
      const status = document.createElement("span"); status.className = "sr-only"; status.setAttribute("role","status");
      button.onclick = async () => {
        try {
          await navigator.clipboard.writeText(code.textContent);
          window.BuckI18n.text(status,"代码已复制");
          window.BuckI18n.text(button,"已复制");
        } catch {
          const selection = window.getSelection(), range = document.createRange();
          range.selectNodeContents(code); selection.removeAllRanges(); selection.addRange(range);
          window.BuckI18n.text(status,"无法自动复制，已选中代码，请手动复制。");
          window.BuckI18n.text(button,"请手动复制");
        }
        setTimeout(()=>{if(button.isConnected)window.BuckI18n.text(button,"复制代码");},1800);
      };
      toolbar.append(label,button,status); pre.before(wrapper); wrapper.append(toolbar,pre);
      pre.tabIndex = 0;
    }
  }
  return {render};
})();
