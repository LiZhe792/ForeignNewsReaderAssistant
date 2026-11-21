/* Margin's frontend is intentionally buildless. The application logic lives in Python. */
'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const icons = {
  compass:'<circle cx="12" cy="12" r="9"/><path d="m16 8-2.5 5.5L8 16l2.5-5.5L16 8Z"/>',
  bookmark:'<path d="M6 4h12v17l-6-4-6 4V4Z"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18m-13 4h2m4 0h2m-8 3h2"/>',
  sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  settings:'<path d="m9 3-1 3-3 1-1 3-2 2 2 2 1 3 3 1 1 3h6l1-3 3-1 1-3 2-2-2-2-1-3-3-1-1-3H9Z"/><circle cx="12" cy="12" r="3"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  'arrow-right':'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  'arrow-left':'<path d="M20 12H4m6-6-6 6 6 6"/>',
  'chevron-left':'<path d="m14 6-6 6 6 6"/>',
  'chevron-right':'<path d="m10 6 6 6-6 6"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  book:'<path d="M12 5C8 3 5 3 2 4v16c3-1 6-1 10 1 4-2 7-2 10-1V4c-3-1-6-1-10 1v16Z"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  leaf:'<path d="M20 3C9 3 3 8 4 15c2 9 14 7 16-12ZM4 21 16 9"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
  minus:'<path d="M5 12h14"/>',
  fullscreen:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  edit:'<path d="m14 5 5 5M4 20l5-1L21 7l-5-5L4 14v6Z"/>',
  trash:'<path d="M3 6h18M9 3h6m-10 3 1 15h12l1-15M10 10v7m4-7v7"/>',
  flame:'<path d="M12 2c2 6 7 6 7 12a7 7 0 0 1-14 0c0-3 2-5 3-7 0 4 3 4 4-5Zm0 11c-5 5-2 8 0 8s5-3 0-8Z"/>',
  pen:'<path d="m4 20 4-1L20 7l-3-3L5 16l-1 4Zm10-13 3 3M4 22h16"/>',
  plant:'<path d="M12 21V10M12 15C5 15 3 11 3 5c7 0 9 4 9 10Zm0-3c0-6 3-9 9-9 0 6-3 9-9 9Z"/>',
};
const icon = (name, extra = '') => `<svg class="icon ${extra}" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.book}</svg>`;
function hydrateIcons(root = document) { $$('[data-icon]', root).forEach(node => { node.innerHTML = icon(node.dataset.icon); }); }
function toast(message, error = false) {
  const element = document.createElement('div'); element.className = 'toast' + (error ? ' error' : ''); element.textContent = message;
  $('#toast-region').append(element); setTimeout(() => { element.classList.add('fading'); setTimeout(() => element.remove(), 250); }, 3800);
}
async function api(path, options = {}) {
  const {timeoutMs=55000, ...requestOptions} = options;
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch('/api' + path, { ...requestOptions, signal: requestOptions.signal || controller.signal, headers: { 'Content-Type': 'application/json', ...requestOptions.headers }, body: requestOptions.body === undefined ? undefined : JSON.stringify(requestOptions.body) });
    let payload; try { payload = await response.json(); } catch { throw new Error('服务返回格式异常，请确认 Python 服务正在运行。'); }
    if (!response.ok) { const error = new Error(payload.error || '操作失败，请稍后重试。'); error.status = response.status; throw error; }
    return payload;
  } catch (error) { if (error.name === 'AbortError') throw new Error('请求超时，请稍后重试。'); if (error instanceof TypeError) throw new Error('暂时无法连接本地服务，请确认 Python 程序正在运行。'); throw error; }
  finally { clearTimeout(timeout); }
}

const state = {articles:[], words:[], stats:{}, settings:{font_size:20,line_height:1.9}, view:'library', article:null,
  category:'全部', difficulty:'全部', query:'', mastery:'all', wordQuery:'', lookup:null, month:'', routeVersion:0, lookupVersion:0, importMode:'paste', editWord:null};
const app = $('#app');
let reader = null;
let flow = null;
let lastActive = performance.now(), lastTick = performance.now(), activeSession = null, tickCount = 0;
const readingSessions = new Map();
let writingTrainer = null;
const minutes = seconds => Math.floor((seconds || 0) / 60);
const levelTag = level => `<span class="tag level-${escapeHTML(level.toLowerCase())}" title="根据阅读易读度估算，并非正式 CEFR 测评">${escapeHTML(level)} · ${['A2','B1'].includes(level)?'轻松阅读':level==='B2'?'进阶阅读':'挑战阅读'}</span>`;
const empty = (title, description, action = '', symbol = 'book') => `<div class="empty-state"><div class="empty-icon">${icon(symbol)}</div><h2>${title}</h2><p>${description}</p>${action}</div>`;
const loading = message => `<div class="loading-screen"><span class="spinner"></span><p>${message}</p></div>`;
const intro = (eyebrow, title, description, right='') => `<div class="page-intro"><div><span class="eyebrow">${eyebrow}</span><h1>${title}</h1><p>${description}</p></div>${right}</div>`;

function artwork(category, feature = false) {
  const common = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 220" preserveAspectRatio="xMidYMid slice" aria-hidden="true"';
  if (feature) return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 310" aria-hidden="true"><circle cx="194" cy="129" r="105" fill="#dbe3cd"/><circle cx="212" cy="78" r="32" fill="#e9c68a"/><path d="M0 200 73 117l65 69 41-39 121 77v86H0Z" fill="#b6c6a3"/><path d="m0 253 115-74 79 62 106-26v95H0Z" fill="#91a381"/><path d="m15 310 89-88 63-4 133 92" fill="#e6e6d2"/><path d="m37 310 76-88m139 88-91-88" stroke="#f6f3df" stroke-width="3"/><path d="M92 220v-80c0-16 10-26 25-26h50c15 0 25 10 25 26v80l-50 14-50-14Z" fill="#355444"/><path d="M99 142c0-11 7-18 18-18h50c11 0 18 7 18 18v35H99v-35Z" fill="#b8c9a9"/><path d="M142 125v50" stroke="#355444" stroke-width="4"/><path d="M99 182h85" stroke="#7e9979" stroke-width="2"/><rect x="106" y="192" width="13" height="8" rx="3" fill="#e7c887"/><rect x="165" y="192" width="13" height="8" rx="3" fill="#e7c887"/><path d="M116 222h52l7-14h-66l7 14Z" fill="#243e33"/><path d="m132 114 6-9h11l7 9" fill="none" stroke="#456149" stroke-width="3"/><path d="m141 105 25-17-18-14" fill="none" stroke="#718765" stroke-width="2"/><path d="M21 271v-98m-13 25 13-31 14 31Zm1 18 14-34 16 34" fill="#6c875f" stroke="#6c875f" stroke-width="3"/><path d="M251 260V153m-15 30 15-37 15 37Zm-4 24 19-43 18 43" fill="#56764f" stroke="#56764f" stroke-width="3"/><path d="m12 81 8-3 8 3m22-23 6-2 6 2" fill="none" stroke="#8ca17d" stroke-width="1.4"/></svg>`;
  if (category === '环境') return `<svg ${common}><rect width="400" height="220" fill="#e7e7db"/><circle cx="294" cy="65" r="65" fill="#d9ddc9"/><path d="M0 177c90-17 199-25 400 0v43H0Z" fill="#c9cfb7"/><ellipse cx="192" cy="185" rx="99" ry="11" fill="#b8c1a6"/><path d="M122 178V89h38v89m79 0V89h38v89" fill="#a28c65"/><path d="M117 82h166v15H117Z" fill="#d3be96"/><path d="M177 82V54c0-8 8-12 15-9l14 6v31" fill="none" stroke="#5e7860" stroke-width="9"/><path d="m187 53 36-21 21 33-49 16Z" fill="#4e7054"/><path d="M253 63v20m-8 0h17" stroke="#6e8061" stroke-width="3"/><path d="M71 160c-15-38-17-69 6-87 6 41 9 55-6 87Zm0-6c4-44 24-57 44-53-10 29-20 40-44 53Z" fill="#81936c"/><path d="M71 139v44" stroke="#627851" stroke-width="3"/><path d="m56 166 29 1-5 20H61Z" fill="#b6906d"/><path d="m300 172 30-41 13 9-29 42Z" fill="#a57e57"/><path d="m318 141 15-23 17 12-15 22Z" fill="#5e7562"/></svg>`;
  if (category === '科学') return `<svg ${common}><rect width="400" height="220" fill="#dde8dd"/><circle cx="309" cy="56" r="31" fill="#f3e5b6"/><path d="M0 163h400v57H0Z" fill="#c6d5bd"/><path d="M19 99h42v64H19Zm57-28h39v92H76Zm214 16h35v76h-35Zm49-27h43v103h-43Z" fill="#aebfae"/><path d="M0 199h400" stroke="#edeedd" stroke-width="14"/><path d="M192 189V80m0 41-22-24m22 41 29-30" stroke="#708366" stroke-width="7"/><ellipse cx="191" cy="73" rx="63" ry="49" fill="#73956e"/><ellipse cx="151" cy="100" rx="37" ry="33" fill="#7d9e71"/><ellipse cx="228" cy="104" rx="43" ry="35" fill="#8ca87b"/><path d="M107 185v-43" stroke="#819474" stroke-width="3"/><circle cx="107" cy="132" r="20" fill="#98b285"/><path d="m273 184 8-27 9 27m-9-27v-16" stroke="#697e62" stroke-width="3"/><circle cx="281" cy="135" r="5" fill="#697e62"/></svg>`;
  if (category === '科技') return `<svg ${common}><rect width="400" height="220" fill="#e7e6ee"/><circle cx="97" cy="63" r="62" fill="#dad9e6"/><circle cx="307" cy="181" r="77" fill="#d5d8e4"/><rect x="100" y="39" width="200" height="132" rx="11" fill="#6c7585"/><rect x="108" y="47" width="184" height="113" rx="5" fill="#f6f5f2"/><path d="m100 171-20 15h240l-20-15" fill="#9ba4b0"/><path d="M126 72h78m-78 13h132m-132 13h109" stroke="#c3c9ca" stroke-width="5"/><rect x="126" y="117" width="146" height="26" rx="6" fill="#e3e8e2"/><path d="M137 130h66" stroke="#9aaa98" stroke-width="3"/><path d="m253 125 5 5-5 5" stroke="#92a18b" fill="none" stroke-width="2"/><circle cx="314" cy="49" r="26" fill="#b6bea8"/><path d="M308 43a6 6 0 1 1 10 5c-4 2-5 2-5 6m0 5v1" fill="none" stroke="#fafaf3" stroke-width="3"/><path d="m70 117 4 10 10 4-10 4-4 10-4-10-10-4 10-4Z" fill="#b0a7bf"/></svg>`;
  if (category === '文化') return `<svg ${common}><rect width="400" height="220" fill="#eee5d9"/><rect x="92" y="26" width="216" height="171" rx="3" fill="#c3aa87"/><rect x="104" y="36" width="192" height="151" fill="#e0d3bc"/><path d="M105 85h191m-191 53h191" stroke="#bca17a" stroke-width="5"/><path d="M117 46h14v37h-14Zm18-2h19v39h-19Zm24 10h10v29h-10Z" fill="#7c9279"/><path d="m181 47 14-4 11 38-14 4Z" fill="#ad8267"/><path d="M222 50h13v33h-13Zm18-9h17v42h-17Zm22 13h16v29h-16Z" fill="#b4a373"/><path d="M120 98h18v37h-18Zm23 5h12v32h-12Zm17-5h21v37h-21Z" fill="#ac7e68"/><path d="m202 102 12-5 15 36-12 5Z" fill="#8a9b8d"/><path d="M239 96h15v39h-15Zm20 8h21v31h-21Z" fill="#81907a"/><path d="M117 152h40v9h-40Zm0 12h47v10h-47Z" fill="#91a086"/><path d="M195 153h15v31h-15Zm20-8h19v39h-19Zm24 6h14v33h-14Zm19-1h21v34h-21Z" fill="#b39776"/><path d="M73 192h255" stroke="#ac9474" stroke-width="6"/></svg>`;
  return `<svg ${common}><rect width="400" height="220" fill="#e4e9db"/><circle cx="295" cy="60" r="34" fill="#e6c994"/><path d="M0 150 89 70l88 104 67-72 156 87v31H0Z" fill="#b4c4a3"/><path d="M0 188 111 140l85 62 112-32 92 30v20H0Z" fill="#8ea582"/><path d="m124 220 59-57h20l76 57" fill="#e2ddc4"/><path d="M174 163V97c0-9 7-16 16-16h30c9 0 16 7 16 16v66Z" fill="#4a6852"/><rect x="181" y="94" width="48" height="32" rx="4" fill="#c4d1b5"/><path d="M205 94v32" stroke="#4a6852" stroke-width="3"/><circle cx="184" cy="143" r="4" fill="#e7cd91"/><circle cx="225" cy="143" r="4" fill="#e7cd91"/></svg>`;
}

async function refreshStats() {
  state.stats = await api('/stats'); updateSidebar();
}
function updateSidebar() {
  const stats = state.stats;
  $('#word-count').textContent = stats.total_words || 0;
  $('#daily-minutes').textContent = `${minutes(stats.today_seconds)} / 15 分钟`;
  $('#daily-bar').style.width = Math.min(100, (stats.today_seconds || 0) / 9) + '%';
  $$('.nav-list a').forEach(link => link.classList.toggle('active', link.dataset.nav === (state.view === 'reader' ? 'library' : state.view)));
  $('#page-label').textContent = {library:'发现文章',reader:'沉浸阅读',vocabulary:'我的生词',activity:'学习足迹'}[state.view] || '发现文章';
}
async function loadData() {
  const [articles, vocabulary, stats, settings] = await Promise.all([api('/articles'),api('/vocabulary'),api('/stats'),api('/settings')]);
  state.articles = articles.articles; state.words = vocabulary.words; state.stats = stats; state.settings = settings; updateSidebar();
}
function dateLabel() { return new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'long',timeZone:'Asia/Shanghai'}).format(new Date()); }
function weekDots() {
  const today = new Date(state.stats.today + 'T12:00:00'); const monday = new Date(today); monday.setDate(today.getDate() - (today.getDay()+6)%7);
  return ['一','二','三','四','五','六','日'].map((day,index) => {
    const d = new Date(monday); d.setDate(d.getDate()+index); const key = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    const checked = state.stats.days?.some(item=>item.date===key); return `<div class="week-day"><span>${day}</span><i class="${checked?'done ':''}${key===state.stats.today?'today':''}">${checked?icon('check'):d.getDate()}</i></div>`;
  }).join('');
}

function renderLibrary() {
  const continuing = [...state.articles].filter(a=>a.progress>0 && a.progress<100).sort((a,b)=>(b.last_read_at||'').localeCompare(a.last_read_at||''))[0];
  const featured = continuing || state.articles.find(a=>a.is_demo) || state.articles[0];
  app.innerHTML = `<div class="page-wrap">${intro('A WINDOW TO THE WORLD','把世界，读成自己的见识。','从一篇好文章开始，让每一次阅读都有所收获。',`<div class="date-label">${icon('calendar')}${dateLabel()}</div>`)}
    <div class="dashboard-top">${featured?`<section class="featured-card"><div class="feature-copy"><div class="feature-kicker">${icon('book')}${continuing?'PICK UP WHERE YOU LEFT OFF':'THE READING EDIT · 编辑精选'}</div><h2>${escapeHTML(featured.title)}</h2><p>${escapeHTML(featured.subtitle || '在字里行间，发现新的视角。')}</p><div class="feature-meta">${levelTag(featured.level)}<span>${featured.reading_minutes} 分钟</span><span>·</span><span>${escapeHTML(featured.category)}</span></div><button class="button primary" data-open="${featured.id}">${continuing?'继续阅读 · '+Math.round(featured.progress)+'%':'开始阅读'}${icon('arrow-right')}</button></div><div class="feature-art">${artwork(featured.category,true)}<span class="feature-caption">THE ART OF SLOW READING</span></div></section>`:empty('下一篇好文章，等你带来','导入一篇文章，开启你的阅读手记。','<button class="button primary" data-action="import">导入文章</button>')}
    <section class="daily-overview"><div class="section-label">一点一滴，正在发生<span>DAILY MOMENT</span></div><div class="streak-row"><div class="streak-icon">${icon('flame')}</div><div><div class="streak-number">${state.stats.streak}<span>天</span></div><small>连续学习的日子</small></div></div><div class="week-dots">${weekDots()}</div><button class="button" data-action="checkin" ${state.stats.checked_in?'disabled':''}>${icon(state.stats.checked_in?'check':'sun')}${state.stats.checked_in?'今日已打卡 · 继续保持':'今天，也来读一点'}</button></section></div>
    <div class="library-toolbar"><h2 class="section-heading">发现好文章 <small>READ SOMETHING GOOD</small></h2><div class="filters">${['全部','科技','文化','科学','生活','环境','其他'].map(cat=>`<button class="filter ${state.category===cat?'active':''}" data-category="${cat}">${cat}</button>`).join('')}</div></div>
    <div class="library-controls"><div class="search-box">${icon('search')}<input id="article-search" aria-label="搜索文章" placeholder="寻找感兴趣的标题或主题…" value="${escapeHTML(state.query)}"></div><select id="difficulty-filter" class="difficulty-select" aria-label="筛选文章难度">${['全部','A2','B1','B2','C1','C2'].map(level=>`<option value="${level}" ${level===state.difficulty?'selected':''}>${level==='全部'?'所有阅读难度':level+' 难度'}</option>`).join('')}</select></div>
    <div id="article-grid" class="article-grid"></div><div class="page-footnote">${icon('leaf')}阅读不必匆忙，理解自有回响。<span>·</span>内置内容为原创学习示例</div></div>`;
  renderArticleCards();
}
function renderArticleCards() {
  const articles = state.articles.filter(a=>(state.category==='全部'||a.category===state.category)&&(state.difficulty==='全部'||a.level===state.difficulty)&&`${a.title} ${a.subtitle} ${a.source}`.toLowerCase().includes(state.query.toLowerCase()));
  $('#article-grid').innerHTML = articles.length ? articles.map(a=>`<article class="article-card"><button class="card-art" data-open="${a.id}" aria-label="阅读 ${escapeHTML(a.title)}">${artwork(a.category)}<span class="art-label">${{科技:'TECH & IDEAS',文化:'ARTS & CULTURE',科学:'SCIENCE & NATURE',生活:'LIFE & JOURNEYS',环境:'A GREENER WORLD',其他:'NEW PERSPECTIVES'}[a.category] || 'NEW PERSPECTIVES'}</span></button><div class="card-copy"><div class="card-meta"><span>${escapeHTML(a.source)}</span><span>· ${escapeHTML(a.category)}</span>${levelTag(a.level)}</div><button class="card-title" data-open="${a.id}">${escapeHTML(a.title)}</button><p class="card-subtitle">${escapeHTML(a.subtitle || '从这篇文章，遇见一个新的视角。')}</p><div class="card-footer"><span>${icon('clock')}${a.reading_minutes} 分钟<span>·</span>${a.word_count} 词</span><button class="read-link" data-open="${a.id}">${a.progress>=100?'再读一遍':a.progress>0?'继续 '+Math.round(a.progress)+'%':'去阅读'}${icon('arrow-right')}</button></div></div><div class="card-progress"><span style="width:${a.progress}%"></span></div></article>`).join('') : empty('暂时没有找到匹配文章','换一个关键词或分类，也许会有新的发现。','<button class="button" data-action="reset-filters">查看全部文章</button>','search');
}

function tokenizeParagraph(paragraph) {
  const saved = new Set(state.words.map(w=>w.word.toLowerCase()));
  return paragraph.split(/([A-Za-z]+(?:['’-][A-Za-z]+)*)/g).map((part,index)=>index%2?`<span class="word ${saved.has(part.toLowerCase())?'saved':''}" data-word="${escapeHTML(part)}">${escapeHTML(part)}</span>`:escapeHTML(part)).join('');
}
function readerScroller() { return $('.reader-scroll'); }
function renderReader(article) {
  state.article = article;
  state.lookup = null;
  state.lookupVersion++;
  const paragraphs = article.content.split(/\n\s*\n|\n/).filter(p => p.trim());
  app.classList.add('reader-page');
  app.innerHTML = `
    <div class="reader-toolbar">
      <a class="reader-back" href="#library">${icon('arrow-left')}返回文章库</a>
      <div class="reader-controls" aria-label="阅读工具栏">
        <div class="control-group"><span class="control-label">字号</span>
          <button class="icon-button" data-action="font-down" aria-label="减小字号">${icon('minus')}</button>
          <span class="font-label" id="font-label">${state.settings.font_size}</span>
          <button class="icon-button" data-action="font-up" aria-label="增大字号">${icon('plus')}</button>
        </div>
        <button class="reader-tool-button" data-reader-action="toc" aria-haspopup="dialog" title="段落目录（T）">${icon('book')}<span>目录</span></button>
        <button class="reader-tool-button" data-reader-action="settings" aria-haspopup="dialog" title="阅读设置（S）">${icon('settings')}<span>阅读设置</span></button>
        <button class="reader-tool-button" data-writing-action="open" aria-haspopup="dialog">${icon('pen')}<span>写作训练</span></button>
        <button class="reader-tool-button" data-writing-action="selection" aria-haspopup="dialog" hidden>用选中内容练习</button>
        <button class="reader-tool-button" data-reader-action="focus" aria-pressed="false" title="专注模式（F）">${icon('leaf')}<span>专注</span></button>
        <button class="reader-tool-button" data-reader-action="fullscreen" aria-pressed="false" title="全屏阅读（G）">${icon('fullscreen')}<span>全屏</span></button>
        <button class="reader-tool-button" data-reader-action="lookup" title="打开查词面板">${icon('search')}<span>查词</span></button>
        <button class="reader-tool-button reader-help-button" data-reader-action="help" aria-label="阅读操作帮助" title="快捷键与手机手势（?）">${icon('info')}</button>
      </div>
    </div>
    <div class="reading-flow-bar" aria-label="阅读与学习节奏">
      <label class="reading-mode-label">模式<select data-reader-setting="reading_mode" aria-label="阅读模式"><option value="fluent">流畅阅读</option><option value="intensive">精读模式</option></select></label>
      <span id="flow-mode-hint" class="flow-mode-hint"></span>
      <button class="button small" data-flow-action="current-paragraph">本段生词</button>
      <button class="button small pending-open" data-flow-action="queue" aria-haspopup="dialog">待查词 <span id="pending-word-count">…</span></button>
      <span id="flow-feedback" class="flow-feedback" role="status"></span><button class="button small" data-flow-action="retry-save" hidden>重试暂存</button>
    </div>
    <div class="reader-grid"><div class="reader-scroll" tabindex="0" role="region" aria-label="文章阅读区域"><article class="reader-paper">
      <div class="reader-label"><span>${escapeHTML(article.category)}</span><span> / </span>${levelTag(article.level)}<span title="${escapeHTML(article.difficulty.method)}">易读度 ${article.difficulty_score}</span></div>
      <h1 class="reader-title">${escapeHTML(article.title)}</h1>
      ${article.subtitle?`<p class="reader-subtitle">${escapeHTML(article.subtitle)}</p>`:''}
      <div class="reader-byline"><span>${escapeHTML(article.source)}</span><span>${icon('clock')} ${article.reading_minutes} 分钟</span><span>${article.word_count} 词</span>
        ${article.source_url?`<a href="${escapeHTML(article.source_url)}" target="_blank" rel="noopener noreferrer">阅读原文 ↗</a>`:(article.is_demo?'<span>原创学习示例</span>':'<span>个人导入</span>')}
        <div class="reader-tools"><button class="icon-button danger" data-action="delete-article" title="删除文章" aria-label="删除文章">${icon('trash')}</button></div>
      </div>
      <div class="reader-hint">${icon('pen')}单击或划选一个英文单词，查看释义并收藏</div>
      <p id="reader-layout-note" class="reader-layout-note" hidden></p>
      <div class="article-body" id="article-body" lang="en">${paragraphs.map((p, i) => `<p class="paragraph" id="reader-p-${i}" data-number="${i+1}" tabindex="-1">${tokenizeParagraph(p.trim())}<button class="paragraph-finish" data-flow-paragraph="${i}" lang="zh-CN" hidden></button></p>`).join('')}</div>
      <div class="reader-end"><small>THE END · A NEW PERSPECTIVE</small><p>又多了解了一点世界，也离目标近了一点。</p>
        <button class="button primary" data-action="complete-reading" id="complete-reading">${icon('check')}${article.progress>=100?'已完成阅读':'标记为已读 · 完成打卡'}</button>
      </div>
    </article></div>
    <aside class="reader-margin"><div class="margin-heading">阅读的留白<span>THE MARGIN</span><button class="icon-button margin-close" data-action="close-margin" aria-label="关闭查词面板">${icon('close')}</button></div>
      <form class="lookup-form" id="lookup-form"><input name="word" aria-label="输入英文单词查词" placeholder="输入单词，或直接点击正文" maxlength="60" required><button type="submit" class="icon-button" aria-label="查询单词">${icon('search')}</button></form>
      <div id="learning-panel">${marginPlaceholder()}</div>
    </aside><aside id="flow-reminder" class="flow-reminder" role="status" aria-live="polite" hidden><strong>给学习留一点空隙</strong><p></p><div><button class="button small primary" data-flow-action="queue">整理待查词</button><button class="button small" data-flow-action="reminder-later">5 分钟后提醒</button><button class="button small" data-flow-action="reminder-dismiss">继续阅读</button></div></aside></div>
    <div class="reading-status"><span class="status-progress"><span class="mini-progress"><span id="reading-bar" style="width:${article.progress}%"></span></span><span id="reading-percent">${Math.round(article.progress)}% 已读</span></span><div class="reader-step-controls"><button class="icon-button" data-reader-action="previous" aria-label="上一段" title="上一段（[）">${icon('chevron-left')}</button><span id="reader-position-label"></span><button class="icon-button" data-reader-action="next" aria-label="下一段" title="下一段（]）">${icon('chevron-right')}</button></div><span class="status-saved" id="save-status">${icon('shield')}进度自动保存在本机</span></div>`;
  activeSession = readingSessions.get(article.id) || {id:article.id, seconds:0, position:article.position, progress:article.progress, saving:null, dirty:false};
  readingSessions.set(article.id, activeSession);
  lastActive = lastTick = performance.now();
  $$('.paragraph').forEach((p, i) => { p.dataset.plain = paragraphs[i].trim(); });
  reader = new MarginReader({root:app, settings:state.settings, onChange:saveSettings,
    onLayout:()=>onReaderScroll({layoutOnly:true}), toast});
  writingTrainer = new MarginWriting({root:app, article, reader, api, toast});
  flow = new MarginFlow({root:app, article, reader, settings:state.settings, api, toast,
    getWords:()=>state.words, onWords:async()=>{state.words=(await api('/vocabulary')).words;await refreshStats();}, lookup});
  requestAnimationFrame(() => {
    const scroller = readerScroller();
    if (!scroller || state.article!==article || state.view!=='reader' || !activeSession || !reader) return;
    const limit = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    scroller.scrollTop = limit * activeSession.position / 100;
    reader.remember();
    scroller.addEventListener('scroll', onReaderScroll, {passive:true});
  });
}
function marginPlaceholder() { return `<div class="margin-placeholder"><div class="margin-symbol">${icon('book')}</div><h3>好奇心，值得停留</h3><p>在上方输入单词，主动查询。<br>释义默认折叠，可以按需展开。</p></div><div class="margin-tip"><span class="eyebrow">A SMALL READING TIP</span><p>流畅模式点词先暂存，精读模式点词打开查词卡片。读完一段，再整理遇见的词。</p></div>`; }
let settingsQueue = Promise.resolve();
let settingsRevision = 0;
function saveSettings(patch) {
  const anchor = reader?.captureAnchor();
  Object.assign(state.settings,patch); flow?.apply(state.settings); reader?.apply(state.settings,anchor);
  const snapshot = {...state.settings}, revision = ++settingsRevision;
  $('#reader-settings-status').textContent='正在保存设置…';
  $('#retry-reader-settings').hidden=true;
  settingsQueue = settingsQueue.catch(()=>{}).then(()=>api('/settings',{method:'PATCH',body:snapshot})).then(()=>{
    if(revision===settingsRevision)$('#reader-settings-status').textContent='阅读设置已保存';
  }).catch(error=>{
    if(revision===settingsRevision){$('#reader-settings-status').textContent='设置尚未保存，请重试';$('#retry-reader-settings').hidden=false;}
    toast(error.message,true);
  });
}
function onReaderScroll(event = {}) {
  if (!activeSession || reader?.restoring) return;
  const scroller=readerScroller(); const contentHeight = scroller.scrollHeight;
  const limit=Math.max(0,contentHeight-scroller.clientHeight);
  activeSession.position=limit>0?Math.max(0,Math.min(100,scroller.scrollTop/limit*100)):0;
  if (!event.layoutOnly) activeSession.progress=Math.max(activeSession.progress,Math.min(99,activeSession.position));
  activeSession.dirty=true;
  updateReaderStatus();
}
function updateReaderStatus() {
  if(!activeSession||!$('#reading-bar')) return;
  $('#reading-bar').style.width=activeSession.progress+'%'; $('#reading-percent').textContent=Math.round(activeSession.progress)+'% 已读';
}
async function flushSession(session=activeSession, keepalive=false) {
  if(!session) return;
  if(session.saving) { await session.saving; if(session.dirty||session.seconds) return flushSession(session,keepalive); return; }
  if(!session.dirty&&!session.seconds) return;
  const seconds=Math.min(60,session.seconds), progress=session.progress, position=session.position;
  session.dirty=false;
  session.saving=api(`/articles/${session.id}/progress`,{method:'PATCH',body:{seconds,progress,position},keepalive}).then(()=> {
    session.seconds=Math.max(0,session.seconds-seconds);
    const article=state.articles.find(a=>a.id===session.id); if(article) {article.progress=progress;article.last_read_at=new Date().toISOString();}
    if(state.view==='reader'&&activeSession===session&&$('#save-status')) $('#save-status').innerHTML=icon('check')+' 进度已保存';
    return true;
  }).catch(error=> {session.dirty=true; if(!keepalive) throw error; return false;}).finally(()=> {session.saving=null;});
  const saved=await session.saving;
  // Reconnection can leave more than one 60-second batch, or newer scroll data.
  if(saved&&(session.dirty||session.seconds)) return flushSession(session,keepalive);
}
function currentContext(word, paragraph) {
  if(!paragraph) return '';
  const sentences=paragraph.match(/[^.!?]+[.!?]+|[^.!?]+$/g)||[paragraph];
  return (sentences.find(s=>s.toLowerCase().includes(word.toLowerCase()))||paragraph).trim().slice(0,5000);
}
function revealMargin() {
  if(reader?.overlayMargin()) $('.reader-margin')?.classList.add('mobile-open');
}
async function lookup(word, context='', options={}) {
  const version=++state.lookupVersion; state.lookup=null;
  if(!$('#learning-panel')) return;
  const articleId=state.article.id, activeReader=reader;
  if(options.fullParagraph)context=currentContext(word,context);
  const anchor=reader.captureAnchor();
  revealMargin(); $('#learning-panel').innerHTML=`<div class="lookup-loading"><span class="spinner"></span>正在查找 ${escapeHTML(word)}…</div>`;
  reader.restoreAnchor(anchor);
  $$('.word.selected').forEach(n=>n.classList.remove('selected')); $$('.word').filter(n=>n.dataset.word.toLowerCase()===word.toLowerCase()).forEach(n=>n.classList.add('selected'));
  try {
    const result=await api('/dictionary?word='+encodeURIComponent(word));
    if(version!==state.lookupVersion||state.view!=='reader') return;
    state.lookup={...result,context,articleId,paragraphIndex:options.paragraphIndex??-1,expanded:!state.settings.definition_collapsed}; renderLookup();
  } catch(error) {if(version===state.lookupVersion&&reader===activeReader&&$('#learning-panel')) $('#learning-panel').innerHTML=`<p class="error-note">${escapeHTML(error.message)}</p><button class="button small" data-action="return-reading">继续阅读</button>`;}
}
function renderLookup() {
  const entry = state.lookup;
  if (!entry) return;
  const saved = state.words.some(w => w.word.toLowerCase() === entry.word.toLowerCase());
  $('#learning-panel').innerHTML = `<div class="lookup-result">
    <div class="lookup-title"><h2>${escapeHTML(entry.word)}</h2></div>
    <details class="lookup-definition" ${entry.expanded?'open':''}><summary>${entry.expanded?'收起释义':'展开释义'}</summary>
    <div class="phonetic">${escapeHTML(entry.phonetic)}${entry.lemma!==entry.word?' · 原形 '+escapeHTML(entry.lemma):''}</div>
    ${entry.found?`<div class="definition"><span class="part-of-speech">${escapeHTML(entry.part_of_speech)}</span>${escapeHTML(entry.meaning)}</div>`:`<p class="lookup-note">${escapeHTML(entry.message)}</p><label>手动填写释义<textarea id="manual-meaning" rows="3" maxlength="3000" placeholder="写下释义后即可收藏">${escapeHTML(entry.manualMeaning||'')}</textarea></label>`}
    <div class="source-caption">${escapeHTML(entry.source)}${entry.cached?' · 本地缓存':''}</div>
    ${entry.context?`<blockquote class="context-block"><span class="context-label">在这篇文章里</span>${escapeHTML(entry.context)}</blockquote>`:entry.example?`<blockquote class="context-block"><span class="context-label">词典例句</span>${escapeHTML(entry.example)}</blockquote>`:''}
    <button class="button primary" data-action="save-lookup" ${saved?'disabled':''}>${icon(saved?'check':'plus')}${saved?'已加入生词本':'加入生词本'}</button>
    </details><div class="lookup-flow-actions"><button class="button small" data-action="defer-lookup">暂存，稍后处理</button><button class="button small" data-action="return-reading">继续阅读</button></div>
  </div>`;
}
async function saveLookup() {
  const entry=state.lookup;if(!entry)return;
  const meaning=entry.meaning||$('#manual-meaning')?.value.trim();if(!meaning){toast('先写下这个单词的释义。',true);return;}
  const button=$('[data-action="save-lookup"]');button.disabled=true;
  try {const result=await api('/vocabulary',{method:'POST',body:{word:entry.word,phonetic:entry.phonetic,part_of_speech:entry.part_of_speech,meaning,context:entry.context,article_id:entry.articleId}});
    const vocabulary=await api('/vocabulary');state.words=vocabulary.words;await refreshStats();
    if(state.lookup===entry&&$('#learning-panel')) renderLookup(); flow?.refreshMarks();
    toast(result.duplicate?'这个单词已经在生词本里了。':'已加入生词本，并保存了原文语境。');
  }catch(error){toast(error.message,true);button.disabled=false;}
}
function summaryTiles() {
  const stats=state.stats;return `<div class="summary-strip"><div class="summary-tile"><span>读过的文章${icon('book')}</span><strong>${stats.completed_articles}<small>篇</small></strong></div><div class="summary-tile"><span>收集的生词${icon('bookmark')}</span><strong>${stats.total_words}<small>个</small></strong></div><div class="summary-tile"><span>累计阅读${icon('clock')}</span><strong>${minutes(stats.total_seconds)}<small>分钟</small></strong></div><div class="summary-tile"><span>连续学习${icon('flame')}</span><strong>${stats.streak}<small>天</small></strong></div></div>`;
}
function renderVocabulary() {
  app.innerHTML=`<div class="page-wrap">${intro('WORDS BECOME YOUR WORLD','让遇见的词，成为自己的词。','每一个收藏，都保留着你与它初次相遇的语境。',`<button class="button primary" data-action="add-word">${icon('plus')}添加生词</button>`)}<div class="summary-strip"><div class="summary-tile"><span>我的词汇${icon('bookmark')}</span><strong>${state.words.length}<small>个</small></strong></div><div class="summary-tile"><span>正在学习${icon('plant')}</span><strong>${state.words.filter(w=>w.mastery==='learning').length}<small>个</small></strong></div><div class="summary-tile"><span>已经掌握${icon('check')}</span><strong>${state.stats.mastered_words}<small>个</small></strong></div><div class="summary-tile"><span>初次遇见${icon('plant')}</span><strong>${state.words.filter(w=>w.mastery==='new').length}<small>个</small></strong></div></div><div class="table-toolbar"><div class="filters">${[['all','全部生词'],['new','初次遇见'],['learning','正在学习'],['mastered','已掌握']].map(([key,label])=>`<button class="filter ${state.mastery===key?'active':''}" data-mastery-filter="${key}">${label}</button>`).join('')}</div><div class="search-box">${icon('search')}<input id="word-search" aria-label="搜索生词" placeholder="搜索单词或释义…" value="${escapeHTML(state.wordQuery)}"></div></div><div id="vocabulary-table"></div><div class="page-footnote">${icon('info')}按实际学习情况标记掌握程度，随时可以修改。</div></div>`;renderWordRows();
}
function renderWordRows() {
  const list=state.words.filter(w=>(state.mastery==='all'||w.mastery===state.mastery)&&`${w.word} ${w.meaning}`.toLowerCase().includes(state.wordQuery.toLowerCase()));
  $('#vocabulary-table').innerHTML=list.length?`<div class="vocab-list"><div class="vocab-head"><span>单词 / 发音</span><span>释义与阅读语境</span><span>掌握程度</span><span>管理</span></div>${list.map(w=>`<div class="vocab-row"><div class="vocab-word"><strong>${escapeHTML(w.word)}</strong><small>${escapeHTML(w.phonetic)}</small></div><div class="vocab-meaning">${escapeHTML(w.meaning)}${w.context?`<div class="vocab-context" title="${escapeHTML(w.context)}">${escapeHTML(w.context)}</div>`:''}${w.note?`<div class="vocab-context">笔记：${escapeHTML(w.note)}</div>`:''}</div><select class="mastery-select ${w.mastery}" data-word-mastery="${w.id}" aria-label="${escapeHTML(w.word)} 的掌握度">${[['new','初次遇见'],['learning','正在学习'],['mastered','已掌握']].map(([key,label])=>`<option value="${key}" ${w.mastery===key?'selected':''}>${label}</option>`).join('')}</select><div class="word-actions"><button class="icon-button" data-edit-word="${w.id}" aria-label="编辑 ${escapeHTML(w.word)}" title="编辑笔记">${icon('edit')}</button><button class="icon-button danger" data-delete-word="${w.id}" aria-label="删除 ${escapeHTML(w.word)}" title="删除生词">${icon('trash')}</button></div></div>`).join('')}</div>`:empty(state.words.length?'没有匹配的生词':'你的词汇花园，等待第一颗种子',state.words.length?'试试其他关键词或掌握程度。':'阅读时点击单词并收藏，或手动添加一个想记住的词。','<a class="button primary" href="#library">去读一篇文章 '+icon('arrow-right')+'</a>','plant');
}

async function renderActivity(month='',version=state.routeVersion) {
  const stats=await api('/stats'+(month?'?month='+month:''));if(version!==state.routeVersion||state.view!=='activity')return;state.month=stats.month;
  const [year,monthNum]=stats.month.split('-').map(Number);const first=new Date(year,monthNum-1,1);const dayCount=new Date(year,monthNum,0).getDate();
  const blank=(first.getDay()+6)%7;const activity=new Map(stats.days.map(day=>[day.date,day]));
  app.innerHTML=`<div class="page-wrap">${intro('SMALL STEPS, REAL GROWTH','每一次翻开，都算数。','你的学习足迹，正在把零散的时间变成看得见的积累。')}${summaryTiles()}<div class="calendar-layout"><section class="calendar-card"><div class="calendar-heading"><h2>${year} 年 ${monthNum} 月<span class="eyebrow">YOUR READING DAYS</span></h2><div class="calendar-actions"><button class="button small" data-action="checkin" ${stats.checked_in?'disabled':''}>${icon('check')}${stats.checked_in?'今日已打卡':'今日打卡'}</button><div class="month-nav"><button class="icon-button" data-month-offset="-1" aria-label="上个月">${icon('chevron-left')}</button><button class="icon-button" data-month-offset="1" aria-label="下个月">${icon('chevron-right')}</button></div></div></div><div class="calendar-grid">${['一','二','三','四','五','六','日'].map(d=>`<div class="calendar-weekday">${d}</div>`).join('')}${Array(blank).fill('<div class="calendar-day blank"></div>').join('')}${Array.from({length:dayCount},(_,index)=> {const key=stats.month+'-'+String(index+1).padStart(2,'0');const day=activity.get(key);return `<button class="calendar-day ${day?'active ':''}${key===stats.today?'today ':''}${key>stats.today?'future':''}" data-calendar-day="${key}" aria-label="${key}${day?' 已学习':' 暂无学习记录'}" title="${day?'阅读 '+minutes(day.reading_seconds)+' 分钟 · 完成 '+day.articles_completed+' 篇':'暂无学习记录'}">${index+1}${day?'<span class="dot"></span>':''}</button>`;}).join('')}</div><div class="calendar-legend"><span><i class="legend-square"></i>有学习记录</span><span>描边为今天</span><span>· 阅读或手动打卡均会点亮</span></div><div id="calendar-detail" class="calendar-detail">这个月，你已经在 ${stats.days.length} 天里留下一点进步。</div></section><aside class="insight-card"><h3>时间会看见你的坚持</h3><div class="big-number">${stats.active_days}<small>天</small></div><p>这是你与英语相遇的日子。<br>不必每次都很多，只要常常开始。</p><div class="insight-divider"></div><div class="insight-line"><span>今日阅读</span><strong>${minutes(stats.today_seconds)} 分钟</strong></div><div class="insight-line"><span>已掌握词汇</span><strong>${stats.mastered_words} 个</strong></div><div class="quote">“Little by little,<br>a little becomes a lot.”<small>KEEP YOUR CURIOSITY ALIVE</small></div></aside></div><div class="page-footnote">${icon('shield')}数据保存在本机 · 北京时间 · 阅读计时只统计前台活跃时间</div></div>`;
  state.calendarStats=stats;
}

function openSettings() { $('#settings-dialog').showModal(); }
function showWordDialog(id=null) {
  state.editWord=id;const form=$('#word-form');form.reset();$('#word-error').textContent='';
  const word=state.words.find(w=>w.id===id);$('#word-dialog-title').textContent=word?'记住 '+word.word:'添加一个想记住的词';
  form.elements.word.disabled=!!word;if(word){form.elements.word.value=word.word;form.elements.meaning.value=word.meaning;form.elements.note.value=word.note;}
  $('#word-dialog').showModal();
}
let confirmResolve=null;
function confirmDelete(message) {
  if(message.startsWith('删除文章「'))message += ' 本篇写作练习、草稿和反馈也会删除。';
  $('#confirm-message').textContent=message;
  $('#confirm-dialog').showModal();
  return new Promise(resolve=> {confirmResolve=resolve;});
}
function resolveConfirm(answer) {$('#confirm-dialog').close();if(confirmResolve){confirmResolve(answer);confirmResolve=null;}}
async function navigate() {
  writingTrainer?.destroy();writingTrainer=null;
  const version=++state.routeVersion;state.lookupVersion++;
  if(activeSession){try{await flushSession(activeSession);}catch(error){toast(error.message,true);}activeSession=null;}
  if(version!==state.routeVersion)return;
  flow?.destroy();flow=null;reader?.destroy();reader=null;
  const [route,id]=(location.hash.slice(1)||'library').split('/');state.view=['library','reader','vocabulary','activity'].includes(route)?route:'library';
  app.classList.remove('reader-page');app.scrollTop=0;updateSidebar();app.innerHTML=loading('正在打开你的阅读手记…');
  try {
    if(state.view==='reader') {if(!/^\d+$/.test(id||''))throw new Error('文章地址无效。');const article=await api('/articles/'+id);if(version===state.routeVersion)renderReader(article);}
    else {await refreshStats();if(version!==state.routeVersion)return;
      if(state.view==='library'){state.articles=(await api('/articles')).articles;if(version===state.routeVersion)renderLibrary();}
      else if(state.view==='vocabulary'){state.words=(await api('/vocabulary')).words;if(version===state.routeVersion)renderVocabulary();}
      else await renderActivity('',version);
    }
  }catch(error){if(version===state.routeVersion)app.innerHTML=empty('这页暂时没有翻开',escapeHTML(error.message),'<a class="button primary" href="#library">返回文章库</a>','info');}
}

document.addEventListener('click',async event=> {
  lastActive=performance.now();const button=event.target.closest('button, a');if(!button)return;
  try {
    if(button.dataset.open){location.hash='reader/'+button.dataset.open;return;}
    if(button.dataset.category){state.category=button.dataset.category;$$('[data-category]').forEach(b=>b.classList.toggle('active',b===button));renderArticleCards();return;}
    if(button.dataset.masteryFilter){state.mastery=button.dataset.masteryFilter;$$('[data-mastery-filter]').forEach(b=>b.classList.toggle('active',b===button));renderWordRows();return;}
    if(button.dataset.editWord){showWordDialog(Number(button.dataset.editWord));return;}
    if(button.dataset.deleteWord){const id=Number(button.dataset.deleteWord),word=state.words.find(w=>w.id===id);if(await confirmDelete(`删除生词「${word.word}」？`)){await api('/vocabulary/'+id,{method:'DELETE',body:{}});state.words=state.words.filter(w=>w.id!==id);await refreshStats();if(state.view==='vocabulary')renderVocabulary();toast('生词已删除。');}return;}
    if(button.dataset.monthOffset){const [year,month]=state.month.split('-').map(Number);const next=new Date(year,month-1+Number(button.dataset.monthOffset),1);await renderActivity(`${next.getFullYear()}-${String(next.getMonth()+1).padStart(2,'0')}`);return;}
    if(button.dataset.calendarDay){const date=button.dataset.calendarDay,day=state.calendarStats.days.find(d=>d.date===date);$('#calendar-detail').textContent=day?`${date} · 阅读 ${minutes(day.reading_seconds)} 分钟 ${day.reading_seconds%60} 秒 · 完成 ${day.articles_completed} 篇${day.manual?' · 手动打卡':''}`:`${date} · 这一天暂无学习记录。`;return;}
    if(button.dataset.importTab){state.importMode=button.dataset.importTab;$$('[data-import-tab]').forEach(b=>b.classList.toggle('active',b===button));$('#paste-fields').hidden=state.importMode!=='paste';$('#url-fields').hidden=state.importMode!=='url';$$('input,textarea',$('#paste-fields')).forEach(input=>input.disabled=state.importMode!=='paste');$('#url-fields input').disabled=state.importMode!=='url';$('#url-fields input').required=state.importMode==='url';$('#import-error').textContent='';return;}
    const action=button.dataset.action;
    if(action==='import'){$('#import-error').textContent='';$('#import-dialog').showModal();}
    else if(action==='close-dialog')button.closest('dialog').close();
    else if(action==='close-margin')$('.reader-margin')?.classList.remove('mobile-open');
    else if(action==='settings')openSettings();
    else if(action==='reset-filters'){state.category=state.difficulty='全部';state.query='';renderLibrary();}
    else if(action==='checkin'){button.disabled=true;await api('/checkin',{method:'POST',body:{}});await refreshStats();if(state.view==='library')renderLibrary();else if(state.view==='activity')await renderActivity(state.month);toast('今天的学习，已留下足迹。');}
    else if(action==='font-down'||action==='font-up'){saveSettings({font_size:Math.max(16,Math.min(28,state.settings.font_size+(action==='font-up'?1:-1)))});}
    else if(action==='save-lookup')await saveLookup();
    else if(action==='defer-lookup'&&state.lookup){const entry=state.lookup;await flow.deferWord(entry.word,entry.paragraphIndex);}
    else if(action==='return-reading'){const anchor=reader.captureAnchor();$('.reader-margin')?.classList.remove('mobile-open');reader.scroller.focus({preventScroll:true});reader.restoreAnchor(anchor);}
    else if(action==='complete-reading'){button.disabled=true;activeSession.progress=100;activeSession.dirty=true;await flushSession();updateReaderStatus();button.innerHTML=icon('check')+'已完成阅读';await refreshStats();toast('读完一篇，点亮一天。进度已保存。');}
    else if(action==='delete-article'){
      const article=state.article, session=activeSession;
      if(await confirmDelete(`删除文章「${article.title}」？已收藏的单词与历史学习记录会保留。`)){
        await flushSession(session);
        await api('/articles/'+article.id,{method:'DELETE',body:{}});
        if(activeSession===session)activeSession=null;
        readingSessions.delete(article.id);
        state.articles=state.articles.filter(item=>item.id!==article.id);
        if(state.view==='reader'&&state.article?.id===article.id)location.hash='library';
        else if(state.view==='library')renderLibrary();
        toast('文章已删除，生词仍然保留。');
      }
    }
    else if(action==='add-word')showWordDialog();
    else if(action==='cancel-confirm')resolveConfirm(false);
    else if(action==='accept-confirm')resolveConfirm(true);
  }catch(error){toast(error.message,true);button.disabled=false;}
});

document.addEventListener('input',event=> {if(event.target.id==='article-search'){state.query=event.target.value;renderArticleCards();}else if(event.target.id==='word-search'){state.wordQuery=event.target.value;renderWordRows();}else if(event.target.id==='manual-meaning'&&state.lookup){state.lookup.manualMeaning=event.target.value;}});
document.addEventListener('toggle',event=>{if(event.target.matches('.lookup-definition')&&state.lookup){state.lookup.expanded=event.target.open;event.target.querySelector('summary').textContent=event.target.open?'收起释义':'展开释义';}},{capture:true});
document.addEventListener('change',async event=> {
  const input=event.target;
  try {if(input.id==='difficulty-filter'){state.difficulty=input.value;renderArticleCards();}
    else if(input.dataset.wordMastery){const id=Number(input.dataset.wordMastery);input.disabled=true;const result=await api('/vocabulary/'+id,{method:'PATCH',body:{mastery:input.value}});const word=state.words.find(w=>w.id===id);Object.assign(word,result);await refreshStats();if(state.view==='vocabulary')renderVocabulary();toast('掌握度已更新。');}
  }catch(error){input.disabled=false;toast(error.message,true);if(input.dataset.wordMastery)input.value=state.words.find(w=>w.id===Number(input.dataset.wordMastery)).mastery;}
});
document.addEventListener('submit',async event=> {
  event.preventDefault();const form=event.target;
  if(form.id==='lookup-form'){lookup(form.elements.word.value.trim());return;}
  if(form.closest('#writing-dialog'))return;
  const button=$('button[type="submit"]',form);if(!button)return;button.disabled=true;
  try {if(form.id==='import-form') {
    $('#import-error').textContent='';button.textContent=state.importMode==='url'?'正在提取正文…':'正在导入…';
    const payload=Object.fromEntries(new FormData(form));const result=await api('/articles',{method:'POST',body:payload});$('#import-dialog').close();form.reset();toast(result.duplicate?'文章已在库中，为你打开。':'文章已导入，开始新的阅读。');location.hash='reader/'+result.id;if(location.hash==='#reader/'+result.id&&state.article?.id===result.id)await navigate();
  }else if(form.id==='word-form') {
    $('#word-error').textContent='';const payload=Object.fromEntries(new FormData(form));const result=await api('/vocabulary'+(state.editWord?'/'+state.editWord:''),{method:state.editWord?'PATCH':'POST',body:payload});$('#word-dialog').close();state.words=(await api('/vocabulary')).words;await refreshStats();if(state.view==='vocabulary')renderVocabulary();toast(result.duplicate?'这个单词已经收藏过了。':'生词已保存。');
  }}catch(error){const target=form.id==='import-form'?$('#import-error'):$('#word-error');if(target)target.textContent=error.message;else toast(error.message,true);}
  finally{button.disabled=false;if(form.id==='import-form')button.innerHTML='导入并开始阅读'+icon('arrow-right');}
});

document.addEventListener('mouseup',event=> {
  if(reader?.ignoreWordTap())return;
  const body=event.target.closest('#article-body');if(!body||event.target.closest('button'))return;
  const selection=window.getSelection(),selected=selection?.toString().trim();
  if(selected&&selection.anchorNode&&body.contains(selection.anchorNode)&&selection.focusNode&&body.contains(selection.focusNode)) {
    if(/^[A-Za-z]+(?:['’-][A-Za-z]+)*$/.test(selected))flow?.selectWord(selected,event.target.closest('.paragraph'));
    return;
  }
  const target=event.target.closest('[data-word]');if(target)flow?.selectWord(target.dataset.word,target.closest('.paragraph'));
});
document.addEventListener('keydown', () => { lastActive = performance.now(); });
$('#confirm-dialog').addEventListener('cancel',()=> {if(confirmResolve){confirmResolve(false);confirmResolve=null;}});
window.addEventListener('hashchange',navigate);
document.addEventListener('pointerdown',()=>lastActive=performance.now(),{passive:true});
document.addEventListener('wheel',()=>lastActive=performance.now(),{passive:true});
document.addEventListener('touchmove',()=>lastActive=performance.now(),{passive:true});
document.addEventListener('visibilitychange',()=> {lastTick=performance.now();if(document.hidden)flushSession(activeSession,true);else lastActive=performance.now();});
window.addEventListener('pagehide',()=> {flushSession(activeSession,true);});
setInterval(()=> {
  const current=performance.now(),elapsed=Math.floor((current-lastTick)/1000);if(elapsed<1)return;lastTick=current;
  if(state.view==='reader'&&activeSession&&!document.hidden&&!$('dialog[open]')&&current-lastActive<60000){const seconds=Math.min(2,elapsed);activeSession.seconds+=seconds;flow?.tick(seconds);}
  tickCount++;if(tickCount%15===0){for(const session of readingSessions.values())flushSession(session).catch(()=> {if($('#save-status'))$('#save-status').textContent='暂未保存，正在等待连接…';});}
},1000);

hydrateIcons();
loadData().then(navigate).catch(error=> {app.innerHTML=empty('暂时无法打开阅读空间',escapeHTML(error.message),'<button class="button primary" data-action="retry-app">重试连接</button>','info');});
document.addEventListener('click',event=> {if(event.target.closest('[data-action="retry-app"]'))location.reload();});
