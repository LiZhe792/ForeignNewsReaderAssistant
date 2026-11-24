/* Article-scoped DeepSeek conversations. The browser never receives a saved key. */
'use strict';

window.MarginArticleQA = class MarginArticleQA {
  constructor({root, article, reader, api, toast}) {
    Object.assign(this, {root, article, reader, api, toast});
    this.dialog = document.getElementById('article-qa-dialog');
    this.events = new AbortController();
    this.turns = [];
    this.historyTab = 'history';
    this.historyItems = [];
    this.loadVersion = 0;
    this.historyVersion = 0;
    this.labels = {ask:'文章问答', meaning:'词义追问', syntax:'句法追问', socratic:'引导思考', preread:'阅读前引导', quiz:'理解检查', feedback:'作答反馈'};
    this.prompts = {meaning:'请解释这个表达在文章中的含义。', syntax:'请解释这段内容的句子结构、修饰关系和指代。',
      socratic:'请通过一次一个小问题，引导我自己理解文章。', preread:'请给我 3 个阅读前问题，引导我阅读，但先不要揭示结论。',
      quiz:'请根据全文生成 3 个阅读理解检查题，暂时不要给答案。'};
    const listen = (element, event, fn, options={}) => element.addEventListener(event, fn.bind(this), {...options, signal:this.events.signal});
    listen(document, 'click', this.onClick);
    listen(document, 'selectionchange', this.captureSelection);
    listen(document, 'pointerdown', function (event) {
      if (event.target.closest('[data-qa-action="open-selection"]')) event.preventDefault();
    }, {capture:true});
    listen(this.dialog, 'submit', this.onSubmit);
    listen(this.dialog, 'change', this.onChange);
    listen(this.dialog, 'close', function () {
      clearTimeout(this.pollTimer);
      if (this.destroyed) return;
      if (this.jumpTarget != null) {
        const target = this.jumpTarget;
        this.jumpTarget = null;
        requestAnimationFrame(() => {
          if (this.destroyed) return;
          this.reader.jump(target, true);
          const paragraph = this.reader.paragraphs[target];
          paragraph.classList.add('qa-source-highlight');
          clearTimeout(this.highlightTimer);
          this.highlightTimer = setTimeout(() => paragraph.classList.remove('qa-source-highlight'), 2600);
        });
      } else if (this.anchor) {
        this.reader.scroller.focus({preventScroll:true});
        this.reader.restoreAnchor(this.anchor);
      }
    });
    this.$('#qa-article-title').textContent = article.title;
    this.newConversation();
    this.captureSelection();
  }

  $(selector) { return this.dialog.querySelector(selector); }
  escape(value) { return String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  status(message) { if (!this.destroyed) this.$('#qa-request-status').textContent = message; }

  captureSelection() {
    if (this.dialog.open || this.destroyed) return;
    const selected = window.getSelection();
    this.readerSelection = null;
    if (selected?.rangeCount && !selected.isCollapsed) {
      const range = selected.getRangeAt(0);
      if (this.reader.body.contains(range.startContainer) && this.reader.body.contains(range.endContainer)) {
        const indices = this.reader.paragraphs.flatMap((p,i)=>range.intersectsNode(p) ? [i+1] : []);
        const fragment = range.cloneContents();
        fragment.querySelectorAll('button').forEach(button=>button.remove());
        const selectedParagraphs = [...fragment.querySelectorAll('.paragraph')];
        const text = (selectedParagraphs.length ? selectedParagraphs.map(p=>p.textContent.trim()).join('\n\n') : fragment.textContent).trim();
        if (text && text.length <= 6000 && indices.length <= 50) this.readerSelection = {text, paragraphs:indices};
      }
    }
    const button = this.root.querySelector('[data-qa-action="open-selection"]');
    button.hidden = !this.readerSelection;
  }

  async open(useSelection=false) {
    if (document.querySelector('dialog[open]')) return;
    if (useSelection && !this.readerSelection) { this.toast('请先在正文中选择要提问的内容。'); return; }
    this.anchor = this.reader.captureAnchor();
    if (useSelection) {
      this.selection = {...this.readerSelection};
      this.$('#qa-scope').value = 'selection';
      this.$('#qa-mode').value = 'ask';
    }
    this.syncSelection();
    this.dialog.showModal();
    this.dialog.scrollTop = 0;
    this.reader.restoreAnchor(this.anchor);
    this.status('');
    await Promise.all([this.loadConfig(), this.loadHistory()]);
    if (this.destroyed) return;
    if (this.threadId && !this.requesting) await this.loadThread(this.threadId, null, true);
  }

  async loadConfig() {
    try {
      const config = await this.api('/ai/settings');
      if (this.destroyed) return;
      this.config = config;
      this.$('#qa-model').innerHTML = config.models.map(model=>`<option value="${this.escape(model)}">${this.escape(model)}</option>`).join('');
      this.$('#qa-model').value = config.model;
      this.$('#qa-config-badge').textContent = config.configured ? '已配置' : '尚未配置';
      this.$('#qa-key-source').textContent = config.key_source === 'environment'
        ? '当前使用 PyCharm 环境变量中的密钥，请在运行配置中管理。' : '密钥仅保存在本机配置文件，已保存的密钥不会回显。';
      this.$('#qa-api-key').disabled = config.key_source === 'environment';
      this.$('[data-qa-action="clear-key"]').disabled = config.key_source !== 'local';
      if (!config.configured) this.$('#qa-configuration').open = true;
      this.syncSending();
    } catch (error) { if (!this.destroyed) this.$('#qa-config-status').textContent = error.message; }
  }

  async saveConfig() {
    const button = this.$('#qa-config-form button[type="submit"]');
    button.disabled = true;
    try {
      await this.api('/ai/settings', {method:'PATCH', body:{api_key:this.$('#qa-api-key').value.trim(), model:this.$('#qa-model').value}});
      if (this.destroyed) return;
      this.$('#qa-api-key').value = '';
      await this.loadConfig();
      this.$('#qa-config-status').textContent = '配置已保存在本机。可以检测连接或开始提问。';
    } catch (error) { if (!this.destroyed) this.$('#qa-config-status').textContent = error.message; }
    finally { if (!this.destroyed) button.disabled = false; }
  }

  async connection(button) {
    button.disabled = true;
    this.$('#qa-config-status').textContent = '正在检测密钥与模型连接…';
    try {
      const result = await this.api('/ai/connection', {method:'POST', body:{}, timeoutMs:180000});
      if (!this.destroyed) this.$('#qa-config-status').textContent = `${result.model} 连接正常；发送问题将按用量计费。`;
    } catch (error) { if (!this.destroyed) this.$('#qa-config-status').textContent = error.message; }
    finally { if (!this.destroyed) button.disabled = false; }
  }

  async loadHistory(more=false) {
    const version = ++this.historyVersion, tab = this.historyTab;
    const route = tab === 'favorites' ? 'favorites' : 'threads';
    try {
      const result = await this.api(`/articles/${this.article.id}/qa/${route}` + (more && this.nextCursor ? `?before=${this.nextCursor}` : ''));
      if (this.destroyed || version !== this.historyVersion) return;
      const rows = result[route];
      this.historyItems = more ? [...this.historyItems,...rows] : rows;
      this.nextCursor = result.next_cursor;
      this.renderHistory();
    } catch (error) { if (!this.destroyed && version === this.historyVersion) this.$('#qa-history-list').innerHTML = `<p class="qa-note">${this.escape(error.message)} 点击“刷新状态”重试。</p>`; }
  }

  renderHistory() {
    const favorites = this.historyTab === 'favorites';
    this.$('#qa-history-list').innerHTML = this.historyItems.length ? this.historyItems.map(row=>{
      const thread = favorites ? row.thread_id : row.id;
      return `<button class="qa-history-item" data-qa-thread="${thread}" ${favorites ? `data-qa-turn="${row.id}"` : ''} ${thread===this.threadId?'aria-current="true"':''}><span>${this.escape(favorites ? row.question.slice(0,75) : row.title)}</span><small>${this.escape(row.created_at.slice(5,10))}${favorites ? ' · 已收藏' : ` · ${row.turn_count} 问`}</small></button>`;
    }).join('') : `<p class="qa-note">${favorites?'本篇还没有收藏的问答。':'本篇还没有问答记录。'}</p>`;
    this.$('[data-qa-action="more-history"]').hidden = !this.nextCursor;
    this.dialog.querySelectorAll('[data-qa-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.qaTab===this.historyTab)));
  }

  async loadThread(id, target=null, preserveDraft=false) {
    const version = ++this.loadVersion;
    clearTimeout(this.pollTimer);
    this.status('正在读取对话…');
    try {
      const result = await this.api('/qa/threads/'+id);
      if (this.destroyed || version !== this.loadVersion) return;
      if (result.thread.article_id !== this.article.id) throw new Error('这段对话属于另一篇文章。');
      this.threadId = id;
      this.turns = result.turns;
      this.$('#qa-thread-title').textContent = result.thread.title;
      this.$('#qa-delete-confirm').hidden = true;
      if (!preserveDraft) {
        const last = this.turns.at(-1);
        this.selection = last?.selected_text ? {text:last.selected_text, paragraphs:last.selection_paragraphs} : null;
        this.$('#qa-scope').value = last?.scope || 'article';
        this.$('#qa-mode').value = last?.mode === 'quiz' ? 'feedback' : last?.mode || 'ask';
        this.$('#qa-question').value = '';
      }
      this.syncSelection(); this.renderTurns(target); this.renderHistory();
      this.status(this.turns.some(turn=>turn.status==='pending') ? '回答正在生成，可稍后回来查看。' : '');
      this.schedulePoll();
    } catch (error) { if (!this.destroyed && version === this.loadVersion) this.status(error.message); }
  }

  schedulePoll() {
    clearTimeout(this.pollTimer);
    if (!this.destroyed && this.dialog.open && !this.requesting && this.threadId && this.turns.some(turn=>turn.status==='pending')) {
      this.pollTimer = setTimeout(()=>this.loadThread(this.threadId,null,true),3000);
    }
  }

  newConversation() {
    this.loadVersion++;
    clearTimeout(this.pollTimer);
    this.threadId = null; this.turns = []; this.selection = null;
    this.$('#qa-thread-title').textContent = '新的文章问答';
    this.$('#qa-question').value = '';
    this.$('#qa-mode').value = 'ask'; this.$('#qa-scope').value = 'article';
    this.$('#qa-delete-confirm').hidden = true;
    this.syncSelection(); this.renderTurns(); this.renderHistory(); this.status('');
  }

  syncSelection() {
    const scope = this.$('#qa-scope');
    scope.querySelector('[value="selection"]').disabled = !this.selection;
    if (!this.selection || ['preread','quiz','feedback'].includes(this.$('#qa-mode').value)) scope.value = 'article';
    scope.disabled = ['preread','quiz','feedback'].includes(this.$('#qa-mode').value);
    this.$('#qa-selection-preview').hidden = scope.value !== 'selection' || !this.selection;
    this.$('#qa-selection-label').textContent = this.selection ? `· 第 ${this.selection.paragraphs.join('、')} 段` : '';
    this.$('#qa-selection-text').textContent = this.selection?.text || '';
    const canGrade = this.turns.filter(turn=>turn.status==='complete').slice(-6).some(turn=>turn.mode==='quiz');
    this.$('#qa-mode [value="feedback"]').disabled = !canGrade;
    if (this.$('#qa-mode').value === 'feedback' && !canGrade) this.$('#qa-mode').value = 'ask';
    this.$('#qa-question').placeholder = this.$('#qa-mode').value === 'feedback'
      ? '请按题号写下你的理解，例如：1. … 2. … 3. …' : '输入问题；追问会带上最近 6 轮对话。';
  }

  renderTurns(target=null) {
    const list = this.$('#qa-messages');
    const latestQuiz = this.turns.filter(turn=>turn.status==='complete').slice(-6).findLast(turn=>turn.mode==='quiz');
    list.innerHTML = this.turns.length ? this.turns.map(turn=>`<article class="qa-turn" id="qa-turn-${turn.id}">
      <div class="qa-turn-meta"><span>${this.escape(this.labels[turn.mode])}</span><span>${turn.scope==='selection'?'针对选中内容':'基于全文'}</span><span>${this.escape(turn.created_at.slice(11,16))}</span></div>
      <p class="qa-question-bubble">${this.escape(turn.question)}</p>
      ${turn.status==='complete' ? `<div class="qa-answer">${this.escape(turn.answer)}</div>
        ${turn.questions.length ? `<ol class="qa-questions">${turn.questions.map(question=>`<li>${this.escape(question)}</li>`).join('')}</ol>` : ''}
        ${turn.citations.length ? `<div class="qa-citations" aria-label="关联原文">${turn.citations.map(citation=>`<button class="qa-citation" data-qa-paragraph="${citation.paragraph}"><strong>第 ${citation.paragraph} 段 ↗</strong><span lang="en">${this.escape(citation.quote)}</span></button>`).join('')}</div>` : ''}
        ${turn.warning ? `<p class="qa-warning">${this.escape(turn.warning)}</p>` : ''}
        <div class="qa-turn-actions"><button class="button small" data-qa-favorite="${turn.id}" aria-pressed="${turn.favorite}">${turn.favorite?'★ 已收藏':'☆ 收藏问答'}</button>
        ${turn.id===latestQuiz?.id ? `<button class="button small" data-qa-feedback="${turn.id}">回答这组题</button>` : ''}
        <button class="button small" data-qa-follow="${turn.id}">继续追问</button></div>` : turn.status==='pending'
        ? '<p class="qa-note">DeepSeek 正在生成回答…可以关闭面板，稍后查看历史。</p>'
        : `<p class="qa-warning">${this.escape(turn.error)}</p><button class="button small" data-qa-retry="${turn.id}">修改后重试</button>`}
      </article>`).join('') : '<div class="qa-empty"><strong>从一个问题开始理解文章</strong><p>可以问主旨与论证，也可以选中原文追问词义和句法。选择“引导我思考”，由 AI 一次提一个小问题。</p><p>还可以生成阅读前引导问题，或在读完后做理解检查。</p></div>';
    if (target) {
      const row = this.$('#qa-turn-'+target);
      if (row) list.scrollTop = row.offsetTop - list.offsetTop;
    } else list.scrollTop = list.scrollHeight;
    this.syncSending();
  }

  syncSending() {
    const pending = this.turns.some(turn=>turn.status==='pending');
    const busy = Boolean(this.requesting || pending);
    this.$('#qa-send').disabled = busy;
    this.$('#qa-question').disabled = busy;
    this.$('#qa-mode').disabled = busy;
    this.$('#qa-scope').disabled = busy || ['preread','quiz','feedback'].includes(this.$('#qa-mode').value);
    this.$('[data-qa-action="new"]').disabled = Boolean(this.requesting);
    this.$('#qa-send').textContent = this.requesting || pending ? '正在回答…' : this.$('#qa-mode').value==='feedback' ? '提交作答' : '发送问题';
    this.$('[data-qa-action="delete"]').disabled = !this.threadId || pending || Boolean(this.requesting);
  }

  onChange(event) {
    if (event.target.id==='qa-mode') {
      const mode = event.target.value, field = this.$('#qa-question');
      if (!field.value.trim() || field.value===this.suggested) field.value = this.prompts[mode] || '';
      this.suggested = this.prompts[mode] || '';
      this.syncSelection(); this.syncSending();
    } else if (event.target.id==='qa-scope') this.syncSelection();
  }

  async send() {
    if (this.requesting || this.turns.some(turn=>turn.status==='pending')) return;
    if (!this.config?.configured) { this.$('#qa-configuration').open=true; this.status('请先保存 DeepSeek API 密钥。'); return; }
    if (!this.$('#qa-question-form').reportValidity()) return;
    const query = {question:this.$('#qa-question').value.trim(), mode:this.$('#qa-mode').value, scope:this.$('#qa-scope').value,
      selected_text:this.selection?.text || '', selection_paragraphs:this.selection?.paragraphs || [], request_id:crypto.randomUUID()};
    if (!query.question) { this.status('请先输入问题或作答。'); return; }
    this.requesting = true; this.syncSending(); this.status('正在向 DeepSeek 提问，请稍候…');
    const startVersion = this.loadVersion;
    let id = this.threadId;
    try {
      if (!id) {
        const thread = await this.api(`/articles/${this.article.id}/qa/threads`,{method:'POST',body:{}});
        id = thread.id;
        if (this.destroyed) return;
        if (this.loadVersion===startVersion && !this.threadId) this.threadId = id;
      }
      const result = await this.api(`/qa/threads/${id}/turns`,{method:'POST',body:query,timeoutMs:180000});
      if (this.destroyed) return;
      if (this.threadId===id) {
        this.$('#qa-question').value = '';
        await this.loadThread(id,null,true);
        if (query.mode==='quiz' && result.turn.status==='complete') { this.$('#qa-mode').value='feedback'; this.syncSelection(); }
      }
      await this.loadHistory();
    } catch (error) {
      if (!this.destroyed) {
        if (id && this.threadId===id) await this.loadThread(id,null,true);
        this.status(error.message);
        await this.loadHistory();
      }
    } finally {
      this.requesting = false;
      if (!this.destroyed) { this.syncSending(); this.schedulePoll(); }
    }
  }

  async onSubmit(event) {
    event.preventDefault();
    if (event.target.id==='qa-config-form') await this.saveConfig();
    else if (event.target.id==='qa-question-form') await this.send();
  }

  restoreTurn(turn, mode) {
    this.selection = turn.selected_text ? {text:turn.selected_text, paragraphs:turn.selection_paragraphs} : null;
    this.$('#qa-mode').value = mode;
    this.$('#qa-scope').value = turn.scope;
    this.$('#qa-question').value = mode === 'feedback' ? '' : turn.status==='failed' ? turn.question : '';
    this.syncSelection(); this.syncSending();
    this.$('#qa-question').focus({preventScroll:true});
  }

  async onClick(event) {
    const button = event.target.closest('button,a');
    if (!button) return;
    try {
      if (button.dataset.qaThread) { await this.loadThread(Number(button.dataset.qaThread),Number(button.dataset.qaTurn)||null); return; }
      if (button.dataset.qaTab) { this.historyTab=button.dataset.qaTab; await this.loadHistory(); return; }
      if (button.dataset.qaParagraph) { this.jumpTarget=Number(button.dataset.qaParagraph)-1; this.dialog.close(); return; }
      if (button.dataset.qaFavorite) {
        button.disabled = true;
        const turn = this.turns.find(turn=>turn.id===Number(button.dataset.qaFavorite));
        const result = await this.api('/qa/turns/'+turn.id,{method:'PATCH',body:{favorite:!turn.favorite}});
        if (this.destroyed) return;
        Object.assign(turn,result);
        const position=this.$('#qa-messages').scrollTop;
        this.renderTurns();this.$('#qa-messages').scrollTop=position;
        await this.loadHistory();return;
      }
      for (const [key,mode] of [['qaFeedback','feedback'],['qaFollow',null],['qaRetry',null]]) {
        if (button.dataset[key]) {
          const turn=this.turns.find(turn=>turn.id===Number(button.dataset[key]));
          this.restoreTurn(turn,mode || (turn.mode==='quiz'?'ask':turn.mode));return;
        }
      }
      switch (button.dataset.qaAction) {
        case 'open': await this.open(); break;
        case 'open-selection': await this.open(true); break;
        case 'close': this.dialog.close(); break;
        case 'new': if(!this.requesting)this.newConversation(); break;
        case 'refresh': if(this.threadId)await this.loadThread(this.threadId,null,true);await this.loadHistory();await this.loadConfig(); break;
        case 'more-history': await this.loadHistory(true); break;
        case 'connection': await this.connection(button); break;
        case 'clear-key':
          await this.api('/ai/settings',{method:'PATCH',body:{clear_key:true}});
          await this.loadConfig();if(!this.destroyed)this.$('#qa-config-status').textContent='本机保存的密钥已移除。';break;
        case 'delete': this.$('#qa-delete-confirm').hidden=false; break;
        case 'cancel-delete': this.$('#qa-delete-confirm').hidden=true; break;
        case 'confirm-delete':
          await this.api('/qa/threads/'+this.threadId,{method:'DELETE',body:{}});
          if(!this.destroyed){this.newConversation();await this.loadHistory();}break;
      }
    } catch (error) { if(!this.destroyed){this.status(error.message);button.disabled=false;} }
  }

  destroy() {
    this.destroyed=true;this.events.abort();
    clearTimeout(this.pollTimer);clearTimeout(this.highlightTimer);
    this.dialog.close();this.$('#qa-api-key').value='';this.$('#qa-messages').replaceChildren();
  }
};
