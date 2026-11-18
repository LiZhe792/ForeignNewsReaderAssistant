/* Sentence and article learning tools use the team's server-side AI client. */
'use strict';
window.MarginDeepAnalysis = class MarginDeepAnalysis {
  constructor({root, article, reader, api}) {
    Object.assign(this, {root, article, reader, api});
    this.dialog=document.getElementById('deep-analysis-dialog');
    this.parts=article.content.split(/\n\s*\n|\n/).filter(p=>p.trim()).map(p=>p.trim());
    this.events=new AbortController();this.sequence=0;this.selection=null;
    const listen=(element,type,handler)=>element.addEventListener(type,handler.bind(this),{signal:this.events.signal});
    listen(document,'mouseup',this.captureSelection);listen(document,'click',this.onClick);
    listen(this.dialog,'change',this.onChange);listen(this.dialog,'close',this.onClose);
  }
  q(selector){return this.dialog.querySelector(selector);}
  captureSelection(event){
    if(this.destroyed||this.dialog.open||event.target.closest('button'))return;
    if(!event.target.closest('#article-body'))return;
    this.selection=null;
    const p=event.target.closest('#article-body .paragraph'),selection=window.getSelection();
    if(!p||!selection?.rangeCount||!p.contains(selection.anchorNode)||!p.contains(selection.focusNode))return;
    const value=selection.toString().trim(),index=this.reader.paragraphs.indexOf(p);
    if(index>=0&&value&&value.length<=2800&&this.parts[index].includes(value))this.selection={index,text:value};
  }
  async onClick(event){
    const button=event.target.closest('button');if(!button)return;
    if(button.dataset.analysisOpen && this.root.contains(button)){await this.open(button.dataset.analysisOpen);return;}
    if(!this.dialog.contains(button))return;
    if(button.dataset.analysisAction==='close')this.dialog.close();
    if(button.dataset.analysisAction==='generate')await this.generate();
  }
  async open(mode){
    if(this.destroyed||document.querySelector('dialog[open]'))return;
    this.mode=mode;this.index=mode==='sentence'&&this.selection?this.selection.index:Math.max(0,this.reader.current||0);
    this.index=Math.min(this.index,this.parts.length-1);this.anchor=this.reader.captureAnchor();
    this.dialog.querySelector('#deep-analysis-title').textContent=mode==='sentence'?'长难句解析':'文章深度理解';
    this.q('#deep-sentence-label').hidden=mode!=='sentence';this.q('#deep-scope-label').hidden=mode==='sentence';
    this.q('#deep-compare-label').hidden=mode==='sentence';this.q('#deep-results').replaceChildren();
    this.dialog.querySelectorAll('select').forEach(el=>el.disabled=false);
    this.q('#deep-compare').replaceChildren();
    this.q('#deep-generate').disabled=true;this.q('#deep-status').textContent='正在读取原文和 AI 配置…';
    this.q('#deep-scope').value='full';this.populateParagraphs();this.populateSentences();this.updatePreview();
    this.dialog.showModal();const version=++this.sequence;
    try {
      const [status, articles]=await Promise.all([this.api('/deep-analysis/status'),this.api('/articles')]);
      if(this.destroyed||!this.dialog.open||version!==this.sequence)return;
      this.configured=status.configured;this.q('#deep-compare').replaceChildren();
      for(const article of articles.articles.filter(item=>item.id!==this.article.id))this.q('#deep-compare').add(new Option(article.title,article.id));
      this.q('#deep-generate').disabled=!this.configured;
      this.q('#deep-status').textContent=this.configured?`AI 服务已配置：${status.model}。选择范围后点击生成。`:'尚未配置 AI 服务。请设置 MARGIN_AI_API_KEY、MARGIN_AI_MODEL 和 MARGIN_AI_BASE_URL，重启应用后使用。';
    } catch(error){if(!this.destroyed&&this.dialog.open&&version===this.sequence)this.q('#deep-status').textContent=error.message;}
  }
  populateParagraphs(){
    const select=this.q('#deep-paragraph');select.replaceChildren();
    this.parts.forEach((p,i)=>select.add(new Option(`第 ${i+1} 段 · ${p.slice(0,55)}`,i)));
    select.value=String(this.index);
  }
  sentences(){return (this.parts[this.index].match(/[^.!?]+[.!?]+(?:["”’']+)?|[^.!?]+$/g)||[]).map(s=>s.trim()).filter(Boolean);}
  populateSentences(){
    const select=this.q('#deep-sentence');select.replaceChildren();
    this.choices=this.sentences();
    if(this.selection&&this.selection.index===this.index){this.choices.unshift(this.selection.text);}
    this.choices.forEach((text,i)=>select.add(new Option(`${i===0&&this.selection?.index===this.index?'已划选 · ':''}${text.slice(0,110)}`,i)));
  }
  updatePreview(){
    const paragraph=this.parts[this.index]||'';let preview;
    if(this.mode==='sentence') preview=this.choices[Number(this.q('#deep-sentence').value)]||'';
    else if(this.q('#deep-scope').value==='paragraph')preview=paragraph;
    else preview=this.article.content;
    const full=preview.length>900;this.q('#deep-source').textContent=preview.slice(0,900)+(full?'\n\n（此处只预览前 900 字符，生成时发送所选范围全文。）':'');
    const compare=this.q('#deep-compare-label');compare.hidden=this.mode!=='article'||this.q('#deep-scope').value!=='full';
  }
  onChange(event){
    if(event.target.id==='deep-paragraph'){this.index=Number(event.target.value);this.selection=null;this.populateSentences();}
    this.updatePreview();
  }
  onClose(){++this.sequence;if(!this.destroyed&&this.anchor){this.reader.restoreAnchor(this.anchor);this.reader.scroller.focus({preventScroll:true});}}
  async generate(){
    if(!this.configured)return;
    const ids=[...this.q('#deep-compare').selectedOptions].map(option=>Number(option.value));
    if(ids.length>3){this.q('#deep-status').textContent='最多选择 3 篇其他文章。';return;}
    const payload={mode:this.mode,level:this.q('#deep-level').value,paragraph_index:this.index};
    if(this.mode==='sentence')payload.selected_text=this.choices[Number(this.q('#deep-sentence').value)]||'';
    else {payload.scope=this.q('#deep-scope').value;payload.compare_ids=payload.scope==='full'?ids:[];}
    if(this.mode==='sentence'&&!payload.selected_text){this.q('#deep-status').textContent='请先选择原句。';return;}
    const button=this.q('#deep-generate');button.disabled=true;
    this.dialog.querySelectorAll('select').forEach(el=>el.disabled=true);
    this.q('#deep-status').textContent='正在生成解析，长文章或多篇对比可能需要较长时间…';
    const version=++this.sequence;
    try {
      const response=await this.api(`/articles/${this.article.id}/deep-analysis`,{method:'POST',body:payload,timeoutMs:130000});
      if(this.destroyed||!this.dialog.open||version!==this.sequence)return;
      this.render(response);this.q('#deep-status').textContent='AI 已生成，请结合原文核对。';
    } catch(error){if(!this.destroyed&&this.dialog.open&&version===this.sequence)this.q('#deep-status').textContent=error.message;}
    finally{if(!this.destroyed&&this.dialog.open&&version===this.sequence){button.disabled=!this.configured;this.dialog.querySelectorAll('select').forEach(el=>el.disabled=false);}}
  }
  render(response){
    const sentence={meaning:'整体意思',main_clause:'句子主干',components:'主谓宾与修饰成分',clauses:'从句',nonfinite:'非谓语结构',special_structures:'插入语、倒装和省略',references:'指代关系',connectors:'逻辑连接词',breakdown:'分层拆句',syntax_tree:'文字语法树',simplification:'简化英文改写',uncertainty:'不确定之处'};
    const article={summary:'摘要',structure:'文章结构',arguments:'论点与论据',paragraph_logic:'段落逻辑关系',background:'背景知识',culture:'文化知识',rhetoric:'修辞手法',stance_tone:'作者立场与语气',fact_opinion:'事实陈述与观点',comparison:'多篇观点对比',uncertainty:'不确定之处'};
    const container=this.q('#deep-results');container.replaceChildren();
    for(const [key,label] of Object.entries(this.mode==='sentence'?sentence:article)){
      const text=response.result[key];if(!text)continue;
      const section=document.createElement('section'),heading=document.createElement('h3'),body=document.createElement('p');
      heading.textContent=label;body.textContent=text;section.append(heading,body);container.append(section);
    }
    if(response.result.evidence.length){
      const section=document.createElement('section'),heading=document.createElement('h3');heading.textContent='原文依据';section.append(heading);
      const titles=new Map(response.materials.map(item=>[item.article_id,item.title]));
      for(const item of response.result.evidence){const quote=document.createElement('blockquote'),label=document.createElement('small'),explain=document.createElement('p');label.textContent=titles.get(item.article_id)||'原文';quote.textContent=item.quote;explain.textContent=item.explanation;section.append(label,quote,explain);}
      container.append(section);
    }
  }
  destroy(){this.destroyed=true;++this.sequence;this.events.abort();if(this.dialog.open)this.dialog.close();}
};
