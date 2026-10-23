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

  function render(element, source) {
    const raw = String(source ?? "");
    element.classList.add("markdown-body");
    try {
      // Safe parser configuration above: no raw HTML, unsafe URLs or image loads.
      element.innerHTML = md.render(raw);
    } catch {
      element.textContent = raw;
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
