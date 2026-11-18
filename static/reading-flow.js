/* Deferred vocabulary processing and gentle reading-session reminders. */
'use strict';

window.MarginFlow = class MarginFlow {
  constructor({root, article, reader, settings, api, toast, getWords, onWords, lookup}) {
    Object.assign(this, {root, article, reader, settings, api, toast, getWords, onWords, lookup});
    this.pending = [];
    this.definitions = new Map();
    this.expanded = new Set();
    this.manual = new Map();
    this.inFlight = new Set();
    this.failed = new Map();
    this.busy = new Set();
    this.events = new AbortController();
    this.dialog = document.getElementById('pending-words-dialog');
    this.filter = 'all';
    this.elapsed = 0;
    this.reminderVisible = false;
    const listen = (element, type, fn, options = {}) => element.addEventListener(type, fn.bind(this), {...options, signal: this.events.signal});
    listen(document, 'click', this.onClick);
    listen(this.dialog, 'change', this.onFilter);
    listen(this.dialog, 'input', function (event) {
      if (event.target.matches('[data-pending-meaning]')) this.manual.set(Number(event.target.dataset.pendingMeaning), event.target.value);
    });
    listen(this.dialog, 'toggle', this.onToggle, {capture: true});
    listen(this.dialog, 'close', function () {
      if (!this.destroyed && this.dialogAnchor) this.reader.restoreAnchor(this.dialogAnchor);
      this.reader.scroller.focus({preventScroll: true});
    });
    this.apply(settings);
    this.ready = this.reload();
  }

  url(suffix = '') { return `/articles/${this.article.id}/pending-words${suffix}`; }
  escape(value) { return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])); }
  normalize(word) { return word.toLowerCase().replaceAll('’', "'"); }
  vocabulary(word) { return this.getWords().find(entry => this.normalize(entry.word) === this.normalize(word)); }
  keepPlace(change) {
    if (this.destroyed) return;
    const anchor = this.reader.captureAnchor();
    change();
    this.reader.restoreAnchor(anchor);
  }

  apply(settings) {
    const providerChanged = this.provider && this.provider !== settings.dictionary_provider;
    this.provider = settings.dictionary_provider || 'auto';
    this.settings = settings;
    if (providerChanged) {
      this.definitions.clear();
      this.renderQueue();
      this.expanded.forEach(id => this.loadDefinition(id));
    }
    this.root.dataset.hintDensity = settings.hint_density;
    if (this.mode !== settings.reading_mode) {
      this.root.querySelector('.reader-margin').classList.remove('mobile-open');
      this.root.querySelectorAll('.word.selected').forEach(word => word.classList.remove('selected'));
      this.mode = settings.reading_mode;
    }
    if (this.interval !== settings.reminder_minutes || settings.hint_density === 'quiet') {
      this.interval = settings.reminder_minutes;
      this.dismissReminder();
    }
    this.root.querySelector('#flow-mode-hint').textContent = this.mode === 'fluent'
      ? '点词先暂存，读完一段再处理。' : '点词打开查词卡片，按需展开释义。';
    this.root.querySelector('.lookup-form input').placeholder = this.mode === 'fluent'
      ? '输入单词或短语主动查词' : '输入单词、短语，或划选正文';
    this.root.querySelector('.reader-hint').lastChild.textContent = this.mode === 'fluent'
      ? '单击或划选一个单词暂存；段末可集中处理，也可随时打开待查词。' : '单击或划选一个单词打开查词卡片；释义可展开、收起，也可暂存稍后处理。';
    this.refreshMarks();
  }

  refreshMarks() {
    const vocabulary = new Map(this.getWords().map(word => [this.normalize(word.word), word]));
    const pending = new Set(this.pending.map(word => `${word.paragraph_index}:${word.word}`));
    this.reader.paragraphs.forEach((paragraph, index) => {
      const seen = new Set();
      paragraph.querySelectorAll('.word').forEach(span => {
        const key = this.normalize(span.dataset.word), saved = vocabulary.get(key);
        const suggested = saved && !(this.settings.suppress_mastered && saved.mastery === 'mastered');
        span.classList.toggle('saved', Boolean(saved));
        span.classList.toggle('learning-cue', Boolean(suggested && (this.settings.hint_density === 'detailed' || !seen.has(key))));
        span.classList.toggle('pending-cue', pending.has(`${index}:${key}`));
        if (saved) span.dataset.mastery = saved.mastery;
        else delete span.dataset.mastery;
        seen.add(key);
      });
      const count = this.pending.filter(word => word.paragraph_index === index).length;
      const finish = paragraph.querySelector('[data-flow-paragraph]');
      finish.hidden = count === 0 || this.settings.hint_density === 'quiet';
      finish.textContent = `读完此段 · 处理 ${count} 个待查词`;
    });
    this.root.querySelector('#pending-word-count').textContent = this.loadError ? '重试' : this.loading ? '…' : this.pending.length;
  }

  async reload() {
    this.loading = true;
    this.loadError = '';
    this.refreshMarks();
    try {
      const result = await this.api(this.url());
      if (this.destroyed) return;
      this.pending = result.words;
    } catch (error) {
      if (!this.destroyed) this.loadError = error.message;
    } finally {
      this.loading = false;
      if (!this.destroyed) this.keepPlace(() => { this.refreshMarks(); this.renderQueue(); });
    }
  }

  selectWord(word, paragraph) {
    const index = paragraph ? this.reader.paragraphs.indexOf(paragraph) : -1;
    if (this.mode === 'fluent') this.deferWord(word, index);
    else this.lookup(word, paragraph?.dataset.plain || '', {paragraphIndex: index, fullParagraph: true});
  }

  async deferWord(word, index = -1) {
    word = this.normalize(word.trim());
    const key = `${index}:${word}`;
    if (this.inFlight.has(key)) return;
    this.inFlight.add(key);
    this.feedback(`正在暂存 ${word}…`);
    try {
      await this.ready;
      // A word already selected should still be saved if navigation finishes first.
      const result = await this.api(this.url(), {method: 'POST', body: {word, paragraph_index: index}, keepalive: true});
      if (this.destroyed) return;
      this.failed.delete(key);
      if (!this.pending.some(entry => entry.id === result.word.id)) this.pending.push(result.word);
      this.keepPlace(() => { this.refreshMarks(); this.renderQueue(); });
      this.feedback(result.duplicate ? `${word} 已在本段待查词中` : `已暂存 ${word}，可以继续阅读`);
    } catch (error) {
      if (!this.destroyed) {
        this.failed.set(key, {word, index});
        this.feedback('暂存未成功，请重试');
        this.toast(error.message, true);
      }
    } finally {
      this.inFlight.delete(key);
      if (!this.destroyed) this.root.querySelector('[data-flow-action="retry-save"]').hidden = this.failed.size === 0;
    }
  }

  feedback(message) {
    if (!this.destroyed) this.root.querySelector('#flow-feedback').textContent = this.settings.hint_density === 'quiet' ? '' : message;
  }

  openQueue(index = 'all') {
    if (document.querySelector('dialog[open]')) return;
    this.filter = String(index);
    this.dialogAnchor = this.reader.captureAnchor();
    this.dismissReminder();
    this.renderQueue();
    this.dialog.showModal();
    this.reader.restoreAnchor(this.dialogAnchor);
  }

  renderQueue() {
    if (this.destroyed) return;
    const list = this.dialog.querySelector('#pending-word-list');
    const scroll = this.dialog.scrollTop;
    const focused = document.activeElement;
    const focusedId = focused?.dataset.pendingId, focusedOp = focused?.dataset.flowOp;
    const editing = focused?.matches('[data-pending-meaning]') ? {
      id: Number(focused.dataset.pendingMeaning), start: focused.selectionStart,
      end: focused.selectionEnd, direction: focused.selectionDirection, scroll: focused.scrollTop,
    } : null;
    if (editing) this.manual.set(editing.id, focused.value);
    const groups = new Map();
    this.pending.forEach(word => groups.set(word.paragraph_index, (groups.get(word.paragraph_index) || 0) + 1));
    if (this.filter !== 'all' && !groups.has(Number(this.filter))) groups.set(Number(this.filter), 0);
    const label = index => index < 0 ? '手动记录' : `第 ${index + 1} 段`;
    this.dialog.querySelector('#pending-filter').innerHTML = `<option value="all">全文 · ${this.pending.length} 个</option>` +
      [...groups].sort(([a], [b]) => a - b).map(([index, count]) => `<option value="${index}">${label(index)} · ${count} 个</option>`).join('');
    this.dialog.querySelector('#pending-filter').value = this.filter;
    this.dialog.querySelector('#pending-queue-summary').textContent = this.filter === 'all'
      ? '按段落集中处理；展开时才查询，收藏或移出后从待查列表移除。' : `${label(Number(this.filter))}的待查词。处理后关闭面板，继续原处阅读。`;
    const entries = this.pending.filter(word => this.filter === 'all' || word.paragraph_index === Number(this.filter));
    if (this.loading) list.innerHTML = '<p class="flow-empty">正在读取待查词…</p>';
    else if (this.loadError) list.innerHTML = `<p class="error-note">${this.escape(this.loadError)}</p><button class="button" data-flow-action="retry-load">重试读取</button>`;
    else if (!entries.length) list.innerHTML = '<p class="flow-empty">这里的待查词已处理完。可以继续阅读，或查看全文待查词。</p>';
    else list.innerHTML = entries.sort((a, b) => a.paragraph_index - b.paragraph_index || a.id - b.id).map(entry => {
      const saved = this.vocabulary(entry.word);
      return `<section class="pending-word" data-pending-row="${entry.id}">
        <div class="pending-heading"><strong lang="en">${this.escape(entry.word)}</strong><span>${label(entry.paragraph_index)}${saved ? ' · 已收藏' : ''}</span></div>
        ${entry.context ? `<blockquote class="pending-context" lang="en">${this.escape(entry.context)}</blockquote>` : ''}
        <details class="pending-definition" data-pending-id="${entry.id}" ${this.expanded.has(entry.id) ? 'open' : ''}><summary>释义 · 按需展开</summary><div class="pending-definition-content">${this.definitionHTML(entry)}</div></details>
        <div class="pending-actions"><button class="button small primary" data-flow-op="collect" data-pending-id="${entry.id}" ${this.busy.has(entry.id) ? 'disabled' : ''}>${saved ? '已收藏，完成处理' : '加入生词本'}</button><button class="button small" data-flow-op="remove" data-pending-id="${entry.id}" ${this.busy.has(entry.id) ? 'disabled' : ''}>移出待查</button></div>
      </section>`;
    }).join('');
    this.dialog.scrollTop = scroll;
    if (editing) {
      const replacement = list.querySelector(`[data-pending-meaning="${editing.id}"]`);
      if (replacement) {
        replacement.focus({preventScroll: true});
        replacement.setSelectionRange(editing.start, editing.end, editing.direction);
        replacement.scrollTop = editing.scroll;
      }
    } else if (focusedId && focusedOp) {
      const replacement = list.querySelector(`[data-flow-op="${focusedOp}"][data-pending-id="${focusedId}"]`);
      (replacement || list.querySelector('summary') || this.dialog.querySelector('[data-flow-action="close-queue"]')).focus({preventScroll: true});
    }
  }

  definitionHTML(entry) {
    const result = this.definitions.get(entry.id);
    if (!result) return '<p class="lookup-note">展开后查询基础释义。</p>';
    if (result.loading) return '<p class="lookup-note">正在查询…</p>';
    if (result.error) return `<p class="error-note">${this.escape(result.error)}</p><button class="button small" data-flow-op="retry-definition" data-pending-id="${entry.id}">重试查词</button>`;
    if (!result.found) return `<p class="lookup-note">${this.escape(result.message)}</p><label>手动填写释义<textarea data-pending-meaning="${entry.id}" maxlength="3000" rows="2">${this.escape(this.manual.get(entry.id) || '')}</textarea></label>`;
    return `${MarginDictionary.pronunciation(result)}<p class="definition"><span class="part-of-speech">${this.escape(result.part_of_speech)}</span>${this.escape(result.meaning)}</p><p class="source-caption">${this.escape(result.source)}${result.cached ? ' · 本地缓存' : ''}</p>${MarginDictionary.knowledge(result, false)}`;
  }

  async loadDefinition(id, retry = false) {
    const entry = this.pending.find(word => word.id === id);
    if (!entry || (this.definitions.has(id) && !retry) || this.definitions.get(id)?.loading) return;
    this.definitions.set(id, {loading: true});
    this.updateDefinition(entry);
    const provider = this.provider;
    try {
      const result = await this.api('/dictionary?word=' + encodeURIComponent(entry.word) + '&provider=' + encodeURIComponent(provider));
      if (!this.destroyed && provider === this.provider) this.definitions.set(id, result);
    } catch (error) { if (!this.destroyed && provider === this.provider) this.definitions.set(id, {error: error.message}); }
    if (!this.destroyed && provider === this.provider) this.updateDefinition(entry);
  }

  updateDefinition(entry) {
    const target = this.dialog.querySelector(`[data-pending-row="${entry.id}"] .pending-definition-content`);
    if (target) target.innerHTML = this.definitionHTML(entry);
  }

  onToggle(event) {
    const details = event.target;
    if (!details.matches('details[data-pending-id]')) return;
    const id = Number(details.dataset.pendingId);
    if (details.open) { this.expanded.add(id); this.loadDefinition(id); }
    else this.expanded.delete(id);
  }

  onFilter(event) {
    if (event.target.id === 'pending-filter') { this.filter = event.target.value; this.renderQueue(); }
  }

  async processWord(id, collect) {
    const entry = this.pending.find(word => word.id === id);
    if (!entry || this.busy.has(id)) return;
    if (collect && !this.vocabulary(entry.word)) {
      const result = this.definitions.get(id);
      if (!result || result.loading || result.error || (!result.found && !this.manual.get(id)?.trim())) {
        const details = this.dialog.querySelector(`details[data-pending-id="${id}"]`);
        details.open = true;
        details.querySelector('summary').focus({preventScroll: true});
        this.toast('请先展开查看释义；未收录的词可以手动填写。');
        return;
      }
    }
    this.busy.add(id);
    this.dialog.querySelectorAll(`[data-pending-id="${id}"] button, button[data-pending-id="${id}"]`).forEach(control => { control.disabled = true; });
    try {
      let duplicate = false;
      if (collect) {
        const definition = this.definitions.get(id) || {};
        const result = await this.api(this.url(`/${id}/collect`), {method: 'POST', body: {
          meaning: definition.meaning || this.manual.get(id) || '', phonetic: definition.phonetic || '', part_of_speech: definition.part_of_speech || '',
        }});
        duplicate = result.duplicate;
      } else await this.api(this.url(`/${id}`), {method: 'DELETE', body: {}});
      if (this.destroyed) return;
      this.pending = this.pending.filter(word => word.id !== id);
      this.expanded.delete(id);
      this.keepPlace(() => { this.refreshMarks(); this.renderQueue(); });
      if (collect) {
        await this.onWords();
        if (!this.destroyed) {
          this.refreshMarks(); this.renderQueue();
          this.dialog.querySelector('#pending-queue-summary').textContent = duplicate
            ? `${entry.word} 已在生词本中，本次处理已完成，原记录保持不变。`
            : `${entry.word} 已加入生词本，原文语境已保留。`;
        }
      }
    } catch (error) { if (!this.destroyed) this.toast(error.message, true); }
    finally {
      this.busy.delete(id);
      if (!this.destroyed) this.dialog.querySelectorAll(`button[data-pending-id="${id}"]`).forEach(control => { control.disabled = false; });
    }
  }

  onClick(event) {
    const paragraph = event.target.closest('[data-flow-paragraph]');
    if (paragraph) { this.openQueue(paragraph.dataset.flowParagraph); return; }
    const operation = event.target.closest('[data-flow-op]');
    if (operation) {
      const id = Number(operation.dataset.pendingId);
      if (operation.dataset.flowOp === 'retry-definition') this.loadDefinition(id, true);
      else this.processWord(id, operation.dataset.flowOp === 'collect');
      return;
    }
    const button = event.target.closest('[data-flow-action]');
    if (!button) return;
    switch (button.dataset.flowAction) {
      case 'queue': this.openQueue(); break;
      case 'current-paragraph': this.openQueue(this.reader.current); break;
      case 'close-queue': this.dialog.close(); break;
      case 'retry-load': this.ready = this.reload(); break;
      case 'retry-save': [...this.failed.values()].forEach(({word, index}) => this.deferWord(word, index)); break;
      case 'reminder-later': this.dismissReminder(5 * 60); break;
      case 'reminder-dismiss': this.dismissReminder(); break;
    }
  }

  tick(seconds) {
    if (this.destroyed || !this.interval || this.settings.hint_density === 'quiet' || this.reminderVisible ||
        document.hidden || document.querySelector('dialog[open]') ||
        (this.reader.overlayMargin() && this.root.querySelector('.reader-margin.mobile-open')) ||
        (this.root.querySelector('.reader-margin')?.contains(document.activeElement) && document.activeElement.matches('input,textarea,select,[contenteditable="true"]'))) return;
    this.elapsed += seconds;
    if (this.elapsed < this.nextReminder || this.reader.selectionActive()) return;
    this.reminderVisible = true;
    const panel = this.root.querySelector('#flow-reminder');
    panel.querySelector('p').textContent = `这一阶段已活跃阅读约 ${Math.max(1, Math.floor(this.elapsed / 60))} 分钟。${this.pending.length ? `有 ${this.pending.length} 个待查词，可以集中处理。` : '可以稍作休息，再继续下一段。'}`;
    panel.querySelector('[data-flow-action="queue"]').hidden = this.pending.length === 0;
    panel.hidden = false;
  }

  dismissReminder(seconds = this.settings.reminder_minutes * 60) {
    this.elapsed = 0;
    this.nextReminder = seconds;
    this.reminderVisible = false;
    this.root.querySelector('#flow-reminder').hidden = true;
  }

  destroy() {
    this.destroyed = true;
    this.events.abort();
    this.dialog.close();
    this.dialog.querySelector('#pending-word-list').replaceChildren();
    delete this.root.dataset.hintDensity;
  }
};
