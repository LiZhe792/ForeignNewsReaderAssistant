/* Downloads are created only on request. API tokens never enter browser storage. */
'use strict';
window.MarginExports = class MarginExports {
  constructor({api,toast}) {
    Object.assign(this,{api,toast});this.epoch=0;this.listVersion=0;this.downloadVersion=0;
  }
  $(selector) {return this.root.querySelector(selector);}
  escape(value) {return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  button(action,label,extra='') {return `<button type="button" class="button small" data-export-action="${action}" ${extra}>${label}</button>`;}
  leave() {
    ++this.epoch;++this.listVersion;++this.downloadVersion;this.events?.abort();this.fileRequest?.abort();
    if(this.root?.querySelector('#export-api-token'))this.$('#export-api-token').value='';
    if(this.root?.querySelector('#export-copy-fallback'))this.$('#export-copy-fallback').value='';
    this.fileRequest=null;this.previewValid=false;this.tokenBusy=false;this.token='';this.tokenConfig=null;this.root=null;
  }
  async mount(root,dataset='vocabulary') {
    this.root=root;this.events=new AbortController();const epoch=this.epoch;
    this.dataset=['vocabulary','notes','report','reading_list'].includes(dataset)?dataset:'vocabulary';
    root.innerHTML='<div class="loading-screen"><span class="spinner"></span><p>正在打开导出中心…</p></div>';
    try {
      this.options=await this.api('/exports/options');if(epoch!==this.epoch)return;
      this.render();
      root.addEventListener('click',event=>{
        const button=event.target.closest('[data-export-action]');
        if(button&&!button.disabled)this.action(button).catch(error=>this.toast(error.message,true));
      },{signal:this.events.signal});
      root.addEventListener('submit',event=>{if(event.target.id==='export-filters'){event.preventDefault();this.loadPreview();}},{signal:this.events.signal});
      root.addEventListener('change',event=>this.change(event),{signal:this.events.signal});
      root.addEventListener('input',event=>{if(event.target.closest('#export-filters'))this.invalidate();},{signal:this.events.signal});
      await this.loadPreview();if(epoch===this.epoch)await this.tokenStatus();
    }catch(error){if(epoch===this.epoch)root.innerHTML=`<div class="empty-state"><h2>暂时无法打开导出中心</h2><p>${this.escape(error.message)}</p><a class="button" href="#library">返回文章库</a></div>`;}
  }
  render() {
    const e=value=>this.escape(value);
    this.root.innerHTML=`<section class="page-wrap export-page"><div class="page-intro"><div><span class="eyebrow">TAKE YOUR LEARNING WITH YOU</span><h1>把积累，带到下一站。</h1><p>导出生词、笔记和学习报告，接入你的学习工具。</p></div><span class="export-local-label">本机生成 · 自主保存</span></div>
      <div class="export-tabs" aria-label="导出内容">${['vocabulary','notes','report','reading_list'].map(key=>`<button type="button" data-export-action="dataset" data-dataset="${key}" aria-pressed="${key===this.dataset}">${e(this.options.datasets[key])}${key==='report'?'':`<small>${this.options.counts[key]}</small>`}</button>`).join('')}</div>
      <div class="export-layout"><section class="export-settings"><h2>导出设置</h2><form id="export-filters"></form>
        <div class="export-format-fields"><label>文件格式<select id="export-format"></select></label><p id="export-format-help" class="export-note"></p><label id="export-deck-label" hidden>Anki 牌组名称<input id="export-deck" maxlength="100" value="Margin 知刊"></label></div>
        <fieldset id="export-scope"><legend>导出范围</legend><label><input type="radio" name="export-scope" value="all" checked>所有匹配记录 <span id="export-all-count"></span></label><label><input type="radio" name="export-scope" value="selected">仅勾选记录 <span id="export-selected-count"></span></label></fieldset>
        <div class="export-actions"><button class="button primary" data-export-action="download" id="export-download" disabled>生成并下载</button>${this.button('cancel-download','取消生成','id="export-cancel" hidden')}${this.button('copy-list','复制阅读清单','id="export-copy" hidden disabled')}</div><p class="export-note">单次最多 5000 条、8 MB 文本。文件在浏览器下载目录保存。</p><p id="export-download-status" role="status" class="export-note"></p>
      </section><section class="export-preview"><div class="export-preview-head"><div><span class="eyebrow">REVIEW BEFORE EXPORT</span><h2>内容预览</h2></div>${this.button('select-loaded','勾选已加载','id="export-select-loaded"')}${this.button('clear-selection','清空勾选','id="export-clear-selection"')}</div><p id="export-preview-status" class="export-note" role="status"></p><div id="export-preview-content"></div>${this.button('more','加载更多','id="export-more" hidden')}<p id="export-preview-note" class="export-note">预览显示摘要；导出文件包含完整内容。</p></section></div>
      <details class="export-integrations"><summary><span>数据接口</span><small id="export-api-status">正在读取状态…</small></summary><p class="export-note">供本机脚本读取生词、笔记、阅读清单与学习报告。接口只读；令牌仅在生成时显示，重置或撤销后旧令牌立即失效。</p><div class="export-actions">${this.button('generate-token','生成只读令牌','id="export-generate-token" disabled')}${this.button('revoke-token','撤销令牌','id="export-revoke-token" hidden')}</div>
        <div id="export-token-result" hidden><label>新令牌<input id="export-api-token" type="password" readonly autocomplete="off" spellcheck="false"></label><div class="export-actions">${this.button('toggle-token','显示令牌')}${this.button('copy-token','复制令牌')}</div><p class="export-note">请在离开页面前保存到你使用的工具中；页面不会记住令牌。</p></div>
        <div class="export-api-guide"><h3>调用示例</h3><p>将 YOUR_TOKEN 替换为刚生成的令牌。</p><pre id="export-api-example"></pre>${this.button('copy-example','复制调用示例')}<div class="export-api-table"><table><thead><tr><th>接口</th><th>可用筛选</th></tr></thead><tbody><tr><td>vocabulary</td><td>query、mastery、article_id、from、to</td></tr><tr><td>notes</td><td>query、kind、tag、article_id、from、to</td></tr><tr><td>reading-list</td><td>query、read_status、from、to</td></tr><tr><td>report</td><td>from、to（YYYY-MM-DD）</td></tr><tr><td>schema</td><td>接口说明</td></tr></tbody></table></div><p class="export-note">路径前缀 /api/integrations/v1/。列表按 ID 升序，使用 limit（1–200）和 after 翻页；next_cursor 为空表示已到末页。服务运行时可用，不提供公网分享链接。</p></div>
      </details><textarea id="export-copy-fallback" class="export-copy-fallback" rows="7" readonly hidden aria-label="可手动复制的内容"></textarea></section>`;
    this.$('#export-api-example').textContent=`curl.exe -H "Authorization: Bearer YOUR_TOKEN" "${location.origin}/api/integrations/v1/vocabulary?limit=100"`;
    this.renderFilters();
  }
  renderFilters() {
    const o=this.options,d=this.dataset;
    const choices=values=>Object.entries(values).map(([key,label])=>`<option value="${key}">${this.escape(label)}</option>`).join('');
    const form=this.$('#export-filters');
    form.innerHTML=`${d==='report'?'':`<label>关键词<input name="query" type="search" maxlength="200" placeholder="搜索标题、内容或释义"></label>`}
      ${d==='vocabulary'?`<label>掌握度<select name="mastery"><option value="">全部掌握度</option>${choices(o.mastery)}</select></label>`:''}
      ${d==='notes'?`<label>笔记类型<select name="kind"><option value="">全部类型</option>${choices(o.kinds)}</select></label><label>标签<input name="tag" maxlength="24" placeholder="输入完整标签"></label>`:''}
      ${d==='reading_list'?'<label>阅读状态<select name="read_status"><option value="">全部文章</option><option value="unread">未读</option><option value="reading">阅读中</option><option value="completed">已读完</option></select></label>':''}
      <div class="export-date-row"><label>${d==='report'?'开始日期':'创建于（起）'}<input type="date" name="from" value="${d==='report'?o.default_from:''}" ${d==='report'?'required':''}></label><label>${d==='report'?'结束日期':'创建于（止）'}<input type="date" name="to" value="${d==='report'?o.today:''}" ${d==='report'?'required':''}></label></div>
      <button type="submit" class="button small">更新预览</button>`;
    const preferred={vocabulary:'csv',notes:'md',report:'pdf',reading_list:'html'}[d];
    this.$('#export-format').innerHTML=o.supported[d].map(key=>`<option value="${key}">${this.escape(o.formats[key])}</option>`).join('');
    this.$('#export-format').value=preferred;
    this.$('#export-deck').value='Margin 知刊::'+o.datasets[d];
    this.$('#export-scope').hidden=d==='report';this.$('#export-copy').hidden=d!=='reading_list';
    this.$('#export-select-loaded').hidden=this.$('#export-clear-selection').hidden=d==='report';
    this.$('#export-preview-note').hidden=d==='report';
    this.formatHelp();this.selected=new Set();this.scope='all';this.items=[];this.cursor=null;this.count=0;this.previewValid=false;
    this.$('#export-preview-content').innerHTML='';this.$('#export-more').hidden=true;
    this.$('[name="export-scope"][value="all"]').checked=true;
    this.updateButtons();
  }
  formatHelp() {
    const fmt=this.$('#export-format').value;
    this.$('#export-format-help').textContent={csv:'适合表格工具，使用 UTF-8 编码；文本公式会转为安全文本。',xlsx:'可直接用 Excel 打开，保留中文、筛选表头和完整长文本。',json:'保留字段、标签和原文位置，适合脚本处理。',md:'可在 Markdown 编辑器阅读，适合复制、归档与分享。',pdf:'适合阅读和打印，长内容会自动分页。',anki:'在 Anki 中导入 .apkg。生词生成问答卡；笔记仅导出「知识卡片」。同一记录使用稳定标识，便于重复导入更新。',html:'生成可独立打开的阅读清单文件，分享文件即可。包含文章信息和来源链接，不包含正文、笔记或个人阅读进度。',obsidian:'下载 ZIP，解压后复制到 Obsidian 仓库。包含 Markdown、标签、来源摘录和往返链接。',notion:'下载 ZIP，在 Notion 的「导入 → Markdown 与 CSV」中导入。也可单独导入 records.csv 为数据库。'}[fmt];
    this.$('#export-deck-label').hidden=fmt!=='anki';
  }
  filterValues() {return Object.fromEntries(new FormData(this.$('#export-filters')));}
  invalidate() {
    this.previewValid=false;++this.listVersion;
    this.$('#export-preview-status').textContent='筛选条件已改变，请更新预览。';this.updateButtons();
  }
  async loadPreview(more=false) {
    if(!this.$('#export-filters').reportValidity())return;
    const epoch=this.epoch,version=++this.listVersion;
    const filters=this.filterValues();
    if(!more){this.selected.clear();this.items=[];this.cursor=null;this.scope='all';this.$('[name="export-scope"][value="all"]').checked=true;}
    this.previewValid=false;this.updateButtons();this.$('#export-preview-status').textContent='正在读取符合条件的内容…';this.$('#export-more').disabled=true;
    try {
      const result=await this.api('/exports/preview',{method:'POST',body:{dataset:this.dataset,format:this.$('#export-format').value,filters,after:more?this.cursor||0:0}});
      if(this.epoch!==epoch||this.listVersion!==version)return;
      this.preview=result;this.filters=result.filters;this.count=result.count;this.cursor=result.next_cursor;this.previewValid=true;
      if(this.dataset==='report')this.reportPreview(result.report);
      else {this.items=more?[...this.items,...result.items]:result.items;this.renderItems();}
      this.$('#export-preview-status').textContent=this.dataset==='report'?`报告覆盖 ${result.count} 天（北京时间）`:`共 ${this.count} 条匹配，已加载 ${this.items.length} 条`;
      this.$('#export-more').hidden=!this.cursor;this.$('#export-more').disabled=false;this.updateButtons();
    }catch(error){if(this.epoch===epoch&&this.listVersion===version){this.$('#export-preview-status').textContent=error.message;this.$('#export-more').disabled=false;this.updateButtons();}}
  }
  renderItems() {
    this.$('#export-preview-content').innerHTML=this.items.map(item=>{
      const title=item.word||item.title,description=item.meaning||item.content||item.quote||item.subtitle||'暂无补充内容';
      const meta=item.mastery_label||item.label||[item.source,item.category,item.level].filter(Boolean).join(' · ');
      return `<label class="export-record"><input type="checkbox" data-export-id="${item.id}" ${this.selected.has(item.id)?'checked':''}><span><strong>${this.escape(title)}</strong><small>${this.escape(meta)}</small><span class="export-excerpt">${this.escape(description.slice(0,240))}${description.length>240?'…':''}</span></span></label>`;
    }).join('')||'<div class="empty-state"><h3>没有符合条件的内容</h3><p>调整筛选条件，或先添加生词和笔记。</p></div>';
  }
  reportPreview(report) {
    const m=report.metrics;
    this.$('#export-preview-content').innerHTML=`<div class="export-report-metrics">${[[Math.round(m.reading_seconds/60*10)/10,'阅读分钟'],[m.checked_days,'打卡天数'],[m.completed_count,'完成次数'],[m.longest_streak,'最长连续天数']].map(([value,label])=>`<div><strong>${value}</strong><span>${label}</span></div>`).join('')}</div><p class="export-note">${this.escape(report.definitions)}</p><div class="export-report-summary"><p>区间新增且仍保留：${m.added_vocabulary} 个生词，${m.added_notes} 条笔记。</p><p>当前掌握分布：初次遇见 ${report.mastery.new} · 正在学习 ${report.mastery.learning} · 已掌握 ${report.mastery.mastered}</p></div><details><summary>每日记录（${report.daily.length} 天）</summary><div class="export-daily"><table><thead><tr><th>日期</th><th>分钟</th><th>完成</th><th>打卡</th></tr></thead><tbody>${report.daily.map(d=>`<tr><td>${d.date}</td><td>${d.reading_minutes}</td><td>${d.completed_count}</td><td>${d.checked_in?'是':'否'}</td></tr>`).join('')}</tbody></table></div></details>`;
  }
  updateButtons() {
    const count=this.dataset==='report'?1:this.scope==='all'?this.count:this.selected.size;
    const disabled=!this.previewValid||!count||count>5000||!!this.fileRequest;
    this.$('#export-download').disabled=disabled;this.$('#export-copy').disabled=disabled;
    this.$('#export-all-count').textContent=`(${this.count||0})`;this.$('#export-selected-count').textContent=`(${this.selected?.size||0})`;
    this.$('#export-select-loaded').disabled=!this.previewValid||!this.items.length;
    this.$('#export-more').disabled=!this.previewValid;
    if(this.previewValid&&count>5000)this.$('#export-download-status').textContent='超过单次 5000 条限制，请缩小筛选范围或勾选部分记录。';
  }
  change(event) {
    if(event.target.id==='export-format') {
      this.formatHelp();
      if(this.dataset==='notes'&&event.target.value==='anki'){this.$('#export-filters').elements.kind.value='card';this.loadPreview();}
    }else if(event.target.closest('#export-filters')) {
      if(this.dataset==='notes'&&event.target.name==='kind'&&this.$('#export-format').value==='anki'&&event.target.value!=='card'){this.$('#export-format').value='md';this.formatHelp();}
      this.invalidate();
    }else if(event.target.name==='export-scope') {this.scope=event.target.value;this.updateButtons();}
    else if(event.target.dataset.exportId) {
      const id=Number(event.target.dataset.exportId);if(event.target.checked)this.selected.add(id);else this.selected.delete(id);
      this.scope='selected';this.$('[name="export-scope"][value="selected"]').checked=true;this.updateButtons();
    }
  }
  async download(copy=false) {
    if(!this.previewValid||this.fileRequest)return;
    const epoch=this.epoch,version=++this.downloadVersion;
    const spec={dataset:this.dataset,format:copy?'md':this.$('#export-format').value,filters:this.filters,deck_name:this.$('#export-deck').value};
    if(this.dataset!=='report'&&this.scope==='selected')spec.ids=[...this.selected];
    this.fileRequest=new AbortController();this.$('#export-cancel').hidden=false;this.$('#export-download-status').textContent='正在生成文件…';this.updateButtons();
    try {
      const response=await fetch('/api/exports/file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec),signal:this.fileRequest.signal});
      if(!response.ok){const problem=await response.json();throw new Error(problem.error||'生成失败，请重试。');}
      const blob=await response.blob();if(epoch!==this.epoch||version!==this.downloadVersion)return;
      if(copy){const content=await blob.text();if(epoch!==this.epoch||version!==this.downloadVersion)return;await this.copyText(content,'阅读清单已复制。');}
      else {
        const filename=response.headers.get('Content-Disposition')?.match(/filename="?([A-Za-z0-9_.-]+)"?/)?.[1]||'margin-export';
        const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
      }
      if(epoch===this.epoch&&version===this.downloadVersion)this.$('#export-download-status').textContent=copy?'清单已准备好。':`已生成 ${this.options.formats[spec.format]}，请查看浏览器下载记录。`;
    }catch(error){if(epoch===this.epoch&&version===this.downloadVersion)this.$('#export-download-status').textContent=error.name==='AbortError'?'已取消本次下载。':error instanceof TypeError?'无法连接本地服务；筛选和勾选已保留，可重试。':error.message;}
    finally{if(epoch===this.epoch&&version===this.downloadVersion){this.fileRequest=null;this.$('#export-cancel').hidden=true;this.updateButtons();}}
  }
  async copyText(value,message) {
    const epoch=this.epoch;
    try{await navigator.clipboard.writeText(value);if(epoch===this.epoch)this.toast(message);}
    catch {if(epoch!==this.epoch)return;const target=this.$('#export-copy-fallback');target.hidden=false;target.value=value;target.focus();target.select();this.toast('浏览器未允许自动复制，内容已选中，可手动复制。');}
  }
  async tokenStatus() {
    const epoch=this.epoch;
    try {
      const config=await this.api('/integrations/token');if(epoch!==this.epoch)return;
      this.tokenConfig=config;this.showTokenState();
    }catch(error){if(epoch===this.epoch){this.$('#export-api-status').textContent=error.message;this.$('#export-generate-token').disabled=false;}}
  }
  showTokenState() {
    const config=this.tokenConfig;
    this.$('#export-api-status').textContent=config.enabled?'已启用 · 令牌尾号 '+config.suffix:'未启用';
    this.$('#export-generate-token').textContent=config.enabled?'重置令牌':'生成只读令牌';this.$('#export-generate-token').disabled=false;
    this.$('#export-revoke-token').hidden=!config.enabled;
  }
  async tokenAction(revoke=false) {
    if(this.tokenBusy)return;
    if(this.tokenConfig?.enabled&&!confirm(revoke?'撤销令牌后，使用它的外部工具将不能读取数据。继续？':'重置后旧令牌立即失效。生成新令牌？'))return;
    const epoch=this.epoch;this.tokenBusy=true;this.$('#export-generate-token').disabled=true;this.$('#export-revoke-token').disabled=true;
    try {
      const result=await this.api('/integrations/token',{method:revoke?'DELETE':'POST',body:{}});if(epoch!==this.epoch)return;
      this.$('#export-copy-fallback').value='';this.$('#export-copy-fallback').hidden=true;
      this.token=result.token||'';this.tokenConfig=result;this.$('#export-api-token').value=this.token;this.$('#export-api-token').type='password';this.$('[data-export-action="toggle-token"]').textContent='显示令牌';
      this.$('#export-token-result').hidden=!this.token;this.showTokenState();this.toast(revoke?'数据接口令牌已撤销。':'只读令牌已生成。');
    }catch(error){if(epoch===this.epoch)this.$('#export-api-status').textContent=error.message;}
    finally{if(epoch===this.epoch){this.tokenBusy=false;this.$('#export-generate-token').disabled=false;this.$('#export-revoke-token').disabled=false;}}
  }
  async action(button) {
    const action=button.dataset.exportAction;
    if(action==='dataset') {
      if(button.dataset.dataset===this.dataset)return;
      ++this.downloadVersion;this.fileRequest?.abort();this.fileRequest=null;this.dataset=button.dataset.dataset;
      this.root.querySelectorAll('[data-export-action="dataset"]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
      this.$('#export-cancel').hidden=true;this.$('#export-download-status').textContent='';this.$('#export-copy-fallback').hidden=true;
      this.renderFilters();return this.loadPreview();
    }
    if(action==='more')return this.loadPreview(true);
    if(action==='select-loaded'||action==='clear-selection') {
      this.selected=action==='select-loaded'?new Set(this.items.map(i=>i.id)):new Set();this.scope='selected';this.$('[name="export-scope"][value="selected"]').checked=true;this.renderItems();this.updateButtons();return;
    }
    if(action==='download')return this.download();
    if(action==='copy-list')return this.download(true);
    if(action==='cancel-download'){this.fileRequest?.abort();return;}
    if(action==='generate-token')return this.tokenAction();
    if(action==='revoke-token')return this.tokenAction(true);
    if(action==='copy-token')return this.copyText(this.token,'令牌已复制。');
    if(action==='copy-example')return this.copyText(this.$('#export-api-example').textContent,'调用示例已复制。');
    if(action==='toggle-token'){const field=this.$('#export-api-token');field.type=field.type==='password'?'text':'password';button.textContent=field.type==='password'?'显示令牌':'隐藏令牌';}
  }
};
