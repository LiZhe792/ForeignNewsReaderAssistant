/* Local, source-verified annotations. Original word/sentence elements stay intact. */
'use strict';

window.MarginKnowledge = class MarginKnowledge {
  constructor({api, toast}) {
    Object.assign(this, {api, toast});
    this.kinds = {highlight:'高亮', bookmark:'段落书签', annotation:'页边批注', sentence:'句子收藏', grammar:'语法点', note:'主题笔记', expression:'表达素材', card:'知识卡片'};
    this.colors = {yellow:'黄色', green:'绿色', blue:'蓝色', pink:'粉色'};
    this.epoch = this.listVersion = this.readerVersion = 0;
    this.items = [];
    this.filters = {kind:'', tag:'', q:''};
    this.createDialogs();
    document.addEventListener('click', event => {
      const button = event.target.closest('[data-k-action]');
      if (button && !button.disabled) this.action(button).catch(error => this.toast(error.message, true));
    });
    document.addEventListener('selectionchange', () => this.captureSelection());
    document.addEventListener('pointerdown', event => {
      if (event.target.closest('#k-selection button')) event.preventDefault();
    });
    window.addEventListener('beforeunload', event => {
      if (this.isDirty() || this.mapDirty) { event.preventDefault(); event.returnValue = ''; }
    });
  }
  $(selector, root=document) { return root.querySelector(selector); }
  escape(value) { return String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  uuid() { return crypto.randomUUID(); }
  button(action, text, extra='') { return `<button type="button" class="button small" data-k-action="${action}" ${extra}>${text}</button>`; }
  kindOptions(onlyFree=false) { return Object.entries(this.kinds).filter(([key])=>!onlyFree || ['grammar','note','expression','card'].includes(key)).map(([key,label])=>`<option value="${key}">${label}</option>`).join(''); }

  createDialogs() {
    const holder = document.createElement('div');
    holder.innerHTML = `<dialog id="k-editor" class="modal k-modal" aria-labelledby="k-editor-heading">
      <div class="modal-heading"><div><span class="eyebrow">KEEP A THOUGHT</span><h2 id="k-editor-heading">整理记录</h2></div><button type="button" class="icon-button" data-k-action="close-editor" aria-label="关闭记录">✕</button></div>
      <form id="k-edit-form"><div class="k-form-row"><label>记录类型<select name="kind">${this.kindOptions()}</select></label><label>标记颜色<select name="color">${Object.entries(this.colors).map(([key,label])=>`<option value="${key}">${label}</option>`).join('')}</select></label></div>
        <label><span id="k-title-label">标题</span><input name="title" maxlength="200" required></label>
        <label><span id="k-content-label">我的笔记</span><textarea name="content" rows="5" maxlength="10000"></textarea></label>
        <label id="k-example-label">自拟例句 / 补充说明<textarea name="example" rows="2" maxlength="3000"></textarea></label>
        <label>笔记标签<input name="tags" maxlength="350" placeholder="用逗号分隔，例如：环境, 写作素材"><small class="k-muted">最多 12 个标签，每个不超过 24 字符。</small></label>
        <details id="k-source"><summary>原文摘录与位置</summary><div id="k-source-content"></div></details>
        <div id="k-edit-conflict" class="k-conflict" hidden><p>记录已在其他窗口修改。当前输入已保留，请比较后选择。</p><details><summary>查看最新版本</summary><pre></pre></details><div class="k-actions">${this.button('reload-item','载入最新版本')}${this.button('keep-item','保留当前内容并保存')}</div></div>
        <p id="k-edit-status" class="k-muted" role="status"></p><div class="k-actions"><button class="button primary" type="submit" id="k-save">保存记录</button>${this.button('close-editor','取消')}${this.button('delete-item','删除记录','id="k-delete"')}</div>
      </form></dialog>
      <dialog id="k-map" class="modal k-map-modal" aria-labelledby="k-map-heading">
        <div class="modal-heading"><div><span class="eyebrow">CONNECT YOUR IDEAS</span><h2 id="k-map-heading">思维导图</h2></div><button type="button" class="icon-button" data-k-action="close-map" aria-label="关闭导图">✕</button></div>
        <div class="k-map-top"><label>导图名称<input id="k-map-title" maxlength="200" required></label><div class="k-actions">${this.button('save-map','保存导图','id="k-map-save"')}${this.button('delete-map','删除导图','id="k-map-delete"')}</div></div>
        <p class="k-muted">点击节点编辑；添加分支、调整父节点，或关联一条知识记录。画布可横向滚动。</p>
        <div class="k-map-layout"><div id="k-map-canvas" class="k-map-canvas" tabindex="0" aria-label="思维导图画布"></div>
          <form id="k-node-form" class="k-node-editor"><label>节点内容<textarea id="k-node-text" maxlength="200" rows="3" required></textarea></label><label>父节点<select id="k-node-parent"></select></label><div class="k-actions">${this.button('add-node','添加子节点')}${this.button('delete-node','删除此分支')}</div>
            <label>关联知识记录<input id="k-link-search" type="search" maxlength="200" placeholder="搜索标题、摘录或标签"></label>${this.button('search-link','查找记录')}<div id="k-link-results"></div><div id="k-node-link" class="k-muted"></div>
          </form></div>
        <div id="k-map-conflict" class="k-conflict" hidden><p>导图已在其他窗口修改。当前内容已保留。</p><details><summary>查看最新版本</summary><pre></pre></details><div class="k-actions">${this.button('reload-map','载入最新版本')}${this.button('keep-map','保留当前导图并保存')}</div></div>
        <p id="k-map-status" class="k-muted" role="status"></p>
      </dialog>`;
    document.body.append(...holder.children);
    this.editor = this.$('#k-editor');
    this.mapDialog = this.$('#k-map');
    this.form = this.$('#k-edit-form');
    this.form.addEventListener('submit', event=>{ event.preventDefault(); this.saveItem(); });
    this.form.addEventListener('change', event=>{ if(event.target.name==='kind')this.fieldLabels(); });
    this.editor.addEventListener('cancel', event=>{event.preventDefault();this.closeEditor();});
    this.mapDialog.addEventListener('cancel', event=>{event.preventDefault();this.closeMap();});
    this.$('#k-node-form').addEventListener('submit', event=>{event.preventDefault();this.searchLinks();});
    this.mapDialog.addEventListener('input', event=>{
      if(event.target.id==='k-map-title') {this.map.title=event.target.value;this.dirtyMap();}
      if(event.target.id==='k-node-text') {
        this.selectedNode().text=event.target.value;this.dirtyMap();this.drawMap();
      }
    });
    this.$('#k-node-parent').addEventListener('change', event=>{
      this.selectedNode().parent=event.target.value;this.dirtyMap();this.drawMap();this.nodeEditor();
    });
  }

  leave() {
    ++this.epoch; ++this.listVersion; ++this.readerVersion;
    this.clearHighlights();
    this.root=null; this.reader=null; this.article=null; this.selection=null; this.items=[];
    this.editor.close(); this.mapDialog.close(); this.edit=null; this.map=null; this.mapDirty=false;
  }
  permitNavigation() {
    if(this.itemSaving || this.mapSaving){this.toast('正在保存，请稍候再切换页面。');return false;}
    return !(this.isDirty() || this.mapDirty) || confirm('离开此页会放弃尚未保存的记录或导图修改，继续离开？');
  }

  mountReader({root, article, reader}) {
    Object.assign(this,{root,article,reader});
    this.mode='reader'; this.paragraphFilter=null;
    const bar=document.createElement('div');bar.id='k-selection';bar.className='k-selection';bar.hidden=true;
    bar.setAttribute('aria-label','选中原文的标注工具');
    bar.innerHTML=`<span id="k-selection-label" class="k-muted"></span><div class="k-actions">${Object.entries(this.colors).map(([color,label])=>this.button('highlight',label,`data-color="${color}" title="${label}高亮"`)).join('')}${this.button('annotate','写批注')}${this.button('sentence','收藏句子')}<label class="k-inline-label">整理为<select id="k-selection-kind">${this.kindOptions(true)}</select></label>${this.button('organize','新建')}${this.button('clear-selection','✕','aria-label="关闭选区工具"')}</div>`;
    root.append(bar);
    const panel=document.createElement('aside');panel.id='k-margin';panel.className='k-margin';panel.hidden=true;
    panel.setAttribute('aria-label','原文标注与页边批注');
    panel.innerHTML=`<div class="k-row"><h2>本文标注</h2>${this.button('close-margin','关闭')}</div><div class="k-actions">${this.button('all-margins','全部标注')}${this.button('paragraph-note','批注此段')}${this.button('refresh-reader','刷新')}<a href="#knowledge" class="button small">知识整理 ↗</a></div><p id="k-margin-label" class="k-muted"></p><div id="k-margin-items"></div>`;
    root.querySelector('.reader-grid').append(panel);
    reader.paragraphs.forEach((p,index)=>{
      p.insertAdjacentHTML('beforeend',`<button type="button" class="k-paragraph-control k-bookmark" data-k-action="bookmark" data-paragraph="${index}" title="收藏第 ${index+1} 段" aria-label="收藏第 ${index+1} 段" aria-pressed="false" disabled>☆</button><button type="button" class="k-paragraph-control k-paragraph-notes" data-k-action="paragraph" data-paragraph="${index}" aria-label="第 ${index+1} 段标注" title="查看或添加本段标注">＋</button>`);
    });
    const epoch=this.epoch;
    this.refreshReader().then(()=>new Promise(resolve=>requestAnimationFrame(resolve))).then(()=>{
      if(this.epoch===epoch)return this.followHash();
    }).catch(error=>this.toast(error.message,true));
  }

  textNodes(p) {
    const walker=document.createTreeWalker(p,NodeFilter.SHOW_TEXT,{acceptNode:node=>node.parentElement.closest('button')?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT});
    const nodes=[];let node,offset=0;
    while((node=walker.nextNode())) {nodes.push({node,start:offset,end:offset+node.length});offset+=node.length;}
    return nodes;
  }
  rangeFor(anchor) {
    const p=this.reader?.paragraphs[anchor.paragraph];
    if(!p || p.dataset.plain.slice(anchor.start,anchor.end)!==anchor.quote)return null;
    const nodes=this.textNodes(p),first=nodes.find(n=>n.end>anchor.start),last=nodes.find(n=>n.end>=anchor.end);
    if(!first || !last)return null;
    const range=document.createRange();range.setStart(first.node,anchor.start-first.start);range.setEnd(last.node,anchor.end-last.start);
    return range.toString()===anchor.quote?range:null;
  }
  captureSelection() {
    if(!this.reader || this.editor.open || this.mapDialog.open || document.activeElement?.closest('#k-selection'))return;
    const selection=window.getSelection(),bar=this.$('#k-selection');
    if(!selection?.rangeCount || selection.isCollapsed) {this.selection=null;if(bar)bar.hidden=true;return;}
    const range=selection.getRangeAt(0),body=this.reader.body;
    if(!body.contains(range.startContainer)||!body.contains(range.endContainer)){this.selection=null;bar.hidden=true;return;}
    const anchors=[];
    this.reader.paragraphs.forEach((p,index)=>{
      if(!range.intersectsNode(p))return;
      const lengthTo=(node,offset)=>{
        const prefix=document.createRange();prefix.selectNodeContents(p);prefix.setEnd(node,offset);
        const fragment=prefix.cloneContents();fragment.querySelectorAll('button').forEach(b=>b.remove());return fragment.textContent.length;
      };
      let start=p.contains(range.startContainer)?lengthTo(range.startContainer,range.startOffset):0;
      let end=p.contains(range.endContainer)?lengthTo(range.endContainer,range.endOffset):p.dataset.plain.length;
      const raw=p.dataset.plain.slice(start,end); start+=raw.length-raw.trimStart().length;end-=raw.length-raw.trimEnd().length;
      if(end>start)anchors.push({paragraph:index,start,end,quote:p.dataset.plain.slice(start,end)});
    });
    if(!anchors.length || anchors.length>20 || anchors.reduce((n,a)=>n+Array.from(a.quote).length,0)>6000){this.selection=null;bar.hidden=true;return;}
    this.selection=anchors;bar.hidden=false;
    this.$('#k-selection-label').textContent=`选中 ${anchors.length} 段 · ${anchors.reduce((n,a)=>n+Array.from(a.quote).length,0)} 字符`;
  }
  paragraphAnchor(index) {
    const p=this.reader.paragraphs[index];
    const quote=Array.from(p.dataset.plain).slice(0,120).join('');
    return [{paragraph:index,start:0,end:quote.length,quote}];
  }
  clearHighlights() {
    if(window.CSS?.highlights)Object.keys(this.colors).forEach(color=>CSS.highlights.delete('knowledge-'+color));
    this.reader?.body.querySelectorAll('mark[data-k-mark]').forEach(mark=>{const parent=mark.parentNode;mark.replaceWith(...mark.childNodes);parent.normalize();});
  }
  drawHighlights() {
    if(!this.reader)return;
    this.clearHighlights();
    const highlights=this.items.filter(item=>item.kind==='highlight' && item.source_state==='available');
    if(window.CSS?.highlights && window.Highlight) {
      Object.keys(this.colors).forEach(color=>{
        const ranges=highlights.filter(i=>i.color===color).flatMap(i=>i.anchors.map(a=>this.rangeFor(a))).filter(Boolean);
        CSS.highlights.set('knowledge-'+color,new Highlight(...ranges));
      });
    } else {
      // Split each original text node once; newest highlight wins on overlaps.
      this.reader.paragraphs.forEach((p,index)=>{
        const anchors=highlights.flatMap(i=>i.anchors.filter(a=>a.paragraph===index).map(a=>({...a,color:i.color})));
        this.textNodes(p).forEach(({node,start,end})=>{
          const relevant=anchors.filter(a=>a.start<end&&a.end>start);if(!relevant.length)return;
          const points=[...new Set([start,end,...relevant.flatMap(a=>[Math.max(a.start,start),Math.min(a.end,end)])])].sort((a,b)=>a-b);
          const fragment=document.createDocumentFragment();
          for(let i=0;i<points.length-1;i++) {
            const from=points[i],to=points[i+1],color=relevant.filter(a=>a.start<=from&&a.end>=to).at(-1)?.color;
            const text=node.textContent.slice(from-start,to-start);
            if(color){const mark=document.createElement('mark');mark.dataset.kMark=color;mark.textContent=text;fragment.append(mark);}else fragment.append(document.createTextNode(text));
          }
          node.replaceWith(fragment);
        });
      });
    }
    this.reader.paragraphs.forEach((p,index)=>{
      const items=this.items.filter(i=>i.source_state==='available'&&i.anchors.some(a=>a.paragraph===index));
      const bookmarked=items.some(i=>i.kind==='bookmark'),button=p.querySelector('.k-bookmark');
      button.disabled=false;button.textContent=bookmarked?'★':'☆';button.setAttribute('aria-pressed',String(bookmarked));
      button.title=button.ariaLabel=`${bookmarked?'取消收藏':'收藏'}第 ${index+1} 段`;
      const notes=p.querySelector('.k-paragraph-notes');notes.textContent=items.length||'＋';notes.title=notes.ariaLabel=`第 ${index+1} 段 · ${items.length} 条标注`;
    });
  }
  async refreshReader() {
    if(!this.article)return;
    const epoch=this.epoch,version=++this.readerVersion,id=this.article.id;
    const result=await this.api(`/articles/${id}/knowledge`);
    if(epoch!==this.epoch || version!==this.readerVersion)return;
    this.items=result.items;this.drawHighlights();this.renderMargin();
  }
  renderMargin() {
    const target=this.$('#k-margin-items');if(!target)return;
    const items=this.items.filter(i=>this.paragraphFilter===null||i.anchors.some(a=>a.paragraph===this.paragraphFilter));
    this.$('#k-margin-label').textContent=`${this.paragraphFilter===null?'整篇文章':'第 '+(this.paragraphFilter+1)+' 段'} · ${items.length} 条记录`;
    target.innerHTML=items.map(i=>this.itemCard(i,true)).join('')||'<p class="k-empty">还没有记录。选中原文可高亮或收藏，也可以直接批注此段。</p>';
  }
  showMargin(index=null) {this.paragraphFilter=index;this.$('#k-margin').hidden=false;this.renderMargin();}
  sourceHTML(item) {
    const e=v=>this.escape(v);
    if(!item.anchors?.length)return '<p class="k-muted">独立整理的记录，未关联文章。</p>';
    const state=item.source_state||'available';
    return `<p class="k-muted">${e(item.article_title||this.article?.title)} · ${item.anchors.map(a=>'第 '+(a.paragraph+1)+' 段').join('、')}</p><blockquote class="k-quote">${e(item.anchors.map(a=>a.quote).join('\n\n'))}</blockquote>${state==='available'?this.button('source','定位原文',`data-id="${item.id||''}"`):`<p class="k-source-warning">${state==='deleted'?'原文已删除，摘录和笔记仍保留。':'原文已变化，无法准确定位；原始摘录仍保留。'}</p>`}`;
  }
  itemCard(item,compact=false) {
    const e=v=>this.escape(v),id=item.id;
    return `<article class="k-card" data-record="${id}" data-color="${item.color}"><div class="k-row"><span class="k-kind">${this.kinds[item.kind]}</span><span class="k-muted">${e(item.updated_at.slice(0,10))}</span></div><h3>${e(item.title)}</h3>
      ${item.kind==='card'?`<details class="k-answer"><summary>翻开答案</summary><div class="k-text">${e(item.content)}</div></details>`:item.content?`<div class="k-text ${compact?'k-clamp':''}">${e(item.content)}</div>`:''}
      ${item.example?`<details><summary>例句 / 补充</summary><div class="k-text">${e(item.example)}</div></details>`:''}
      ${item.quote?`<blockquote class="k-quote ${compact?'k-clamp':''}">${e(item.quote)}</blockquote>`:''}
      <div class="k-tags">${item.tags.map(tag=>`<button type="button" data-k-action="tag" data-tag="${e(tag)}"># ${e(tag)}</button>`).join('')}</div>
      ${item.article_title?`<p class="k-muted k-source-title">${e(item.article_title)}${item.source_state==='deleted'?' · 原文已删除':item.source_state==='changed'?' · 原文已变化':''}</p>`:''}
      <div class="k-actions">${this.button('edit-item','查看 / 编辑',`data-id="${id}"`)}${item.source_state==='available'?this.button('source','定位原文',`data-id="${id}"`):''}${item.kind!=='card'?this.button('make-card','制成卡片',`data-id="${id}"`):''}</div></article>`;
  }

  async followHash() {
    const parts=location.hash.slice(1).split('/');
    if(parts[2]==='knowledge' && /^\d+$/.test(parts[3]||''))await this.locate(Number(parts[3]));
  }
  async locate(id) {
    const epoch=this.epoch,{item}=await this.api('/knowledge/items/'+id);
    if(epoch!==this.epoch)return;
    if(item.source_state!=='available'){this.toast('原文已删除或变化，保留的摘录可在记录中查看。',true);return;}
    if(!this.closeEditor() || !this.closeMap())return;
    if(this.article?.id!==item.article_id){location.hash=`reader/${item.article_id}/knowledge/${id}`;return;}
    this.reader.jump(item.anchors[0].paragraph);
    this.showMargin(item.anchors[0].paragraph);
    this.reader.body.querySelectorAll('.k-source-focus').forEach(p=>p.classList.remove('k-source-focus'));
    item.anchors.forEach(a=>this.reader.paragraphs[a.paragraph].classList.add('k-source-focus'));
    const range=this.rangeFor(item.anchors[0]);
    if(range){
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
      const rect=range.getClientRects()[0],scroller=this.reader.scroller,viewport=scroller.getBoundingClientRect();
      if(rect && (rect.top<viewport.top+15 || rect.bottom>viewport.bottom-40))scroller.scrollTop+=rect.top-viewport.top-25;
      this.reader.remember();
    }
    const card=this.$(`[data-record="${id}"]`,this.$('#k-margin'));card?.classList.add('k-selected-card');
    card?.scrollIntoView({block:'nearest'});
  }

  async renderLibrary(root) {
    this.mode='library';this.root=root;
    root.innerHTML=`<section class="page-wrap k-library"><div class="page-intro"><div><span class="eyebrow">COLLECT · CONNECT · CREATE</span><h1>把读过的，留在自己的知识里。</h1><p>从一处高亮到一张知识卡片，让每次阅读有所积累。</p></div></div>
      <div class="k-library-tools"><label class="k-inline-label">新建<select id="k-new-kind">${this.kindOptions(true)}</select></label>${this.button('new-item','新建记录')}${this.button('new-map','新建思维导图')}</div>
      <div class="k-tabs" aria-label="知识整理分类"><button type="button" data-k-action="items-tab" class="active">笔记与收藏</button><button type="button" data-k-action="maps-tab">思维导图</button></div>
      <form id="k-filter-form" class="k-filters"><label>记录类型<select name="kind"><option value="">所有类型</option>${this.kindOptions()}</select></label><label>笔记标签<input name="tag" list="k-tag-options" maxlength="24" placeholder="全部标签"></label><datalist id="k-tag-options"></datalist><label class="k-search-label">关键词<input name="q" type="search" maxlength="200" placeholder="搜索标题、正文、例句、摘录"></label><button type="submit" class="button small">筛选</button>${this.button('reset-filter','重置')}</form>
      <p id="k-list-status" class="k-muted" role="status"></p><div id="k-list" class="k-list"></div><div class="k-actions">${this.button('more-items','加载更多','id="k-more" hidden')}${this.button('refresh-list','刷新列表')}</div></section>`;
    this.$('#k-new-kind').value='note';
    const form=this.$('#k-filter-form');Object.entries(this.filters).forEach(([key,value])=>form.elements[key].value=value);
    form.addEventListener('submit',event=>{event.preventDefault();this.filters=Object.fromEntries(new FormData(form));this.loadList();});
    this.listTab='items';await this.loadList();
  }
  async loadList(more=false) {
    if(this.mode!=='library' || !this.root)return;
    const epoch=this.epoch,version=++this.listVersion,tab=this.listTab;
    const params=new URLSearchParams(tab==='items'?this.filters:{});
    if(more && this.cursor)params.set('before',this.cursor);
    this.$('#k-list-status').textContent='正在读取…';this.$('#k-more').disabled=true;
    this.$('#k-list').inert=true;this.$('#k-list').setAttribute('aria-busy','true');
    try {
      const result=await this.api('/knowledge/'+(tab==='items'?'items':'maps')+'?'+params);
      if(epoch!==this.epoch||version!==this.listVersion)return;
      const records=result[tab];this.listItems=more?[...this.listItems,...records]:records;
      this.cursor=result.next_cursor;
      this.$('#k-list').innerHTML=this.listItems.map(item=>tab==='items'?this.itemCard(item):`<article class="k-card"><span class="k-kind">思维导图</span><h3>${this.escape(item.title)}</h3><p class="k-muted">更新于 ${this.escape(item.updated_at.slice(0,10))}</p>${this.button('open-map','打开导图',`data-id="${item.id}"`)}</article>`).join('')||'<div class="k-empty">这里还没有记录。阅读时选中原文开始标注，或新建一条主题笔记。</div>';
      this.$('#k-list-status').textContent=`已显示 ${this.listItems.length} 条${this.cursor?'，可加载更多':''}`;
      this.$('#k-more').hidden=!this.cursor;this.$('#k-more').disabled=false;
      if(tab==='items') {
        const options=await this.api('/knowledge/options');
        if(epoch===this.epoch&&version===this.listVersion)this.$('#k-tag-options').innerHTML=options.tags.map(tag=>`<option value="${this.escape(tag)}"></option>`).join('');
      }
    } catch(error) {if(epoch===this.epoch&&version===this.listVersion){this.$('#k-list-status').textContent=error.message+' 可点击刷新列表重试。';this.$('#k-more').disabled=false;}}
    finally {if(epoch===this.epoch&&version===this.listVersion&&this.$('#k-list')){this.$('#k-list').inert=false;this.$('#k-list').removeAttribute('aria-busy');}}
  }

  editSnapshot() {return JSON.stringify(Object.fromEntries(new FormData(this.form)));}
  isDirty() {return this.editor.open && this.editSnapshot()!==this.cleanSnapshot;}
  openEditor(item=null,kind='note',anchors=null) {
    if(this.editor.open && this.isDirty() && !confirm('放弃当前尚未保存的修改？'))return;
    this.returnAnchor=this.reader?.captureAnchor();
    this.edit=item?{...item}:{kind,anchors:anchors?structuredClone(anchors):[],article_id:anchors?this.article.id:null,article_title:anchors?this.article.title:'',request_id:this.uuid(),title:'',content:'',example:'',tags:[],color:'yellow'};
    if(!item && anchors)this.edit.title=Array.from(anchors.map(a=>a.quote).join(' ')).slice(0,100).join('');
    const f=this.form.elements;
    f.kind.innerHTML=this.kindOptions(!this.edit.anchors.length);f.kind.value=this.edit.kind;f.kind.disabled=!!item;
    ['title','content','example','color'].forEach(key=>f[key].value=this.edit[key]||'');f.tags.value=this.edit.tags.join(', ');
    this.$('#k-source-content').innerHTML=this.sourceHTML(this.edit);this.$('#k-source').open=false;
    // A not-yet-saved selection has no record ID to navigate to.
    if(!item)this.$('[data-k-action="source"]',this.$('#k-source-content'))?.remove();
    this.$('#k-delete').hidden=!item;this.$('#k-edit-conflict').hidden=true;this.$('#k-edit-status').textContent='';
    this.fieldLabels();this.cleanSnapshot=this.editSnapshot();
    if(!this.editor.open)this.editor.showModal();
    f.title.focus({preventScroll:true});
  }
  fieldLabels() {
    const kind=this.form.elements.kind.value;
    this.$('#k-editor-heading').textContent=(this.edit.id?'编辑':'新建')+this.kinds[kind];
    this.$('#k-title-label').textContent=kind==='card'?'卡片正面 · 问题':kind==='expression'?'表达 / 短语':'标题';
    this.$('#k-content-label').textContent=kind==='card'?'卡片背面 · 答案':kind==='expression'?'释义与使用场景':kind==='grammar'?'语法规则与分析':kind==='annotation'?'页边批注':'我的笔记';
    this.form.elements.content.required=['note','annotation','card'].includes(kind);
  }
  closeEditor(force=false) {
    if(this.itemSaving)return false;
    if(!force && this.isDirty() && !confirm('放弃尚未保存的记录修改？'))return false;
    const wasOpen=this.editor.open;this.editor.close();this.edit=null;
    if(wasOpen && !force && this.reader){this.reader.scroller.focus({preventScroll:true});this.reader.restoreAnchor(this.returnAnchor);}
    return true;
  }
  async saveItem() {
    if(this.itemSaving || !this.form.reportValidity())return;
    const edit=this.edit,epoch=this.epoch,f=this.form.elements;
    const body={title:f.title.value,content:f.content.value,example:f.example.value,color:f.color.value,tags:f.tags.value.split(/[,，\n]/).map(v=>v.trim()).filter(Boolean)};
    if(edit.id)body.version=edit.version;
    else Object.assign(body,{kind:f.kind.value,article_id:edit.article_id,anchors:edit.anchors,request_id:edit.request_id});
    const controls=[...this.form.querySelectorAll('input,textarea,select')].map(input=>[input,input.disabled]);
    controls.forEach(([input])=>input.disabled=true);
    this.itemSaving=true;this.$('#k-save').disabled=true;this.$('#k-edit-status').textContent='正在保存…';
    let saved;
    try {
      saved=await this.api('/knowledge/items'+(edit.id?'/'+edit.id:''),{method:edit.id?'PATCH':'POST',body});
      if(epoch!==this.epoch || this.edit!==edit)return;
    } catch(error) {
      if(epoch===this.epoch && this.edit===edit) {
        this.$('#k-edit-status').textContent=error.message;
        if(error.status===409 && edit.id) {
          try {const {item}=await this.api('/knowledge/items/'+edit.id);if(this.edit!==edit)return;this.latestItem=item;this.$('#k-edit-conflict').hidden=false;this.$('pre',this.$('#k-edit-conflict')).textContent=[item.title,item.content,item.example,'颜色：'+this.colors[item.color],'标签：'+item.tags.join(', ')].join('\n\n');}catch(e){this.$('#k-edit-status').textContent=e.message;}
        }
      }
    } finally {controls.forEach(([input,disabled])=>input.disabled=disabled);this.itemSaving=false;this.$('#k-save').disabled=false;}
    if(saved && epoch===this.epoch && this.edit===edit) {
      this.closeEditor(true);this.toast(saved.duplicate?'此处已经收藏，可在标注列表编辑。':'记录已保存在本机。');
      try{await this.refreshCurrent();}catch(error){this.toast('记录已保存，刷新列表失败：'+error.message,true);}
    }
  }
  async refreshCurrent() {if(this.mode==='reader')await this.refreshReader();else if(this.mode==='library')await this.loadList();}
  async quickSave(kind,color='yellow',anchors=this.selection) {
    if(!anchors?.length){this.toast('请先在正文中选中要保存的内容。');return;}
    if(this.quickSaving)return;this.quickSaving=true;
    const epoch=this.epoch;
    try {
      const {duplicate}=await this.api('/knowledge/items',{method:'POST',body:{kind,color,article_id:this.article.id,anchors,title:Array.from(anchors.map(a=>a.quote).join(' ')).slice(0,100).join(''),request_id:this.uuid()}});
      if(epoch!==this.epoch)return;
      window.getSelection()?.removeAllRanges();this.toast(duplicate?'这处原文已收藏，可在标注列表编辑。':this.kinds[kind]+'已保存。');await this.refreshReader();
    }finally{this.quickSaving=false;}
  }

  async openMap(id=null) {
    const epoch=this.epoch;
    const map=id?(await this.api('/knowledge/maps/'+id)).map:{title:'新的知识导图',nodes:[{id:'root',parent:null,text:'中心主题',item_id:null}],request_id:this.uuid()};
    if(epoch!==this.epoch)return;
    this.map=map;this.nodeId='root';this.collapsed=new Set();this.mapDirty=false;
    this.$('#k-map-title').value=map.title;this.$('#k-map-delete').hidden=!map.id;this.$('#k-map-conflict').hidden=true;
    this.$('#k-map-status').textContent=id?'已加载本机保存的导图。':'新导图尚未保存。';
    this.drawMap();this.nodeEditor();if(!this.mapDialog.open)this.mapDialog.showModal();
  }
  selectedNode() {return this.map.nodes.find(n=>n.id===this.nodeId);}
  descendants(id) {const ids=new Set([id]);let previous;do{previous=ids.size;this.map.nodes.forEach(n=>{if(ids.has(n.parent))ids.add(n.id);});}while(ids.size!==previous);return ids;}
  dirtyMap() {this.mapDirty=true;this.$('#k-map-status').textContent='有未保存的修改。';}
  drawMap() {
    const e=v=>this.escape(v);
    const branch=node=>{
      const children=this.map.nodes.filter(n=>n.parent===node.id),folded=this.collapsed.has(node.id);
      return `<li><div class="k-node-wrap"><button type="button" class="k-node ${node.id===this.nodeId?'selected':''}" data-k-action="select-node" data-node="${node.id}" aria-pressed="${node.id===this.nodeId}">${e(node.text||'未命名节点')}${node.item_id?'<small>↗ 关联记录</small>':''}</button>${children.length?`<button type="button" class="k-fold" data-k-action="fold-node" data-node="${node.id}" aria-label="${folded?'展开':'折叠'}分支" aria-expanded="${!folded}">${folded?'+':'−'}</button>`:''}</div>${children.length&&!folded?'<ul>'+children.map(branch).join('')+'</ul>':''}</li>`;
    };
    this.$('#k-map-canvas').innerHTML='<ul class="k-tree">'+branch(this.map.nodes.find(n=>n.id==='root'))+'</ul>';
  }
  nodeEditor() {
    const node=this.selectedNode(),excluded=this.descendants(node.id);
    this.$('#k-node-text').value=node.text;
    const parent=this.$('#k-node-parent');parent.disabled=node.id==='root';
    parent.innerHTML=node.id==='root'?'<option>中心节点</option>':this.map.nodes.filter(n=>!excluded.has(n.id)).map(n=>`<option value="${n.id}">${this.escape(n.text)}</option>`).join('');
    if(node.parent)parent.value=node.parent;
    this.$('[data-k-action="delete-node"]').disabled=node.id==='root';
    this.$('[data-k-action="add-node"]').disabled=this.map.nodes.length>=100;
    this.$('#k-link-results').replaceChildren();this.$('#k-link-search').value='';
    this.$('#k-node-link').innerHTML=node.item_id?`已关联记录 #${node.item_id}<div class="k-actions">${this.button('view-linked','查看关联记录',`data-id="${node.item_id}"`)}${this.button('unlink-node','取消关联')}</div>`:'尚未关联记录。';
  }
  async searchLinks() {
    const map=this.map,nodeId=this.nodeId,query=this.$('#k-link-search').value;
    try {
      const {items,next_cursor}=await this.api('/knowledge/items?q='+encodeURIComponent(query));
      if(this.map!==map || this.nodeId!==nodeId || this.$('#k-link-search').value!==query)return;
      this.$('#k-link-results').innerHTML=items.map(i=>this.button('link-node',this.escape(this.kinds[i.kind]+' · '+i.title),`data-id="${i.id}"`)).join('')||'<p class="k-muted">没有匹配记录。</p>';
      if(next_cursor)this.$('#k-link-results').insertAdjacentHTML('beforeend','<p class="k-muted">仅列出前 50 条，请输入更具体的关键词。</p>');
    }catch(error){if(this.map===map)this.$('#k-link-results').textContent=error.message;}
  }
  async saveMap() {
    if(this.mapSaving || !this.map)return;
    const map=this.map;
    if(!map.title.trim() || map.nodes.some(n=>!n.text.trim())){this.$('#k-map-status').textContent='请填写导图名称和每个节点的内容。';return;}
    const body={title:map.title,nodes:structuredClone(map.nodes),...(map.id?{version:map.version}:{request_id:map.request_id})};
    const snapshot=JSON.stringify([map.title,map.nodes]);
    this.mapSaving=true;this.$('#k-map-save').disabled=true;this.$('#k-map-status').textContent='正在保存…';
    try {
      const result=await this.api('/knowledge/maps'+(map.id?'/'+map.id:''),{method:map.id?'PATCH':'POST',body});
      if(this.map!==map)return;
      Object.assign(map,{id:result.map.id,version:result.map.version});
      this.mapDirty=JSON.stringify([map.title,map.nodes])!==snapshot;
      this.$('#k-map-status').textContent=this.mapDirty?'上次内容已保存；还有新的修改待保存。':'导图已保存在本机。';
      this.$('#k-map-delete').hidden=false;this.$('#k-map-conflict').hidden=true;
      if(this.mode==='library'&&this.listTab==='maps')await this.loadList();
    }catch(error){
      if(this.map!==map)return;
      this.$('#k-map-status').textContent=error.message;
      if(error.status===409 && map.id)try {
        const result=await this.api('/knowledge/maps/'+map.id);if(this.map!==map)return;
        this.latestMap=result.map;this.$('#k-map-conflict').hidden=false;
        this.$('pre',this.$('#k-map-conflict')).textContent=result.map.title+'\n\n'+result.map.nodes.map(n=>(n.parent?result.map.nodes.find(p=>p.id===n.parent).text+' → ':'中心：')+n.text+(n.item_id?' · 关联记录 #'+n.item_id:'')).join('\n');
      }catch(e){this.$('#k-map-status').textContent=e.message;}
    }finally{this.mapSaving=false;this.$('#k-map-save').disabled=false;}
  }
  closeMap(force=false) {
    if(this.mapSaving)return false;
    if(!force && this.mapDirty && !confirm('放弃尚未保存的导图修改？'))return false;
    this.mapDialog.close();this.map=null;this.mapDirty=false;return true;
  }

  async action(button) {
    const action=button.dataset.kAction,id=Number(button.dataset.id),paragraph=Number(button.dataset.paragraph);
    if(action==='highlight')return this.quickSave('highlight',button.dataset.color);
    if(action==='sentence')return this.quickSave('sentence');
    if(action==='annotate'||action==='organize') {if(this.selection)this.openEditor(null,action==='annotate'?'annotation':this.$('#k-selection-kind').value,this.selection);return;}
    if(action==='clear-selection'){window.getSelection()?.removeAllRanges();return;}
    if(action==='margin'||action==='all-margins'){this.showMargin();return;}
    if(action==='close-margin'){this.$('#k-margin').hidden=true;return;}
    if(action==='paragraph'){this.showMargin(paragraph);return;}
    if(action==='paragraph-note'){const index=this.paragraphFilter??this.reader.current;this.openEditor(null,'annotation',this.paragraphAnchor(index));return;}
    if(action==='refresh-reader')return this.refreshReader();
    if(action==='bookmark') {
      button.disabled=true;
      try {
        const existing=this.items.find(i=>i.kind==='bookmark'&&i.anchors[0].paragraph===paragraph);
        if(existing){await this.api('/knowledge/items/'+existing.id,{method:'DELETE',body:{version:existing.version}});await this.refreshReader();}
        else await this.quickSave('bookmark','yellow',this.paragraphAnchor(paragraph));
      }finally{if(button.isConnected)button.disabled=false;}return;
    }
    if(action==='source')return this.locate(id);
    if(action==='edit-item'||action==='make-card') {
      const epoch=this.epoch,{item}=await this.api('/knowledge/items/'+id);if(epoch!==this.epoch)return;
      if(action==='edit-item')this.openEditor(item);
      else {
        this.openEditor(null,'card');
        Object.assign(this.edit,{anchors:item.source_state==='available'?item.anchors:[],article_id:item.source_state==='available'?item.article_id:null,article_title:item.article_title});
        this.form.elements.title.value=item.title;this.form.elements.content.value=item.content||item.quote;this.form.elements.tags.value=item.tags.join(', ');
        this.$('#k-source-content').innerHTML=this.sourceHTML(this.edit);this.$('[data-k-action="source"]',this.$('#k-source-content'))?.remove();
      }return;
    }
    if(action==='close-editor'){this.closeEditor();return;}
    if(action==='delete-item') {
      const item=this.edit;if(!item?.id || this.itemSaving || !confirm('删除这条记录？关联导图中的节点文字会保留。'))return;
      const epoch=this.epoch;
      await this.api('/knowledge/items/'+item.id,{method:'DELETE',body:{version:item.version}});
      if(this.epoch===epoch){if(this.edit===item)this.closeEditor(true);await this.refreshCurrent();}return;
    }
    if(action==='reload-item'){if(confirm('用最新版本替换当前未保存的内容？')){this.cleanSnapshot=this.editSnapshot();this.openEditor(this.latestItem);}return;}
    if(action==='keep-item'){this.edit.version=this.latestItem.version;return this.saveItem();}
    if(action==='new-item'){this.openEditor(null,this.$('#k-new-kind').value);return;}
    if(action==='tag') {
      this.filters={kind:'',tag:button.dataset.tag,q:''};
      if(this.mode!=='library'){location.hash='knowledge';return;}
      this.$('#k-filter-form').elements.tag.value=button.dataset.tag;this.$('#k-filter-form').elements.kind.value='';this.$('#k-filter-form').elements.q.value='';return this.loadList();
    }
    if(action==='reset-filter'){this.filters={kind:'',tag:'',q:''};this.$('#k-filter-form').reset();return this.loadList();}
    if(action==='items-tab'||action==='maps-tab') {
      this.listTab=action==='items-tab'?'items':'maps';this.$('#k-filter-form').hidden=this.listTab==='maps';
      document.querySelectorAll('.k-tabs button').forEach(b=>b.classList.toggle('active',b===button));return this.loadList();
    }
    if(action==='more-items')return this.loadList(true);
    if(action==='refresh-list')return this.loadList();
    if(action==='new-map')return this.openMap();
    if(action==='open-map')return this.openMap(id);
    if(action==='close-map'){this.closeMap();return;}
    if(action==='save-map')return this.saveMap();
    if(action==='select-node'){this.nodeId=button.dataset.node;this.drawMap();this.nodeEditor();return;}
    if(action==='fold-node'){const node=button.dataset.node;if(this.collapsed.has(node))this.collapsed.delete(node);else this.collapsed.add(node);this.drawMap();return;}
    if(action==='add-node') {
      if(this.map.nodes.length>=100)return;
      const parent=this.nodeId;this.collapsed.delete(parent);this.nodeId=this.uuid();this.map.nodes.push({id:this.nodeId,parent,text:'新分支',item_id:null});this.dirtyMap();this.drawMap();this.nodeEditor();this.$('#k-node-text').focus();this.$('#k-node-text').select();return;
    }
    if(action==='delete-node') {
      if(this.nodeId==='root'||!confirm('删除此节点及其全部子节点？'))return;
      const parent=this.selectedNode().parent,ids=this.descendants(this.nodeId);this.map.nodes=this.map.nodes.filter(n=>!ids.has(n.id));this.nodeId=parent;this.dirtyMap();this.drawMap();this.nodeEditor();return;
    }
    if(action==='search-link')return this.searchLinks();
    if(action==='link-node'||action==='unlink-node'){this.selectedNode().item_id=action==='link-node'?id:null;this.dirtyMap();this.drawMap();this.nodeEditor();return;}
    if(action==='view-linked'){const map=this.map,epoch=this.epoch,{item}=await this.api('/knowledge/items/'+id);if(this.map===map&&this.epoch===epoch)this.openEditor(item);return;}
    if(action==='delete-map') {
      if(!this.map?.id || this.mapSaving || !confirm('删除这张导图？知识库中的记录会保留。'))return;
      await this.api('/knowledge/maps/'+this.map.id,{method:'DELETE',body:{version:this.map.version}});this.closeMap(true);await this.loadList();return;
    }
    if(action==='reload-map'){if(confirm('用最新导图替换当前未保存的内容？'))await this.openMap(this.map.id);return;}
    if(action==='keep-map'){this.map.version=this.latestMap.version;return this.saveMap();}
  }
};
