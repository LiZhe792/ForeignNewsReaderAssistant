/* Contextual explanations and resumable, editable aligned translations. */
'use strict';
window.MarginStudy = class MarginStudy {
  constructor({root, article, reader, settings, api, toast, onSettings, getLookup}) {
    Object.assign(this,{root,article,reader,settings,api,toast,onSettings,getLookup});
    this.events=new AbortController();this.results=new Map();this.drafts=new Map();this.errors=new Map();
    this.revealed=new Set();this.editing=new Set();this.kind='context';this.scope='sentence';this.onlyFavorites=false;
    this.dialog=document.getElementById('study-dialog');this.generation=0;
    const listen=(element,type,fn,options={})=>element.addEventListener(type,fn.bind(this),{...options,signal:this.events.signal});
    listen(document,'click',this.onClick);listen(document,'mouseup',this.onSelection);
    listen(this.dialog,'change',this.onChange);listen(this.dialog,'input',this.onInput);
    listen(this.dialog,'toggle',this.onToggle,{capture:true});
    listen(this.dialog,'close',function(){this.stop();if(!this.destroyed&&this.anchor){this.reader.restoreAnchor(this.anchor);this.reader.scroller.focus({preventScroll:true});}});
  }
  escape(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  url(suffix=''){return `/articles/${this.article.id}/assistance${suffix}`;}
  apply(settings){this.settings=settings;if(this.dialog.open&&!this.busy){this.dialog.querySelector('#study-level').value=settings.ai_level;this.renderResults();}}
  onSelection(event){
    if(this.destroyed||document.querySelector('dialog[open]')||event.target.closest('button'))return;
    const paragraph=event.target.closest('#article-body .paragraph'), selection=window.getSelection();
    if(!paragraph||!selection?.rangeCount||!selection.toString().trim()||!paragraph.contains(selection.anchorNode)||!paragraph.contains(selection.focusNode))return;
    const range=selection.getRangeAt(0), prefix=range.cloneRange();prefix.selectNodeContents(paragraph);prefix.setEnd(range.startContainer,range.startOffset);
    const raw=range.toString(), text=raw.trim();
    const start=[...prefix.toString()].length+[...raw].length-[...raw.trimStart()].length,end=start+[...text].length;
    const plain=paragraph.dataset.plain;
    if(!text||[...text].length>2800||[...plain].slice(start,end).join('')!==text)return;
    this.selection={paragraph_index:this.reader.paragraphs.indexOf(paragraph),start,end,scope:MarginDictionary.valid(text)&&text.length<=120?'word':'sentence'};
    const bar=this.root.querySelector('#study-selection-bar');bar.hidden=false;
    bar.querySelector('span').textContent=`已选 ${[...text].length} 字符`;
  }
  preferred(text,index){
    const parts=this.article.content.split(/\n\s*\n|\n/).filter(p=>p.trim()).map(p=>p.trim());
    const indices=[index,this.reader.current,...parts.map((_,i)=>i)].filter((n,i,a)=>Number.isInteger(n)&&n>=0&&n<parts.length&&a.indexOf(n)===i);
    const escaped=text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/ /g,'\\s+').replace(/['’]/g,"['’]");
    const pattern=new RegExp(`(?<![A-Za-z])${escaped}(?![A-Za-z])`,'i');
    for(const i of indices){const match=pattern.exec(parts[i]);if(match)return {paragraph_index:i,start:[...parts[i].slice(0,match.index)].length,end:[...parts[i].slice(0,match.index+match[0].length)].length,scope:'word'};}
    return null;
  }
  async open(kind,preferred=null){
    if(document.querySelector('dialog[open]'))return;
    if(preferred){this.selection=this.preferred(preferred.word,preferred.paragraphIndex);if(!this.selection){this.toast('这个词未出现在原文中，请划选原文中的表达后再试。',true);return;}}
    this.kind=kind;this.anchor=this.reader.captureAnchor();this.dialog.showModal();
    this.manifest=null;this.service=null;
    this.dialog.querySelector('#study-message').textContent='正在读取原文和已保存结果…';
    this.dialog.querySelector('#study-results').replaceChildren();this.dialog.querySelector('#study-generate').disabled=true;
    this.opening=(this.opening||0)+1;const opening=this.opening;
    try{
      const [manifest,service]=await Promise.all([this.api(this.url()),this.api('/assistance/status')]);
      if(this.destroyed||!this.dialog.open||opening!==this.opening)return;
      this.manifest=manifest;this.service=service;
      const incoming=new Map(manifest.results.map(row=>[row.id,row]));
      for(const [id,row] of this.results)if(incoming.has(id)&&incoming.get(id).version<row.version)incoming.set(id,row);
      this.results=incoming;
      this.index=this.selection?.paragraph_index??this.reader.current??0;this.index=Math.min(this.index,manifest.paragraphs.length-1);
      this.scope=this.selection?'selection':'sentence';this.onlyFavorites=false;
      this.syncControls();this.renderResults();this.message('');
    }catch(error){if(!this.destroyed&&this.dialog.open){this.message(error.message);this.dialog.querySelector('#study-reload').hidden=false;}}
  }
  syncControls(){
    if(!this.manifest)return;
    const q=selector=>this.dialog.querySelector(selector), parts=this.manifest.paragraphs;
    q('#study-kind-context').setAttribute('aria-pressed',String(this.kind==='context'));
    q('#study-kind-translation').setAttribute('aria-pressed',String(this.kind==='translation'));
    q('#study-paragraph').innerHTML=parts.map((p,i)=>`<option value="${i}">第 ${i+1} 段 · ${this.escape(p.text.slice(0,44))}</option>`).join('');
    q('#study-paragraph').value=this.index;
    q('#study-scope option[value="selection"]').disabled=!this.selection||this.selection.paragraph_index!==this.index;
    q('#study-scope option[value="full"]').disabled=this.kind!=='translation';
    q('#study-scope').value=this.scope;
    const sentences=parts[this.index].sentences;
    this.sentence=Math.min(this.sentence||0,Math.max(0,sentences.length-1));
    q('#study-sentence').innerHTML=sentences.map((s,i)=>`<option value="${i}">第 ${i+1} 句 · ${this.escape([...parts[this.index].text].slice(s.start,s.end).join('').slice(0,60))}</option>`).join('');
    q('#study-sentence').value=this.sentence;q('#study-sentence-label').hidden=this.scope!=='sentence';
    q('#study-level').value=this.settings.ai_level||'intermediate';
    q('#study-favorites').checked=this.onlyFavorites;
    q('#study-service').textContent=this.service.message;
    q('#study-setup').hidden=this.service.configured;
    q('#study-generate').textContent=this.kind==='context'?'生成语境释义':'生成译文';
    q('#study-generate').disabled=!this.service.configured||this.busy;
    q('#study-reload').hidden=true;
    q('#study-source-preview').textContent=this.scope==='full'?`全文共 ${this.targets().length} 个片段，逐段生成并保存；可以随时停止，之后继续。`:this.targets().map(target=>this.source(target)).join('\n\n');
    this.dialog.querySelectorAll('#study-paragraph, #study-scope, #study-sentence, #study-level, [data-study-action="kind"]').forEach(control=>{control.disabled=!!this.busy;});
    q('#study-stop').hidden=!this.busy;
  }
  targets(){
    if(!this.manifest)return [];
    const withIndex=(unit,index)=>({...unit,paragraph_index:index,scope:'paragraph'});
    if(this.scope==='full')return this.manifest.paragraphs.flatMap((p,i)=>p.units.map(unit=>withIndex(unit,i)));
    if(this.scope==='selection')return this.selection?[this.selection]:[];
    const paragraph=this.manifest.paragraphs[this.index];
    if(this.scope==='paragraph')return paragraph.units.map(unit=>withIndex(unit,this.index));
    const sentence=paragraph.sentences[this.sentence||0]||paragraph.units[0];
    return sentence?[{...sentence,paragraph_index:this.index,scope:'sentence'}]:[];
  }
  source(target){return [...this.manifest.paragraphs[target.paragraph_index].text].slice(target.start,target.end).join('');}
  matches(row,target,level=this.settings.ai_level){return row.kind===this.kind&&row.level===level&&(!this.service.configured||row.profile===this.manifest.profile)&&!row.stale&&row.scope===target.scope&&row.paragraph_index===target.paragraph_index&&row.start===target.start&&row.end===target.end;}
  message(text){this.dialog.querySelector('#study-message').textContent=text;}
  async run(){
    if(this.busy||!this.manifest||!this.service.configured)return;
    const targets=this.targets(),kind=this.kind,level=this.settings.ai_level, generation=++this.generation;
    if(!targets.length)return;
    this.busy=true;this.request=new AbortController();this.syncControls();let done=0;
    try{
      for(const target of targets){
        if(this.destroyed||generation!==this.generation)break;
        const cached=[...this.results.values()].find(row=>this.matches(row,target,level));
        if(cached){this.revealed.add(cached.id);done++;continue;}
        this.message(`正在${kind==='context'?'解读':'翻译'} ${done+1}/${targets.length}，已完成的内容会保留。`);
        const response=await this.api(this.url(),{method:'POST',signal:this.request.signal,body:{kind,level,article_hash:this.manifest.article_hash,...target}});
        if(this.destroyed||generation!==this.generation)break;
        this.results.set(response.result.id,response.result);this.revealed.add(response.result.id);done++;this.renderResults();
      }
      if(!this.destroyed&&generation===this.generation){this.message(`已完成 ${done}/${targets.length}。AI 结果供学习参考，可核对原文并修订。`);this.renderResults();}
    }catch(error){if(!this.destroyed&&generation===this.generation)this.message(`${error.message} 已完成 ${done}/${targets.length}，再次点击生成可继续。`);}
    finally{if(generation===this.generation){this.busy=false;this.syncControls();}}
  }
  stop(){
    this.opening=(this.opening||0)+1;
    if(!this.busy)return;
    this.generation++;this.request?.abort();this.busy=false;
    if(!this.destroyed){this.syncControls();this.message('已停止后续生成。已保存的结果会保留，重新打开面板可继续。');}
  }
  renderContext(row){
    const p=row.payload,escape=this.escape.bind(this);
    const fields=[['contextual_meaning','本句中的含义'],['sense_disambiguation','一词多义辨析'],['phrase_meaning','短语整体含义'],['figurative_language','隐喻与习语'],['register_tone','语域与语气'],['learning_note','学习提示']];
    return fields.filter(([key])=>p[key]).map(([key,label])=>`<section><h4>${label}</h4><p>${escape(p[key])}</p></section>`).join('')+
      `<section><h4>原文依据</h4><blockquote lang="en">${escape(p.evidence)}</blockquote></section>`+
      (p.references.length?`<section><h4>上下文指代</h4>${p.references.map(ref=>`<p><strong>${escape(ref.expression)}</strong> → ${escape(ref.referent)}</p><blockquote lang="en">${escape(ref.evidence)}</blockquote>`).join('')}</section>`:'')+
      `<section><h4>结合文章的新例句 · AI 生成</h4>${p.examples.map(example=>`<p lang="en">${escape(example.en)}</p><p>${escape(example.zh)}</p>`).join('')}</section>`;
  }
  renderTranslation(row){
    const p=row.payload,escape=this.escape.bind(this),draft=this.drafts.get(row.id)||{literal:p.literal,natural:p.natural,note:row.note};
    return `<details class="study-translation" data-study-result="${row.id}" ${this.revealed.has(row.id)?'open':''}><summary>译文 · 按需展开</summary>
      <div class="study-translation-pair"><section><h4>直译${row.edited?' · 已修订':''}</h4><p>${escape(p.literal)}</p></section><section><h4>意译${row.edited?' · 已修订':''}</h4><p>${escape(p.natural)}</p></section></div>
      <section><h4>直译与意译对比</h4><p>${escape(p.comparison)}</p></section>
      ${p.expressions.length?`<section><h4>重点表达</h4>${p.expressions.map(item=>`<p><strong lang="en">${escape(item.expression)}</strong>：${escape(item.explanation)}</p>`).join('')}</section>`:''}
      ${p.cultural_note?`<section><h4>文化与背景</h4><p>${escape(p.cultural_note)}</p></section>`:''}
      ${row.note?`<section><h4>我的修订笔记</h4><p>${escape(row.note)}</p></section>`:''}
      <div class="study-row-actions"><button class="button small" data-study-action="favorite" data-study-id="${row.id}" aria-pressed="${row.favorite}">${row.favorite?'取消收藏':'收藏译文'}</button><button class="button small" data-study-action="edit" data-study-id="${row.id}">${this.editing.has(row.id)?'收起修订':'修订译文'}</button></div>
      ${this.editing.has(row.id)?`<div class="study-editor"><label>直译（可修订）<textarea rows="3" maxlength="8000" data-study-edit="literal" data-study-id="${row.id}">${escape(draft.literal)}</textarea></label><label>意译（可修订）<textarea rows="3" maxlength="8000" data-study-edit="natural" data-study-id="${row.id}">${escape(draft.natural)}</textarea></label><label>修订笔记<textarea rows="2" maxlength="2000" data-study-edit="note" data-study-id="${row.id}">${escape(draft.note)}</textarea></label><p class="study-draft-note">${this.drafts.has(row.id)?'有尚未保存的输入':'修改后点击保存；原始模型译文会保留'}</p><div class="study-row-actions"><button class="button primary small" data-study-action="save" data-study-id="${row.id}">保存修订</button><button class="button small" data-study-action="original" data-study-id="${row.id}">载入模型初稿</button></div></div>`:''}
      ${row.edited?`<details class="study-original"><summary>查看模型初稿</summary><p>${escape(row.original.literal)}</p><p>${escape(row.original.natural)}</p></details>`:''}
      </details>`;
  }
  renderResults(){
    if(!this.manifest||this.destroyed)return;
    const list=this.dialog.querySelector('#study-results'),scroll=this.dialog.scrollTop,focused=document.activeElement;
    const editing=focused?.matches('[data-study-edit]')?{id:focused.dataset.studyId,field:focused.dataset.studyEdit,start:focused.selectionStart,end:focused.selectionEnd,direction:focused.selectionDirection,scroll:focused.scrollTop}:null;
    const targets=this.targets();
    const rows=[...this.results.values()].filter(row=>this.onlyFavorites?row.kind==='translation'&&row.favorite:targets.some(target=>this.matches(row,target)))
      .sort((a,b)=>a.paragraph_index-b.paragraph_index||a.start-b.start||a.id-b.id);
    list.innerHTML=rows.length?rows.map(row=>`<article class="study-result" data-study-row="${row.id}"><div class="study-result-meta">第 ${row.paragraph_index+1} 段 · ${this.escape({beginner:'入门',intermediate:'进阶',advanced:'深入'}[row.level])} · ${this.escape(row.model)} · ${row.edited?'用户修订':'AI 生成'}${row.favorite?' · 已收藏':''}${row.stale?' · 原文已有变化':''}</div><blockquote class="study-original-source" lang="en">${this.escape(row.source_text)}</blockquote>${row.kind==='context'?this.renderContext(row):this.renderTranslation(row)}${row.payload.uncertainty?`<p class="study-uncertainty">不确定之处：${this.escape(row.payload.uncertainty)}</p>`:''}${this.errors.has(row.id)?`<p class="error-note" role="alert">${this.escape(this.errors.get(row.id))}</p>`:''}</article>`).join(''):'<p class="study-empty">当前范围还没有保存的结果。选择原文并点击生成，或查看已收藏译文。</p>';
    this.dialog.scrollTop=scroll;
    if(editing){const replacement=list.querySelector(`[data-study-id="${editing.id}"][data-study-edit="${editing.field}"]`);if(replacement){replacement.focus({preventScroll:true});replacement.setSelectionRange(editing.start,editing.end,editing.direction);replacement.scrollTop=editing.scroll;}}
  }
  onInput(event){
    const input=event.target;if(!input.matches('[data-study-edit]'))return;
    const id=Number(input.dataset.studyId),row=this.results.get(id);
    const draft=this.drafts.get(id)||{literal:row.payload.literal,natural:row.payload.natural,note:row.note};
    draft[input.dataset.studyEdit]=input.value;this.drafts.set(id,draft);
    input.closest('.study-editor').querySelector('.study-draft-note').textContent='有尚未保存的输入';
  }
  onToggle(event){if(event.target.matches('.study-translation')){const id=Number(event.target.dataset.studyResult);if(event.target.open)this.revealed.add(id);else this.revealed.delete(id);}}
  onChange(event){
    if(this.busy)return;
    const input=event.target;
    if(input.id==='study-paragraph'){this.index=Number(input.value);this.sentence=0;if(this.scope==='selection')this.scope='sentence';}
    else if(input.id==='study-scope')this.scope=input.value;
    else if(input.id==='study-sentence')this.sentence=Number(input.value);
    else if(input.id==='study-level')this.onSettings({ai_level:input.value});
    else if(input.id==='study-favorites')this.onlyFavorites=input.checked;
    else return;
    this.syncControls();this.renderResults();this.message('');
  }
  async update(id,change){
    const row=this.results.get(id);if(!row||this.saving?.has(id))return;
    this.saving??=new Set();this.saving.add(id);this.errors.delete(id);
    const submitted={...change};
    try{
      const response=await this.api(this.url('/'+id),{method:'PATCH',body:{version:row.version,...change}});
      if(this.destroyed)return;
      this.results.set(id,response.result);
      if('literal' in submitted||'natural' in submitted||'note' in submitted){
        const draft=this.drafts.get(id);
        if(!draft||['literal','natural','note'].every(key=>draft[key]===submitted[key]))this.drafts.delete(id);
      }
      this.message(this.drafts.has(id)?'上一版已保存，之后的输入仍待保存。':'译文记录已保存。');
    }catch(error){if(!this.destroyed){if(error.status===409&&error.data?.current)this.results.set(id,error.data.current);this.errors.set(id,error.message);}}
    finally{this.saving.delete(id);if(!this.destroyed)this.renderResults();}
  }
  async onClick(event){
    const button=event.target.closest('[data-study-action]');if(!button)return;
    const action=button.dataset.studyAction,id=Number(button.dataset.studyId),row=this.results.get(id);
    if(action==='open-context')return this.open('context');
    if(action==='open-translation')return this.open('translation');
    if(action==='explain-selection')return this.open('context');
    if(action==='translate-selection')return this.open('translation');
    if(action==='lookup-context')return this.open('context',this.getLookup());
    if(action==='lookup-translation')return this.open('translation',this.getLookup());
    if(action==='close'){this.dialog.close();return;}
    if(action==='stop'){this.stop();return;}
    if(action==='reload'){this.dialog.close();return this.open(this.kind);}
    if(action==='kind'&&!this.busy){this.kind=button.dataset.studyKind;if(this.scope==='full'&&this.kind==='context')this.scope='sentence';this.syncControls();this.renderResults();return;}
    if(action==='generate')return this.run();
    if(!row)return;
    if(action==='favorite')return this.update(id,{favorite:!row.favorite});
    if(action==='edit'){if(this.editing.has(id))this.editing.delete(id);else this.editing.add(id);this.renderResults();}
    if(action==='save')return this.update(id,this.drafts.get(id)||{literal:row.payload.literal,natural:row.payload.natural,note:row.note});
    if(action==='original'){this.drafts.set(id,{literal:row.original.literal,natural:row.original.natural,note:this.drafts.get(id)?.note??row.note});this.editing.add(id);this.renderResults();}
  }
  destroy(){this.destroyed=true;this.stop();this.events.abort();this.dialog.close();this.dialog.querySelector('#study-results').replaceChildren();}
};
