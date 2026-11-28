/* Writing drafts stay local; feedback is requested only by an explicit submission. */
'use strict';

window.MarginWriting = class MarginWriting {
  constructor({root, article, reader, api, toast}) {
    Object.assign(this, {root, article, reader, api, toast});
    this.dialog = document.getElementById('writing-dialog');
    this.events = new AbortController();
    this.history = [];
    this.attempts = [];
    this.loadVersion = this.historyVersion = this.feedbackVersion = 0;
    this.saveQueue = Promise.resolve();
    this.storagePrefix = `margin-writing:${article.id}:`;
    const listen = (target, event, callback, options={}) => target.addEventListener(event, callback.bind(this), {...options, signal:this.events.signal});
    listen(document, 'click', this.onClick);
    listen(document, 'selectionchange', this.captureSelection);
    listen(document, 'pointerdown', function(event) {
      if (event.target.closest('[data-writing-action="selection"]')) event.preventDefault();
    }, {capture:true});
    listen(this.dialog, 'submit', function(event) {
      event.preventDefault();
      if(event.target.id==='writing-setup')this.create();
      if(event.target.id==='writing-config-form')this.saveConfig();
    });
    listen(this.dialog, 'change', this.onChange);
    listen(this.dialog, 'input', function(event) {
      if(event.target.id==='writing-source'&&this.$('#writing-source-choice').value==='custom')this.customSource=event.target.value;
      if (event.target.id!=='writing-draft') return;
      this.rememberDraft(); this.updateCount();
      this.$('#writing-save-status').textContent = '草稿待保存';
      clearTimeout(this.saveTimer);
      this.saveTimer = setTimeout(()=>this.saveCurrent(), 700);
    });
    listen(this.dialog, 'close', function() {
      // A queued close from the previous article can reach the new controller.
      if(this.destroyed||this.dialog.open||!this.anchor)return;
      const anchor=this.anchor;
      this.anchor=null;
      clearTimeout(this.pollTimer);
      this.$('#writing-api-key').value='';
      this.rememberDraft(); this.saveCurrent();
      this.reader.scroller.focus({preventScroll:true});
      this.reader.restoreAnchor(anchor);
    });
    this.$('#writing-article-title').textContent = article.title;
    this.$('#writing-editor').hidden = true;
    this.$('#writing-setup').hidden = false;
    this.$('#writing-feedback').replaceChildren();
    this.$('#writing-status').textContent = '';
    this.$('#writing-api-key').value='';
    this.$('#writing-configuration').open=false;
    this.$('#writing-config-state').textContent='';
    this.$('#writing-config-status').textContent='';
    this.$('#writing-config-save').disabled=true;
    this.captureSelection();
  }

  $(selector) { return this.dialog.querySelector(selector); }
  escape(value) { return String(value??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  status(message) { if(!this.destroyed)this.$('#writing-status').textContent=message; }
  route(id=this.exercise?.id) { return '/writing/exercises/'+id; }
  key(kind, id=this.exercise?.id) { return this.storagePrefix+kind+':'+(id??'current'); }
  readLocal(key) {
    try { return JSON.parse(localStorage.getItem(key)); } catch { return null; }
  }
  writeLocal(key, value) {
    try {
      if(value===null)localStorage.removeItem(key);else localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch {
      this.status('浏览器临时保存不可用。请保持页面打开，并点击保存草稿确认已写入本机数据库。');
      return false;
    }
  }

  captureSelection() {
    if (this.destroyed || this.dialog.open) return;
    this.readerSelection = '';
    const selection = window.getSelection();
    if (selection?.rangeCount && !selection.isCollapsed) {
      const range = selection.getRangeAt(0);
      if (this.reader.body.contains(range.startContainer) && this.reader.body.contains(range.endContainer)) {
        const fragment = range.cloneContents();
        fragment.querySelectorAll('button').forEach(button=>button.remove());
        const paragraphs = [...fragment.querySelectorAll('.paragraph')];
        const value = (paragraphs.length ? paragraphs.map(p=>p.textContent.trim()).join('\n\n') : fragment.textContent).trim();
        if(value.length<=6000)this.readerSelection=value;
      }
    }
    this.root.querySelector('[data-writing-action="selection"]').hidden = !this.readerSelection;
  }

  async open(useSelection=false) {
    if(document.querySelector('dialog[open]'))return;
    const selected = useSelection ? this.readerSelection : '';
    if(useSelection&&!selected){this.toast('请先在正文中选择素材。');return;}
    this.anchor = this.reader.captureAnchor();
    this.dialog.showModal();
    this.reader.restoreAnchor(this.anchor);
    this.status('正在载入练习…');
    try {
      if (!this.options) {
        const result = await this.api('/writing/options');
        if(this.destroyed)return;
        this.options = result.modes;
        this.$('#writing-mode').innerHTML = this.options.map(mode=>`<option value="${mode.id}">${this.escape(mode.label)}</option>`).join('');
        this.resetSetup();
      }
      await this.loadHistory();
      if(this.destroyed)return;
      if(selected) {
        await this.newExercise();
        this.selection = selected;
        this.buildSources();
        this.$('#writing-source-choice').value='selection';
        this.sourceChanged();
      } else if(!this.exercise) {
        const last = this.readLocal(this.key('active',null));
        if(Number.isSafeInteger(last)&&last>0)await this.loadExercise(last);
      } else await this.refreshFeedback();
      this.status('');
      this.loadWords();
      await this.loadConfig();
    } catch(error){this.status(error.message);}
  }

  async loadConfig() {
    const config=await this.api('/writing/settings');
    if(this.destroyed)return;
    this.$('#writing-model').innerHTML=config.models.map(model=>`<option value="${this.escape(model)}">${this.escape(model)}</option>`).join('');
    this.$('#writing-model').value=config.model;
    this.$('#writing-api-key').disabled=config.key_source==='environment';
    this.$('#writing-key-source').textContent=config.key_source==='environment'
      ? '当前使用 PyCharm 的 DEEPSEEK_API_KEY 环境变量。'
      : '密钥只保存在本机。若已配置文章问答，这里会复用同一份配置。';
    this.$('#writing-config-state').textContent=config.configured?'已配置':'尚未配置';
    this.$('#writing-config-save').disabled=false;
    if(!config.configured)this.$('#writing-configuration').open=true;
  }

  async saveConfig() {
    const button=this.$('#writing-config-save');
    button.disabled=true;
    try {
      await this.api('/writing/settings',{method:'PATCH',body:{api_key:this.$('#writing-api-key').value.trim(),model:this.$('#writing-model').value}});
      if(this.destroyed)return;
      this.$('#writing-api-key').value='';
      await this.loadConfig();
      if(!this.destroyed)this.$('#writing-config-status').textContent='AI 配置已保存。';
    } catch(error){if(!this.destroyed)this.$('#writing-config-status').textContent=error.message;}
    finally{if(!this.destroyed)button.disabled=false;}
  }

  resetSetup() {
    this.selection = this.readerSelection;
    this.customSource = '';
    this.$('#writing-mode').value='imitation';
    this.$('#writing-targets').value='';
    this.$('#writing-focus').value='';
    this.buildSources(); this.modeChanged();
  }

  buildSources() {
    this.paragraphs = this.article.content.split(/\n\s*\n|\n/).filter(p=>p.trim()).map(p=>p.trim());
    this.$('#writing-source-choice').innerHTML = `${this.selection?'<option value="selection">选中原文</option>':''}${this.paragraphs.map((p,i)=>`<option value="${i}">第 ${i+1} 段</option>`).join('')}<option value="custom">自备素材</option>`;
    this.$('#writing-source-choice').value=String(Math.min(this.reader.current||0,this.paragraphs.length-1));
  }

  modeChanged() {
    const mode = this.$('#writing-mode').value;
    const option = this.options.find(item=>item.id===mode);
    this.$('#writing-instruction').textContent=option.instruction;
    this.$('#writing-targets-label').hidden=mode!=='vocabulary';
    this.$('#writing-wordbank').hidden=mode!=='vocabulary';
    this.$('#writing-source-choice').disabled=mode==='continuation';
    this.$('.writing-source-fields').hidden=['grammar','expression'].includes(mode);
    if(mode==='zh_en')this.$('#writing-source-choice').value='custom';
    this.sourceChanged();
  }

  sourceChanged() {
    const mode = this.$('#writing-mode').value, choice = this.$('#writing-source-choice').value;
    const field = this.$('#writing-source');
    field.readOnly=choice!=='custom'||mode==='continuation';
    if(mode==='continuation')field.value=this.article.content.slice(0,6000);
    else if(choice==='selection')field.value=this.selection||'';
    else if(choice!=='custom')field.value=this.paragraphs[Number(choice)]||'';
    else field.value=this.customSource||'';
    field.placeholder=mode==='zh_en'?'输入要翻译成英文的中文。':'输入句子、段落或需要讨论的话题。';
    this.$('#writing-source-note').textContent=mode==='continuation'?'续写将以完整文章为素材，此处最多预览 6000 字符。':choice==='custom'?'可输入自己的练习素材。':'可直接使用这一段，也可关闭面板后选中具体句子。';
  }

  async loadWords() {
    try {
      const result=await this.api('/vocabulary');
      if(this.destroyed)return;
      this.$('#writing-word-choice').innerHTML='<option value="">选择单词</option>'+result.words.map(word=>`<option value="${this.escape(word.word)}">${this.escape(word.word)}</option>`).join('');
    } catch { if(!this.destroyed)this.$('#writing-word-choice').innerHTML='<option value="">生词本暂不可用，可手动输入</option>'; }
  }

  async newExercise() {
    const version=++this.loadVersion;
    this.rememberDraft();
    await this.saveCurrent();
    if(this.destroyed||version!==this.loadVersion)return;
    ++this.feedbackVersion;
    clearTimeout(this.pollTimer);
    this.exercise=null;this.attempts=[];
    this.writeLocal(this.key('active',null),null);
    this.$('#writing-editor').hidden=true;this.$('#writing-setup').hidden=false;
    this.resetSetup();this.renderHistory();this.status('');
  }

  async create() {
    if(this.creating||!this.options)return;
    const mode=this.$('#writing-mode').value, choice=this.$('#writing-source-choice').value;
    const query={mode,source_kind:choice==='custom'?'custom':'article',source_text:this.$('#writing-source').value,
      target_words:this.$('#writing-targets').value.split(/[,，;；\n]+/).map(word=>word.trim()).filter(Boolean),focus:this.$('#writing-focus').value};
    if(['grammar','expression','continuation'].includes(mode))query.source_text='';
    if(JSON.stringify(query)!==this.createSignature){this.createSignature=JSON.stringify(query);this.createToken=crypto.randomUUID();}
    this.creating=true;this.$('#writing-create').disabled=true;
    const version=++this.loadVersion;
    try {
      const result=await this.api(`/articles/${this.article.id}/writing`,{method:'POST',body:{...query,request_id:this.createToken}});
      if(this.destroyed||version!==this.loadVersion)return;
      this.activate(result.exercise,[]);this.status('');await this.loadHistory();
      if(this.destroyed)return;
      this.$('#writing-draft').focus({preventScroll:true});
      this.createSignature=null;
    } catch(error){this.status(error.message);}
    finally{this.creating=false;if(!this.destroyed)this.$('#writing-create').disabled=false;}
  }

  async loadHistory(more=false) {
    const version=++this.historyVersion;
    const result=await this.api(`/articles/${this.article.id}/writing`+(more&&this.nextCursor?`?before=${this.nextCursor}`:''));
    if(this.destroyed||version!==this.historyVersion)return;
    this.history=more?[...this.history,...result.exercises]:result.exercises;
    this.nextCursor=result.next_cursor;this.renderHistory();
  }

  renderHistory() {
    this.$('#writing-history-list').innerHTML=this.history.length?this.history.map(row=>`<button class="writing-history-item" data-writing-id="${row.id}" ${row.id===this.exercise?.id?'aria-current="true"':''}><strong>${this.escape(row.label)}</strong><small>${this.escape(row.created_at.slice(5,16).replace('T',' '))} · ${row.attempt_count} 轮${row.status==='pending'?' · 生成中':''}</small></button>`).join(''):'<p class="writing-note">还没有练习记录。</p>';
    this.$('[data-writing-action="more"]').hidden=!this.nextCursor;
  }

  async loadExercise(id) {
    const version=++this.loadVersion;
    this.rememberDraft();await this.saveCurrent();
    if(this.destroyed||version!==this.loadVersion)return;
    clearTimeout(this.pollTimer);
    const result=await this.api(this.route(id));
    if(this.destroyed||version!==this.loadVersion)return;
    if(result.exercise.article_id!==this.article.id)throw new Error('这项练习属于另一篇文章。');
    this.activate(result.exercise,result.attempts);
  }

  activate(exercise, attempts) {
    ++this.feedbackVersion;
    this.exercise=exercise;this.attempts=attempts;this.selectedAttempt=null;
    this.writeLocal(this.key('active',null),exercise.id);
    const cached=this.readLocal(this.key('draft'));
    let value=exercise.draft;
    if(cached&&typeof cached.draft==='string'&&cached.draft.length<=4000&&Number.isSafeInteger(cached.version)) {
      value=cached.draft;
      if(cached.version!==exercise.version&&value!==exercise.draft) {
        exercise.remote={...exercise};exercise.conflict=true;exercise.version=cached.version;
      }
    }
    this.$('#writing-setup').hidden=true;this.$('#writing-editor').hidden=false;
    this.$('#writing-exercise-title').textContent=exercise.label;
    this.$('#writing-task').textContent=exercise.instruction+(exercise.focus?' 重点：'+exercise.focus:'');
    this.$('#writing-material').hidden=!exercise.source_text;
    this.$('#writing-material-text').textContent=exercise.source_text;
    this.$('#writing-target-summary').textContent=exercise.target_words.length?'目标词：'+exercise.target_words.join('、'):'';
    this.$('#writing-draft').value=value;
    this.$('#writing-draft').lang=exercise.mode==='en_zh'?'zh-CN':'en';
    this.$('#writing-delete-confirm').hidden=true;
    this.$('#writing-save-status').textContent=value===exercise.draft?'草稿已保存':'已恢复浏览器中尚未保存的作答';
    this.showConflict();this.updateCount();this.renderFeedback();this.renderHistory();this.sync();this.schedulePoll();
  }

  rememberDraft() {
    if(!this.exercise||this.destroyed)return;
    this.writeLocal(this.key('draft'),{draft:this.$('#writing-draft').value,version:this.exercise.version});
  }

  saveCurrent() {
    clearTimeout(this.saveTimer);
    if(!this.exercise)return Promise.resolve(true);
    const exercise=this.exercise, draft=this.$('#writing-draft').value;
    this.rememberDraft();
    this.saveQueue=this.saveQueue.catch(()=>false).then(()=>this.saveSnapshot(exercise,draft));
    return this.saveQueue;
  }

  async saveSnapshot(exercise,draft) {
    if(exercise.conflict)return false;
    const previousVersion=exercise.version;
    try {
      const result=await this.api(this.route(exercise.id),{method:'PATCH',body:{draft,version:exercise.version}});
      Object.assign(exercise,{draft:result.exercise.draft,version:result.exercise.version});
      const key=this.key('draft',exercise.id), cached=this.readLocal(key);
      if(cached?.draft===draft)this.writeLocal(key,null);
      else if(cached?.version===previousVersion)this.writeLocal(key,{...cached,version:exercise.version});
      if(!this.destroyed&&this.exercise===exercise) {
        this.$('#writing-save-status').textContent=this.$('#writing-draft').value===draft?'草稿已保存':'较新修改待保存';
      }
      return true;
    } catch(error) {
      try {
        const latest=await this.api(this.route(exercise.id));
        if(latest.exercise.version!==exercise.version&&latest.exercise.draft!==draft){exercise.conflict=true;exercise.remote=latest.exercise;}
      } catch { /* The browser draft remains available during an outage. */ }
      if(!this.destroyed&&this.exercise===exercise){this.$('#writing-save-status').textContent='尚未保存，请重试';this.showConflict();this.status(error.message);}
      return false;
    }
  }

  showConflict() {
    this.$('#writing-conflict').hidden=!this.exercise?.conflict;
    this.$('#writing-server-draft').textContent=this.exercise?.remote?.draft||'';
    this.sync();
  }

  async resolveConflict(useServer) {
    const exercise=this.exercise;
    if(!exercise?.conflict)return;
    if(useServer)this.$('#writing-draft').value=exercise.remote.draft;
    exercise.version=exercise.remote.version;exercise.draft=exercise.remote.draft;exercise.conflict=false;
    this.rememberDraft();this.showConflict();this.updateCount();await this.saveCurrent();
  }

  updateCount() {
    const draft=this.$('#writing-draft').value;
    const words=(draft.match(/[A-Za-z]+(?:['’-][A-Za-z]+)*/g)||[]).length;
    this.$('#writing-count').textContent=`${words} 个英文词 · ${draft.length} / 4000 字符`;
  }

  sync() {
    const pending=this.attempts.some(item=>item.status==='pending');
    const sending=this.sendingId===this.exercise?.id&&Boolean(this.sendingId);
    this.$('#writing-draft').disabled=sending;
    this.$('#writing-submit').disabled=!this.exercise||pending||Boolean(this.sendingId)||this.exercise.conflict;
    this.$('[data-writing-action="delete"]').disabled=pending||sending;
    this.$('#writing-submit').textContent=pending||sending?'正在生成反馈…':this.readLocal(this.key('request'))?'重试这次提交':'获取反馈';
  }

  async send() {
    const exercise=this.exercise;
    if(!exercise||exercise.conflict||this.sendingId||this.attempts.some(a=>a.status==='pending'))return;
    if(!this.$('#writing-draft').value.trim()){this.status('请先写下你的作答。');return;}
    this.sendingId=exercise.id;this.sync();this.status('正在保存原稿并获取反馈…');
    const requestKey=this.key('request',exercise.id);
    try {
      let query=this.readLocal(requestKey);
      if(!query) {
        if(!await this.saveCurrent())return;
        query={request_id:crypto.randomUUID(),draft:exercise.draft,version:exercise.version};
        this.writeLocal(requestKey,query);
      }
      const result=await this.api(this.route(exercise.id)+'/attempts',{method:'POST',body:query,timeoutMs:180000});
      this.writeLocal(requestKey,null);
      if(this.destroyed)return;
      if(this.exercise===exercise){this.selectedAttempt=result.attempt.id;await this.refreshFeedback();this.status(result.attempt.status==='failed'?result.attempt.error:'反馈已保存，可以修改作答后再次提交。');}
      await this.loadHistory();
    } catch(error) {
      if(error.status>=400&&error.status<500)this.writeLocal(requestKey,null);
      if(!this.destroyed&&this.exercise===exercise) {
        try { await this.refreshFeedback(); } catch { /* Keep the original submission token for an explicit retry. */ }
        this.status(error.message+' 原稿已保留。再次点击会核对同一次提交。');
      }
    } finally {
      this.sendingId=null;
      if(!this.destroyed){this.sync();this.schedulePoll();}
    }
  }

  async refreshFeedback() {
    if(!this.exercise)return;
    const exercise=this.exercise,version=++this.feedbackVersion;
    const result=await this.api(this.route(exercise.id));
    if(this.destroyed||this.exercise!==exercise||version!==this.feedbackVersion)return;
    this.attempts=result.attempts;this.renderFeedback();this.sync();this.schedulePoll();
  }

  schedulePoll() {
    clearTimeout(this.pollTimer);
    if(!this.destroyed&&this.dialog.open&&!this.sendingId&&this.attempts.some(item=>item.status==='pending')){
      this.pollTimer=setTimeout(()=>this.refreshFeedback().catch(error=>{this.status(error.message+' 可点击刷新查看反馈。');}),2500);
    }
  }

  renderFeedback() {
    const container=this.$('#writing-feedback');
    if(!this.attempts.length){container.innerHTML='';return;}
    const attempt=this.attempts.find(item=>item.id===this.selectedAttempt)||this.attempts[0];
    this.selectedAttempt=attempt.id;
    const names={pending:'生成中',complete:'已完成',failed:'未完成'};
    const heading=`<div class="writing-feedback-heading"><h3>反馈与对照</h3><label>提交版本<select id="writing-attempt">${this.attempts.map((item,index)=>`<option value="${item.id}" ${item.id===attempt.id?'selected':''}>第 ${this.attempts.length-index} 轮 · ${names[item.status]}</option>`).join('')}</select></label></div>`;
    if(attempt.status!=='complete') {
      container.innerHTML=heading+`<p class="writing-instruction">${attempt.status==='pending'?'DeepSeek 正在生成反馈。可以关闭面板，稍后查看记录。':this.escape(attempt.error)}</p><details><summary>查看本轮提交原稿</summary><div class="writing-text">${this.escape(attempt.original)}</div></details>`;
      return;
    }
    const feedback=attempt.feedback;
    const list=(title,items)=>items.length?`<h4>${title}</h4><ul class="writing-feedback-list">${items.map(item=>`<li>${this.escape(item)}</li>`).join('')}</ul>`:'';
    const diff=(parts,tag)=>parts.map(part=>part.changed?`<${tag}>${this.escape(part.text)}</${tag}>`:this.escape(part.text)).join('');
    const kinds={grammar:'语法与拼写',expression:'表达调整',translation:'翻译调整',structure:'结构调整'};
    container.innerHTML=heading+`<p class="writing-note">比较的是本轮提交原稿与 AI 修改稿。当前编辑区的后续修改会在下一轮提交时保存。</p>
      <p class="writing-feedback-summary">${this.escape(feedback.summary)}</p>
      ${feedback.warning?`<p class="writing-instruction">${this.escape(feedback.warning)}</p>`:''}
      ${feedback.target_usage.length?`<p class="writing-target-usage">目标词原形检查：${feedback.target_usage.map(item=>`${this.escape(item.word)}（${item.used?'已出现':'未匹配'}）`).join('、')}。变形及搭配请结合反馈判断。</p>`:''}
      ${list('做得好的地方',feedback.strengths)}
      <div class="writing-comparison"><section><h4>本轮原稿 · 删除内容有删除线</h4><div class="writing-text">${diff(attempt.comparison.before,'del')}</div></section><section><h4>修改稿 · 新增内容有下划线</h4><div class="writing-text">${diff(attempt.comparison.after,'ins')}</div></section></div>
      ${feedback.corrections.length?'<h4>修改说明</h4>'+feedback.corrections.map(item=>`<div class="writing-correction"><strong>${kinds[item.kind]}</strong><blockquote class="writing-text">${this.escape(item.before)} → ${this.escape(item.after||'（删除）')}</blockquote><p class="writing-note">${this.escape(item.reason)}</p></div>`).join(''):'<p class="writing-note">本轮没有可定位的逐项修改，请结合整体建议检查。</p>'}
      ${list('表达与练习建议',feedback.suggestions)}
      <p class="writing-note">${this.escape(attempt.model)} · ${this.escape(attempt.created_at.replace('T',' ').slice(0,19))}</p>`;
  }

  onChange(event) {
    if(event.target.id==='writing-mode')this.modeChanged();
    if(event.target.id==='writing-source-choice')this.sourceChanged();
    if(event.target.id==='writing-attempt'){this.selectedAttempt=Number(event.target.value);this.renderFeedback();}
  }

  async onClick(event) {
    const button=event.target.closest('button');
    if(!button)return;
    try {
      if(button.dataset.writingId){await this.loadExercise(Number(button.dataset.writingId));return;}
      switch(button.dataset.writingAction) {
        case 'open': await this.open(); break;
        case 'selection': await this.open(true); break;
        case 'close': this.dialog.close(); break;
        case 'new': if(this.options)await this.newExercise(); break;
        case 'save': await this.saveCurrent(); break;
        case 'submit': await this.send(); break;
        case 'refresh': await this.loadHistory();await this.refreshFeedback();break;
        case 'more': await this.loadHistory(true);break;
        case 'keep-local': await this.resolveConflict(false);break;
        case 'use-server': await this.resolveConflict(true);break;
        case 'add-word': {
          const value=this.$('#writing-word-choice').value;
          if(value)this.$('#writing-targets').value=[this.$('#writing-targets').value.trim(),value].filter(Boolean).join(', ');
          break;
        }
        case 'delete': this.$('#writing-delete-confirm').hidden=false;break;
        case 'cancel-delete': this.$('#writing-delete-confirm').hidden=true;break;
        case 'confirm-delete': {
          const id=this.exercise.id;
          await this.saveCurrent();
          await this.api(this.route(id),{method:'DELETE',body:{}});
          this.writeLocal(this.key('draft',id),null);this.writeLocal(this.key('request',id),null);
          if(this.destroyed)return;
          this.exercise=null;await this.newExercise();await this.loadHistory();break;
        }
      }
    } catch(error){this.status(error.message);}
  }

  destroy() {
    this.rememberDraft();this.saveCurrent();
    this.$('#writing-api-key').value='';
    this.destroyed=true;this.events.abort();
    clearTimeout(this.saveTimer);clearTimeout(this.pollTimer);
    this.dialog.close();
  }
};
