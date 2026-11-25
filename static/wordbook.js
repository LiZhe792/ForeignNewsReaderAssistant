/* Local wordbooks and rich entries. No build step or external media service. */
'use strict';
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {tag:'标签',group:'分组',book:'词书',word:'单词',phrase:'短语',pattern:'句型'};
  const button = (action, label, extra = '') => `<button type="button" class="button small" data-wb-action="${action}" ${extra}>${label}</button>`;
  class Wordbook {
    constructor({root,api,toast,onChange,confirm}) {
      Object.assign(this,{root,api,toast,onChange,confirm});
      this.dialog=document.querySelector('#wordbook-dialog');this.abort=new AbortController();this.selected=new Set();this.page=0;this.filters={query:'',collection:'',kind:'',status:''};
      for(const node of [root,this.dialog]) for(const type of ['click','input','change','submit']) node.addEventListener(type,event=>this.handle(event),{signal:this.abort.signal});
    }
    destroy(){clearTimeout(this.filterTimer);this.dead=true;this.abort.abort();this.dialog.close();}
    async request(path,method='GET',body){return this.api('/wordbook'+path,{method,body,signal:this.abort.signal});}
    async render(rowsOnly=false){
      const version=this.loadVersion=(this.loadVersion||0)+1;
      const result=await this.request('?'+new URLSearchParams({...this.filters,limit:50,offset:this.page*50}));
      if(this.dead||version!==this.loadVersion)return;
      Object.assign(this,result);
      if(!this.entries.length&&this.page>0){this.page--;return this.render(rowsOnly);}
      this.selected=new Set([...this.selected].filter(id=>this.entries.some(e=>e.id===id)));
      if(rowsOnly)this.rows();else this.draw();
    }
    async changed(){await this.render();await this.onChange?.();}
    filtered(){return this.entries;}
    draw(){
      const collectionsOpen=this.root.querySelector('.wb-collections')?.open;
      this.root.innerHTML=`<div class="page-wrap wb-page"><div class="page-intro"><div><span class="eyebrow">A PERSONAL WORD GARDEN</span><h1>让遇见的词，成为自己的词。</h1><p>词书、原句与卡片，一起留下；按记忆的节奏再相遇。</p></div><a class="button primary" href="#review">开始复习</a></div>
      <div class="summary-strip">${[['收藏词条',this.counts.entries],['原文语境',this.counts.contexts],['自建词书',this.collections.filter(c=>c.kind==='book').length],['困难词',this.counts.difficult]].map(([label,value])=>`<div class="summary-tile"><span>${label}</span><strong>${value}</strong></div>`).join('')}</div>
      <div class="wb-toolbar">${button('add','添加词条')}${this.undoToken?button('undo','撤销刚才删除'):''}${button('import','批量导入')}${button('export','导出词条')}${button('merge','合并词形')}<span id="wb-selected" role="status"></span></div>
      <div class="wb-filters"><label>搜索<input id="wb-search" placeholder="单词、词形、释义或标签" value="${esc(this.filters.query)}"></label><label>分类<select id="wb-collection"><option value="">全部分类</option>${this.collectionOptions(this.filters.collection)}</select></label><label>词条类型<select id="wb-kind"><option value="">全部类型</option>${['word','phrase','pattern'].map(k=>`<option value="${k}" ${this.filters.kind===k?'selected':''}>${labels[k]}</option>`).join('')}</select></label><label>学习状态<select id="wb-status"><option value="">全部状态</option>${Object.entries({new:'初次遇见',learning:'正在学习',mastered:'已掌握',difficult:'困难词',paused:'已暂停复习'}).map(([k,v])=>`<option value="${k}" ${this.filters.status===k?'selected':''}>${v}</option>`).join('')}</select></label></div>
      <div id="wb-rows"></div><details class="wb-collections" ${collectionsOpen?'open':''}><summary>管理标签、分组与词书</summary><div class="wb-toolbar"><select id="wb-new-kind" aria-label="新分类类型"><option value="book">词书</option><option value="group">分组</option><option value="tag">标签</option></select><input id="wb-new-name" maxlength="60" aria-label="新分类名称" placeholder="例如：经济外刊">${button('collection-add','创建')}</div><div class="wb-collection-list">${this.collections.map(c=>`<div><span>${labels[c.kind]}</span><input data-collection-name="${c.id}" aria-label="${esc(c.name)} 名称" maxlength="60" value="${esc(c.name)}">${button('collection-rename','更名',`data-id="${c.id}"`)}${button('collection-delete','删除分类',`data-id="${c.id}"`)}</div>`).join('')||'<p>创建一个词书，开始归类。</p>'}</div></details><p class="wb-hint">手动掌握度与复习进度分别保存；暂停和困难标记不会删除词条。单次导入、导出最多 500 条。</p></div>`;
      this.rows();
    }
    collectionOptions(current=''){return this.collections.map(c=>`<option value="${c.id}" ${String(current)===String(c.id)?'selected':''}>${labels[c.kind]} · ${esc(c.name)}</option>`).join('');}
    rows(){
      const list=this.filtered(),pages=Math.max(1,Math.ceil(this.total/50));this.page=Math.min(this.page,pages-1);
      this.root.querySelector('#wb-selected').textContent=`已勾选 ${this.selected.size} 条`;
      this.root.querySelector('#wb-rows').innerHTML=`<div class="wb-list">${list.map(e=>`<article class="wb-row"><label class="wb-select"><input type="checkbox" data-wb-select="${e.id}" aria-label="选择 ${esc(e.word)}" ${this.selected.has(e.id)?'checked':''}></label><div><button class="wb-word" data-wb-action="edit" data-id="${e.id}">${esc(e.word)}</button><small>${esc(e.phonetic)} · ${labels[e.entry_kind]}</small><div class="wb-chips">${e.collections.map(c=>`<span>${esc(c.name)}</span>`).join('')}${e.aliases.length?`<span>词形 ${esc(e.aliases.join(', '))}</span>`:''}</div></div><div><p class="wb-meaning">${esc(e.meaning)}</p><small>${e.senses.length} 个义项 · ${e.contexts.length} 个原句 · ${e.cards.length} 张卡片</small><small>${e.schedule.suspended?'复习已暂停':e.schedule.state==='new'?'尚未开始复习':'下次：'+this.date(e.schedule.due_at)}${e.schedule.leech?' · 困难词':''}</small></div><div class="wb-row-actions">${button('edit','编辑',`data-id="${e.id}"`)}${button('pause',e.schedule.suspended?'恢复复习':'暂停',`data-id="${e.id}"`)}${button('difficult',e.schedule.leech?'移出困难词':'标为困难',`data-id="${e.id}"`)}${button('delete','删除',`data-id="${e.id}"`)}</div></article>`).join('')||'<div class="empty-state"><h2>这里还没有词条</h2><p>添加单词、短语或句型，或调整筛选条件。</p></div>'}</div><div class="wb-pagination">${button('previous','上一页',this.page===0?'disabled':'')}<span>${list.length} 条 · ${this.page+1} / ${pages} 页</span>${button('next','下一页',this.page+1>=pages?'disabled':'')}${button('select-page','勾选本页')}${button('clear-selection','清空勾选')}</div>`;
    }
    date(value){return window.MarginPlatform?.date ? MarginPlatform.date(value,true) : new Intl.DateTimeFormat('zh-CN',{timeZone:window.MarginPlatform?.preferences?.timezone||'Asia/Shanghai',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value));}
    open(title,content){this.panelGeneration=(this.panelGeneration||0)+1;this.dialog.innerHTML=`<div class="modal-heading"><h2>${esc(title)}</h2>${button('close','关闭')}</div>${content}<p id="wb-error" class="form-error" role="alert"></p>`;if(!this.dialog.open)this.dialog.showModal();}
    error(error){if(this.dead)return;const target=this.dialog.open?this.dialog.querySelector('#wb-error'):null;if(target)target.textContent=error.message;else this.toast(error.message,true);}
    newDraft(entry){return entry?structuredClone(entry):{word:'',entry_kind:'word',phonetic:'',note:'',mastery:'new',senses:[{meaning:'',part_of_speech:'',example:''}],contexts:[],notes:[],collections:[],cards:[],aliases:[]};}
    edit(entry){this.draft=this.newDraft(entry);this.conflict=null;this.editForm();}
    field(label,key,value,limit=3000,area=false){return `<label>${label}${area?`<textarea rows="2" maxlength="${limit}" data-wb-field="${key}">${esc(value)}</textarea>`:`<input maxlength="${limit}" data-wb-field="${key}" value="${esc(value)}">`}</label>`;}
    editForm(){
      const d=this.draft;
      this.open(d.id?'编辑 '+d.word:'添加词条',`<form id="wb-edit"><fieldset class="wb-fieldset"><div class="wb-two"><label>词条类型<select data-wb-field="entry_kind">${['word','phrase','pattern'].map(k=>`<option value="${k}" ${d.entry_kind===k?'selected':''}>${labels[k]}</option>`).join('')}</select></label><label>单词 / 短语 / 句型<input id="wb-word" data-wb-field="word" maxlength="120" value="${esc(d.word)}" ${d.id?'disabled':''} required></label></div><p class="wb-hint">短语支持 1–6 个英文单词；句型可用 + 或 … 表示可替换结构。</p><div class="wb-two">${this.field('音标','phonetic',d.phonetic,150)}<label>手动掌握度<select data-wb-field="mastery">${Object.entries({new:'初次遇见',learning:'正在学习',mastered:'已掌握'}).map(([k,v])=>`<option value="${k}" ${d.mastery===k?'selected':''}>${v}</option>`).join('')}</select></label></div>
      <section class="wb-section"><h3>义项 <small>第一项为主释义</small></h3>${d.senses.map((s,i)=>`<div class="wb-block">${this.field('释义',`senses.${i}.meaning`,s.meaning,3000,true)}${this.field('词性',`senses.${i}.part_of_speech`,s.part_of_speech,100)}${this.field('例句',`senses.${i}.example`,s.example,3000,true)}${i?button('remove-item','删除此义项',`data-list="senses" data-index="${i}"`):''}</div>`).join('')}${button('add-item','添加义项','data-list="senses"')}</section>
      <section class="wb-section"><h3>多个原句</h3>${d.contexts.map((s,i)=>`<div class="wb-block">${this.field('原句',`contexts.${i}.text`,s.text,5000,true)}${this.field('原句翻译',`contexts.${i}.translation`,s.translation,5000,true)}${this.field('文章标题',`contexts.${i}.article_title`,s.article_title,250)}${button('remove-item','删除此原句',`data-list="contexts" data-index="${i}"`)}</div>`).join('')}${button('add-item','添加原句','data-list="contexts"')}</section>
      <section class="wb-section"><h3>标签、分组与词书</h3><div class="wb-memberships">${this.collections.map(c=>`<label><input type="checkbox" data-wb-membership="${c.id}" ${d.collections.some(x=>x.id===c.id)?'checked':''}>${labels[c.kind]} · ${esc(c.name)}</label>`).join('')||'先在生词本页面创建分类，再为词条勾选。'}</div></section>
      <section class="wb-section"><h3>学习笔记</h3>${this.field('随手笔记','note',d.note,2000,true)}<div class="wb-toolbar"><select id="wb-template" aria-label="笔记模板"><option value="reading">阅读笔记</option><option value="contrast">易混词对比</option><option value="pattern">句型练习</option></select>${button('template','追加模板')}</div>${d.notes.map((s,i)=>`<div class="wb-block">${this.field('笔记标题',`notes.${i}.title`,s.title,100)}${this.field('笔记内容',`notes.${i}.body`,s.body,2000,true)}${button('remove-item','删除此笔记',`data-list="notes" data-index="${i}"`)}</div>`).join('')}${button('add-item','添加笔记','data-list="notes"')}</section>
      <section class="wb-section"><h3>例句、图片与音频卡片</h3><p class="wb-hint">先保存词条，再添加卡片。图片支持 PNG/JPEG/WebP，音频支持 MP3/WAV/OGG，每个文件不超过 2 MB。每个词条最多 20 张。</p>${d.id?`<div id="wb-cards">${this.cards(d.cards)}</div><div class="wb-block">${this.field('例句正面','cardFront',d.cardFront||'',3000,true)}${this.field('例句背面 / 解释','cardBack',d.cardBack||'',3000,true)}${button('example-add','保存例句卡片')}<label>上传图片或音频<input id="wb-media" type="file" accept="image/png,image/jpeg,image/webp,audio/mpeg,audio/wav,audio/x-wav,audio/ogg"></label></div>`:''}</section><div class="modal-footer"><span id="wb-edit-status">${d.id?'版本 '+d.version:'新词条'}</span><button type="submit" class="button primary">保存词条</button></div></fieldset></form><div id="wb-conflict"></div>`);
    }
    cards(cards){return cards.map(c=>`<div class="wb-card">${c.kind==='image'?`<img src="/api/wordbook/media/${c.id}" alt="${esc(c.front||'词条图片卡片')}" loading="lazy">`:c.kind==='audio'?`<audio controls preload="none" src="/api/wordbook/media/${c.id}"></audio>`:`<p lang="en">${esc(c.front)}</p><p>${esc(c.back)}</p>`}${c.kind!=='example'?`<small>${esc(c.filename)}</small>`:''}${button('card-delete','删除卡片',`data-id="${c.id}"`)}</div>`).join('');}
    readField(input){const path=input.dataset.wbField?.split('.');if(!path)return;let parent=this.draft;while(path.length>1)parent=parent[path.shift()];parent[path[0]]=input.value;}
    payload(){const d=this.draft;return {word:d.word,entry_kind:d.entry_kind,phonetic:d.phonetic,note:d.note,mastery:d.mastery,senses:d.senses,contexts:d.contexts,notes:d.notes,collection_ids:d.collections.map(c=>c.id),version:d.version};}
    async save(){
      if(this.busy)return;this.busy=true;this.dialog.querySelector('fieldset').disabled=true;
      try{const d=this.draft;await this.request(d.id?'/'+d.id:'',d.id?'PATCH':'POST',this.payload());if(this.dead)return;this.dialog.close();this.toast('词条已保存。');await this.changed();}
      catch(e){this.error(e);if(e.status===409&&e.data?.current){this.conflict=e.data.current;this.dialog.querySelector('#wb-conflict').innerHTML='<p>本页输入已保留。请核对另一处的更新：</p><pre>'+esc(JSON.stringify({释义:this.conflict.meaning,笔记:this.conflict.note,版本:this.conflict.version},null,2))+'</pre>'+button('accept-version','已核对，采用最新版本继续编辑');}}
      finally{this.busy=false;const fieldset=this.dialog.querySelector('fieldset');if(fieldset)fieldset.disabled=false;}
    }
    async mediaMutation(path,method,body){
      if(this.busy)return;this.busy=true;const fieldset=this.dialog.querySelector('fieldset');fieldset.disabled=true;
      try{const result=await this.request(path,method,{...body,version:this.draft.version});if(this.dead)return;this.draft.version=result.entry.version;this.draft.cards=result.entry.cards;this.dialog.querySelector('#wb-cards').innerHTML=this.cards(this.draft.cards);this.dialog.querySelector('#wb-edit-status').textContent='卡片已保存；其余编辑需点击保存词条。';await this.changed();}
      catch(e){this.error(e);if(e.status===409)this.dialog.querySelector('#wb-error').textContent+=' 请关闭后重新打开词条。';}
      finally{this.busy=false;fieldset.disabled=false;}
    }
    async upload(file){if(!file)return;if(file.size>2*1024*1024)throw new Error('媒体文件限 2 MB。');const bytes=new Uint8Array(await file.arrayBuffer());let raw='';for(let i=0;i<bytes.length;i+=16384)raw+=String.fromCharCode(...bytes.subarray(i,i+16384));const mime=file.type==='audio/x-wav'?'audio/wav':file.type;await this.mediaMutation('/'+this.draft.id+'/cards','POST',{kind:mime.startsWith('image/')?'image':'audio',mime,filename:file.name,base64:btoa(raw)});}
    importForm(){this.importData=null;this.open('批量导入',`<p>JSON 可保留义项、原句、词形与媒体；CSV 导入基础字段，表头至少包含 word,meaning。已有词条的复习记录不会覆盖。</p><label>JSON / CSV 文件<input id="wb-import-file" type="file" accept=".json,.csv,application/json,text/csv"></label><label>重复词条处理<select id="wb-import-mode"><option value="skip">跳过已有词条</option><option value="merge">追加资料，保留原笔记与复习进度</option></select></label><div id="wb-import-preview" role="status"></div>${button('import-confirm','确认导入','disabled')}`);}
    async preview(file){
      const generation=this.previewGeneration=(this.previewGeneration||0)+1,panel=this.panelGeneration;
      const active=()=>!this.dead&&this.dialog.open&&this.panelGeneration===panel&&this.previewGeneration===generation;
      this.importData=null;const confirm=this.dialog.querySelector('[data-wb-action="import-confirm"]');confirm.disabled=true;
      this.dialog.querySelector('#wb-import-preview').textContent='';this.dialog.querySelector('#wb-error').textContent='';
      try{
        if(!file)return;if(file.size>20*1024*1024)throw new Error('导入文件限 20 MB。');const raw=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());
        let payload;if(file.name.toLowerCase().endsWith('.csv'))payload={format:'csv',csv:raw};else{const value=JSON.parse(raw.replace(/^\uFEFF/,''));payload={entries:Array.isArray(value)?value:value.entries};}
        if(!active())return;
        const result=await this.request('/import','POST',{...payload,preview:true});if(!active())return;this.importData=payload;this.dialog.querySelector('#wb-import-preview').textContent=`预览：${result.count} 条，其中 ${result.duplicates} 条已存在。示例：${result.examples.join(', ')}`;confirm.disabled=false;
      }catch(error){if(active())throw error;}
    }

    exportForm(){const list=this.selected.size?this.entries.filter(e=>this.selected.has(e.id)):this.filtered();this.exportIds=list.map(e=>e.id);this.open('导出词条',`<p>将导出${this.selected.size?'勾选的':'当前页的'} ${list.length} 条词条。JSON 保存完整资料；CSV 仅保存基础字段与分类。导入文件不恢复历史复习日志。</p><label>格式<select id="wb-export-format"><option value="json">JSON · 完整资料</option><option value="csv">CSV · 表格基础字段</option></select></label><label class="wb-check"><input id="wb-export-media" type="checkbox" checked>JSON 包含图片与音频（文件较大时可关闭）</label>${button('download','下载文件')}`);}
    async download(){if(!this.exportIds.length||this.exportIds.length>500)throw new Error('请选择 1–500 条词条。');const query=new URLSearchParams({ids:this.exportIds.join(','),format:this.dialog.querySelector('#wb-export-format').value,media:this.dialog.querySelector('#wb-export-media').checked?'1':'0'});const response=await fetch('/api/wordbook/export?'+query,{signal:this.abort.signal});if(!response.ok)throw new Error((await response.json()).error);const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='margin-words.'+query.get('format');link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);this.dialog.close();}
    mergeForm(){const entries=this.entries.filter(e=>this.selected.has(e.id));if(entries.length<2||entries.length>31)throw new Error('请先勾选 2–31 个需要合并的词形。');this.mergeEntries=entries;this.open('合并词形',`<p>将 ${entries.map(e=>esc(e.word)).join('、')} 合并到所选词条。保留目标词的掌握度和复习计划，汇总原句、义项、卡片与历史记录；其余词条变为可识别词形。</p><label>保留的目标词条<select id="wb-merge-target">${entries.map(e=>`<option value="${e.id}">${esc(e.word)}${e.suggested_lemma&&e.suggested_lemma!==e.word?' · 词典原形 '+esc(e.suggested_lemma):''}</option>`).join('')}</select></label>${button('merge-confirm','确认合并')}`);}
    async handle(event){
      const input=event.target;
      if(event.type==='submit'&&input.id==='wb-edit'){event.preventDefault();event.stopPropagation();await this.save();return;}
      if(event.type==='input'){
        if(input.dataset.wbField)this.readField(input);
        if(input.id==='wb-search'){this.filters.query=input.value;this.page=0;clearTimeout(this.filterTimer);this.filterTimer=setTimeout(()=>this.render(true).catch(e=>this.error(e)),250);}return;
      }
      if(event.type==='change'){
        try{if(input.dataset.wbSelect){const id=Number(input.dataset.wbSelect);input.checked?this.selected.add(id):this.selected.delete(id);this.root.querySelector('#wb-selected').textContent=`已勾选 ${this.selected.size} 条`;}
          else if(input.dataset.wbMembership){const id=Number(input.dataset.wbMembership);this.draft.collections=this.draft.collections.filter(c=>c.id!==id);if(input.checked)this.draft.collections.push(this.collections.find(c=>c.id===id));}
          else if(['wb-collection','wb-kind','wb-status'].includes(input.id)){this.filters[input.id.slice(3)]=input.value;this.page=0;clearTimeout(this.filterTimer);this.filterTimer=setTimeout(()=>this.render(true).catch(e=>this.error(e)),250);}
          else if(input.id==='wb-media')await this.upload(input.files[0]);
          else if(input.id==='wb-import-file')await this.preview(input.files[0]);
        }catch(e){this.error(e);}return;
      }
      if(event.type!=='click')return;const target=input.closest('[data-wb-action]');if(!target)return;event.preventDefault();event.stopPropagation();
      const action=target.dataset.wbAction,id=Number(target.dataset.id),entry=this.entries?.find(e=>e.id===id);
      if(this.busy)return;
      try{
        if(action==='close')this.dialog.close();
        else if(action==='add')this.edit();
        else if(action==='edit')this.edit((await this.request('/'+id)).entry);
        else if(action==='previous'||action==='next'){this.page+=action==='next'?1:-1;await this.render(true);}
        else if(action==='clear-selection'){this.selected.clear();this.rows();}
        else if(action==='select-page'){this.filtered().forEach(e=>this.selected.add(e.id));this.rows();}
        else if(action==='delete'){if(await this.confirm(`删除「${entry.word}」及其卡片？历史复习计数会保留。`)){const result=await this.api('/vocabulary/'+id,{method:'DELETE',body:{}});this.undoToken=result.undo_token;await this.changed();this.toast('词条已删除，10分钟内可点击撤销。');}}
        else if(action==='undo'){await this.api('/undo/'+this.undoToken,{method:'POST',body:{}});this.undoToken=null;await this.changed();this.toast('词条、卡片和复习安排已恢复。');}
        else if(action==='pause'||action==='difficult'){await this.api('/review/cards/'+id,{method:'PATCH',body:{version:entry.schedule.version,[action==='pause'?'suspended':'leech']:!entry.schedule[action==='pause'?'suspended':'leech']}});await this.render();}
        else if(action==='collection-add'){await this.request('/collections','POST',{kind:this.root.querySelector('#wb-new-kind').value,name:this.root.querySelector('#wb-new-name').value});await this.render();}
        else if(action==='collection-rename'){await this.request('/collections/'+id,'PATCH',{name:this.root.querySelector(`[data-collection-name="${id}"]`).value});await this.render();}
        else if(action==='collection-delete'){if(await this.confirm('删除这个分类？词条与学习记录会保留。')){await this.request('/collections/'+id,'DELETE',{});if(this.filters.collection===String(id))this.filters.collection='';await this.render();}}
        else if(action==='add-item'||action==='remove-item'){const name=target.dataset.list,list=this.draft[name];if(action==='remove-item')list.splice(Number(target.dataset.index),1);else{if(list.length>=30)throw new Error('每类最多 30 项。');list.push(name==='senses'?{meaning:'',part_of_speech:'',example:''}:name==='contexts'?{text:'',translation:'',article_title:'',article_id:null}:{title:'',body:''});}this.editForm();}
        else if(action==='template'){const additions=this.templates[this.dialog.querySelector('#wb-template').value].filter(title=>!this.draft.notes.some(n=>n.title===title));if(this.draft.notes.length+additions.length>30)throw new Error('笔记最多 30 项。');this.draft.notes.push(...additions.map(title=>({title,body:''})));this.editForm();}
        else if(action==='accept-version'){this.draft.version=this.conflict.version;this.conflict=null;this.dialog.querySelector('#wb-conflict').textContent='已采用最新版本，再次保存将提交本页输入。';this.dialog.querySelector('#wb-error').textContent='';}
        else if(action==='example-add')await this.mediaMutation('/'+this.draft.id+'/cards','POST',{kind:'example',front:this.draft.cardFront||'',back:this.draft.cardBack||''});
        else if(action==='card-delete')await this.mediaMutation('/'+this.draft.id+'/cards/'+id,'DELETE',{});
        else if(action==='import')this.importForm();
        else if(action==='import-confirm'){if(!this.importData)throw new Error('请先选择文件并完成预览。');target.disabled=true;const r=await this.request('/import','POST',{...this.importData,mode:this.dialog.querySelector('#wb-import-mode').value});this.dialog.close();this.toast(`新增 ${r.added}，追加 ${r.merged}，跳过 ${r.skipped} 条。`);await this.changed();}
        else if(action==='export')this.exportForm();
        else if(action==='download')await this.download();
        else if(action==='merge')this.mergeForm();
        else if(action==='merge-confirm'){target.disabled=true;const targetId=Number(this.dialog.querySelector('#wb-merge-target').value);await this.request('/merge','POST',{target_id:targetId,source_ids:this.mergeEntries.filter(e=>e.id!==targetId).map(e=>e.id),versions:Object.fromEntries(this.mergeEntries.map(e=>[e.id,e.version]))});this.selected.clear();this.dialog.close();await this.changed();this.toast('词形已合并，目标词复习进度已保留。');}
      }catch(e){this.error(e);target.disabled=false;}
    }
  }
  window.MarginWordbook=Wordbook;
})();
