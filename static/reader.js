/* Reader-only interactions. No build step or external browser library is needed. */
'use strict';

window.MarginReader = class MarginReader {
  constructor({root, settings, onChange, onLayout, toast}) {
    this.root = root;
    this.settings = settings;
    this.onChange = onChange;
    this.onLayout = onLayout;
    this.toast = toast;
    this.scroller = root.querySelector('.reader-scroll');
    this.paper = root.querySelector('.reader-paper');
    this.body = root.querySelector('#article-body');
    this.paragraphs = [...this.body.querySelectorAll('.paragraph')];
    this.current = 0;
    this.destroyed = false;
    this.restoring = false;
    this.layoutVersion = 0;
    this.ignoreTapUntil = 0;
    this.events = new AbortController();
    const listen = (target, event, callback, options = {}) => target.addEventListener(
      event, callback.bind(this), {...options, signal: this.events.signal});
    listen(document, 'click', this.onClick);
    listen(document, 'change', this.onSettingChange);
    listen(document, 'input', this.onSettingPreview);
    listen(document, 'keydown', this.onKeyDown);
    listen(document, 'fullscreenchange', this.onFullscreenChange);
    listen(this.scroller, 'scroll', this.onScroll, {passive: true});
    listen(this.scroller, 'touchstart', this.onTouchStart, {passive: true});
    listen(this.scroller, 'touchmove', this.onTouchMove, {passive: false});
    listen(this.scroller, 'touchend', this.onTouchEnd, {passive: true});
    listen(this.scroller, 'touchcancel', function () { this.touch = null; }, {passive: true});
    this.buildOutline();
    this.apply(settings);
    this.remember();
    this.dimensions = this.measure();
    this.observer = new ResizeObserver(() => {
      if (this.destroyed) return;
      const dimensions = this.measure();
      this.updateLayoutNote();
      if (dimensions === this.dimensions) return;
      this.dimensions = dimensions;
      if (!this.restoring && this.anchor) this.restoreAnchor(this.anchor);
    });
    this.observer.observe(this.paper);
    this.observer.observe(this.scroller);
  }

  measure() { return `${this.paper.clientWidth}:${this.scroller.clientHeight}`; }
  selectionActive() { return Boolean(window.getSelection()?.toString().trim()); }
  ignoreWordTap() { return performance.now() < this.ignoreTapUntil; }
  overlayMargin() {
    return matchMedia('(max-width: 1000px)').matches || this.settings.focus_mode || this.settings.reading_mode === 'fluent' || Boolean(document.fullscreenElement);
  }

  apply(settings, anchor = null) {
    this.settings = settings;
    const fonts = {
      serif: 'Georgia, "Times New Roman", serif',
      classic: '"Times New Roman", Georgia, serif',
      sans: 'Arial, "Microsoft YaHei", sans-serif',
      mono: 'Consolas, "Courier New", monospace',
    };
    document.body.classList.add('reader-active');
    document.body.classList.toggle('reader-focused', settings.focus_mode);
    document.body.classList.toggle('reader-fullscreen', Boolean(document.fullscreenElement));
    document.body.dataset.readerTheme = settings.theme;
    document.body.dataset.readingMode = settings.reading_mode;
    this.paper.style.setProperty('--reader-font', `${settings.font_size}px`);
    this.paper.style.setProperty('--reader-line', settings.line_height);
    this.paper.style.setProperty('--reader-typeface', fonts[settings.font_family] || fonts.serif);
    this.paper.style.setProperty('--reader-width', `${settings.content_width}px`);
    this.paper.style.setProperty('--paragraph-space', `${settings.paragraph_spacing}em`);
    this.body.dataset.layout = settings.layout;
    this.body.classList.toggle('show-paragraph-numbers', settings.paragraph_numbers);
    this.syncControls();
    this.updateLayoutNote();
    if (anchor) this.restoreAnchor(anchor);
  }

  syncControls() {
    document.querySelectorAll('[data-reader-setting]').forEach(input => {
      const value = this.settings[input.dataset.readerSetting];
      if (input.type === 'checkbox') input.checked = value;
      else if (input.type === 'radio') input.checked = input.value === value;
      else input.value = value;
    });
    Object.entries(this.settings).forEach(([key, value]) => this.updateOutput(key, value));
    const focus = this.root.querySelector('[data-reader-action="focus"]');
    focus.setAttribute('aria-pressed', String(this.settings.focus_mode));
    focus.querySelector('span').textContent = this.settings.focus_mode ? '退出专注' : '专注';
    const fullscreen = this.root.querySelector('[data-reader-action="fullscreen"]');
    fullscreen.setAttribute('aria-pressed', String(Boolean(document.fullscreenElement)));
    fullscreen.querySelector('span').textContent = document.fullscreenElement ? '退出全屏' : '全屏';
    this.root.querySelector('#font-label').textContent = this.settings.font_size;
    this.root.querySelector('[data-action="font-down"]').disabled = this.settings.font_size <= 16;
    this.root.querySelector('[data-action="font-up"]').disabled = this.settings.font_size >= 28;
  }

  updateOutput(key, value) {
    const output = document.querySelector(`[data-reader-output="${key}"]`);
    if (!output) return;
    output.textContent = `${value}${key === 'line_height' ? ' 倍' : key === 'paragraph_spacing' ? ' em' : ' px'}`;
  }

  updateLayoutNote() {
    const note = this.root.querySelector('#reader-layout-note');
    const double = this.settings.layout === 'double';
    note.hidden = !double;
    note.textContent = this.body.clientWidth >= 680
      ? '双栏阅读：每行先左后右，再向下阅读。'
      : '当前正文区域较窄，已自动显示单栏；增大宽度或开启专注模式可使用双栏。';
  }

  captureAnchor() {
    const top = this.scroller.getBoundingClientRect().top + 20;
    const candidates = [this.root.querySelector('.reader-title'), ...this.paragraphs, this.root.querySelector('.reader-end')];
    let node = candidates.find(element => element.getBoundingClientRect().bottom > top) || candidates.at(-1);
    const selected = this.paragraphs[this.current];
    if (node.classList.contains('paragraph') && selected &&
        Math.abs(node.getBoundingClientRect().top - selected.getBoundingClientRect().top) < 2 &&
        selected.getBoundingClientRect().bottom > top) node = selected;
    const rect = node.getBoundingClientRect();
    return {node, fraction: (top - rect.top) / Math.max(rect.height, 1), atTop: this.scroller.scrollTop < 2};
  }

  restoreAnchor(anchor) {
    if (this.destroyed || !anchor.node.isConnected) return;
    this.restoring = true;
    const version = ++this.layoutVersion;
    const rect = anchor.node.getBoundingClientRect();
    const delta = rect.top + anchor.fraction * rect.height - this.scroller.getBoundingClientRect().top - 20;
    this.scroller.scrollTop = anchor.atTop ? 0 : this.scroller.scrollTop + delta;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (this.destroyed || version !== this.layoutVersion) return;
      this.restoring = false;
      this.remember();
      this.onLayout();
    }));
  }

  remember() {
    if (this.destroyed) return;
    this.anchor = this.captureAnchor();
    this.updateCurrent();
  }

  onScroll() {
    if (this.restoring || this.scrollFrame) return;
    this.scrollFrame = requestAnimationFrame(() => {
      this.scrollFrame = null;
      if (!this.destroyed && !this.restoring) this.remember();
    });
  }

  updateCurrent() {
    const top = this.scroller.getBoundingClientRect().top + 24;
    let index = this.paragraphs.findIndex(paragraph => paragraph.getBoundingClientRect().bottom > top);
    if (index < 0) index = this.paragraphs.length - 1;
    // In a two-column row, preserve an explicitly selected right-hand paragraph.
    const selected = this.paragraphs[this.current]?.getBoundingClientRect();
    const first = this.paragraphs[index]?.getBoundingClientRect();
    if (selected && first && selected.bottom > top && Math.abs(selected.top - first.top) < 2) index = this.current;
    this.current = Math.max(0, index);
    document.querySelectorAll('[data-reader-jump]').forEach(button => {
      if (Number(button.dataset.readerJump) === this.current) button.setAttribute('aria-current', 'location');
      else button.removeAttribute('aria-current');
    });
    this.root.querySelector('#reader-position-label').textContent = `第 ${this.current + 1} / ${this.paragraphs.length} 段`;
    this.root.querySelector('[data-reader-action="previous"]').disabled = this.current === 0;
    this.root.querySelector('[data-reader-action="next"]').disabled = this.current === this.paragraphs.length - 1;
  }

  buildOutline() {
    const list = document.createElement('ol');
    this.paragraphs.forEach((paragraph, index) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.readerJump = index;
      const number = document.createElement('strong');
      number.textContent = String(index + 1).padStart(2, '0');
      const excerpt = document.createElement('span');
      const plain = paragraph.dataset.plain;
      excerpt.textContent = plain.slice(0, 100) + (plain.length > 100 ? '…' : '');
      button.append(number, excerpt);
      item.append(button);
      list.append(item);
    });
    document.querySelector('#reader-toc-list').replaceChildren(list);
  }

  jump(index, focus = false) {
    index = Math.max(0, Math.min(this.paragraphs.length - 1, index));
    this.current = index;
    const paragraph = this.paragraphs[index];
    const top = this.scroller.scrollTop + paragraph.getBoundingClientRect().top - this.scroller.getBoundingClientRect().top - 20;
    this.scroller.scrollTo({top, behavior: 'instant'});
    if (focus) paragraph.focus({preventScroll: true});
    this.remember();
  }

  showDialog(id) {
    const dialog = document.getElementById(id);
    if (dialog.open || document.querySelector('dialog[open]')) return;
    this.syncControls();
    this.updateCurrent();
    dialog.showModal();
    if (id === 'reader-toc-dialog') {
      const current = dialog.querySelector('[aria-current]');
      current?.scrollIntoView({block: 'nearest'});
      current?.focus({preventScroll: true});
    }
  }

  focusMode() {
    this.root.querySelector('.reader-margin').classList.remove('mobile-open');
    this.onChange({focus_mode: !this.settings.focus_mode});
  }

  async fullscreen() {
    if (this.fullscreenBusy) return;
    if (!document.fullscreenElement && (!document.fullscreenEnabled || !document.documentElement.requestFullscreen)) {
      this.toast('当前浏览器不支持网页全屏，可以使用专注模式。');
      return;
    }
    this.fullscreenBusy = true;
    try {
      this.anchor = this.captureAnchor();
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      this.toast('未能进入全屏，请使用工具栏重试，或切换专注模式。', true);
    } finally { this.fullscreenBusy = false; }
  }

  onFullscreenChange() { this.apply(this.settings, this.anchor); }

  onClick(event) {
    const jump = event.target.closest('[data-reader-jump]');
    if (jump) {
      jump.closest('dialog').close();
      this.jump(Number(jump.dataset.readerJump), true);
      return;
    }
    const button = event.target.closest('[data-reader-action]');
    if (!button) return;
    switch (button.dataset.readerAction) {
      case 'settings': this.showDialog('reader-settings-dialog'); break;
      case 'toc': this.showDialog('reader-toc-dialog'); break;
      case 'help': this.showDialog('reader-help-dialog'); break;
      case 'close-dialog': button.closest('dialog').close(); break;
      case 'focus': this.focusMode(); break;
      case 'fullscreen': this.fullscreen(); break;
      case 'previous': this.jump(this.current - 1); break;
      case 'next': this.jump(this.current + 1); break;
      case 'lookup':
        this.root.querySelector('.reader-margin').classList.add('mobile-open');
        this.root.querySelector('.lookup-form input').focus({preventScroll: true});
        break;
      case 'reset': this.onChange(JSON.parse(document.body.dataset.readerDefaults)); break;
      case 'retry-settings': this.onChange({...this.settings}); break;
    }
  }

  onSettingChange(event) {
    const input = event.target.closest('[data-reader-setting]');
    if (!input) return;
    const value = input.type === 'checkbox' ? input.checked : ['range', 'number'].includes(input.type) ? Number(input.value) : input.value;
    if (input.type === 'number' && !input.reportValidity()) { this.syncControls(); return; }
    this.onChange({[input.dataset.readerSetting]: value});
  }

  onSettingPreview(event) {
    if (event.target.matches('input[type="range"][data-reader-setting]')) {
      this.updateOutput(event.target.dataset.readerSetting, event.target.value);
    }
  }

  onKeyDown(event) {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || document.querySelector('dialog[open]')) return;
    if (event.key === 'Escape') {
      const margin = this.root.querySelector('.reader-margin');
      if (this.overlayMargin() && margin.classList.contains('mobile-open')) margin.classList.remove('mobile-open');
      else if (!document.fullscreenElement && this.settings.focus_mode) this.focusMode();
      return;
    }
    if (!this.settings.shortcuts_enabled || event.target.isContentEditable || event.target.closest('input,textarea,select,button,a') || this.selectionActive()) return;
    const key = event.key.toLowerCase();
    const actions = {
      f: () => this.focusMode(), g: () => this.fullscreen(),
      t: () => this.showDialog('reader-toc-dialog'), s: () => this.showDialog('reader-settings-dialog'),
      '?': () => this.showDialog('reader-help-dialog'),
      n: () => this.onChange({paragraph_numbers: !this.settings.paragraph_numbers}),
      c: () => this.onChange({layout: this.settings.layout === 'single' ? 'double' : 'single'}),
      '[': () => this.jump(this.current - 1), ']': () => this.jump(this.current + 1),
      '-': () => this.onChange({font_size: Math.max(16, this.settings.font_size - 1)}),
      '+': () => this.onChange({font_size: Math.min(28, this.settings.font_size + 1)}),
      '=': () => this.onChange({font_size: Math.min(28, this.settings.font_size + 1)}),
      home: () => this.scroller.scrollTo({top: 0}), end: () => this.scroller.scrollTo({top: this.scroller.scrollHeight}),
      ' ': () => this.page(event.shiftKey ? -1 : 1),
      pagedown: () => this.page(1), pageup: () => this.page(-1),
    };
    if (!actions[key]) return;
    event.preventDefault();
    if (event.repeat && ['f', 'g', 't', 's', 'n', 'c', '?'].includes(key)) return;
    actions[key]();
  }

  page(direction) { this.scroller.scrollBy({top: direction * this.scroller.clientHeight * .85, behavior: 'instant'}); }

  onTouchStart(event) {
    this.touch = null;
    if (!this.settings.gestures_enabled || event.touches.length !== 1 || this.selectionActive() || event.target.closest('button,a,input,select,textarea')) return;
    const point = event.touches[0];
    if (point.clientX < 24 || point.clientX > innerWidth - 24) return;
    this.touch = {id: point.identifier, x: point.clientX, y: point.clientY, time: performance.now(), scroll: this.scroller.scrollTop};
  }

  onTouchMove(event) {
    if (event.touches.length !== 1) { this.touch = null; return; }
    if (!this.touch) return;
    const point = event.touches[0];
    const dx = Math.abs(point.clientX - this.touch.x), dy = Math.abs(point.clientY - this.touch.y);
    if (dx + dy > 12) this.ignoreTapUntil = performance.now() + 700;
    // Claim only a deliberate horizontal gesture, leaving scroll, pinch and selection native.
    if (event.cancelable && dx > 24 && dx > dy * 2 && !this.selectionActive() && performance.now() - this.touch.time < 650) event.preventDefault();
  }

  onTouchEnd(event) {
    const start = this.touch;
    this.touch = null;
    if (!start || event.touches.length || this.selectionActive() || performance.now() - start.time > 650) return;
    const point = [...event.changedTouches].find(touch => touch.identifier === start.id);
    if (!point) return;
    const dx = point.clientX - start.x, dy = point.clientY - start.y;
    if (Math.abs(dx) < 80 || Math.abs(dx) < Math.abs(dy) * 2 || Math.abs(this.scroller.scrollTop - start.scroll) > 24) return;
    this.ignoreTapUntil = performance.now() + 700;
    this.jump(this.current + (dx < 0 ? 1 : -1));
  }

  destroy() {
    this.destroyed = true;
    this.events.abort();
    this.observer.disconnect();
    cancelAnimationFrame(this.scrollFrame);
    ['reader-settings-dialog', 'reader-toc-dialog', 'reader-help-dialog'].forEach(id => document.getElementById(id).close());
    document.body.classList.remove('reader-active', 'reader-focused', 'reader-fullscreen');
    delete document.body.dataset.readerTheme;
    delete document.body.dataset.readingMode;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  }
};
