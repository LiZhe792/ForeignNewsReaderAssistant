/* Margin's frontend is intentionally buildless. The application logic lives in Python. */
'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
function ui(key, english) {
  if (typeof MarginInterface === 'undefined') return key;
  if (english !== undefined) return MarginInterface.language() === 'en' ? english : key;
  return MarginInterface.t(key);
}
const localizeUI = root => { if (typeof MarginInterface !== 'undefined') MarginInterface.localize(root); };
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
  chart:'<path d="M4 3v17h17M8 15V9m5 6V5m5 10v-4"/>',
  plant:'<path d="M12 21V10M12 15C5 15 3 11 3 5c7 0 9 4 9 10Zm0-3c0-6 3-9 9-9 0 6-3 9-9 9Z"/>',
};
const icon = (name, extra = '') => `<svg class="icon ${extra}" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.book}</svg>`;
function hydrateIcons(root = document) { $$('[data-icon]', root).forEach(node => { node.innerHTML = icon(node.dataset.icon); }); }
function toast(message, error = false) {
  const element = document.createElement('div'); element.className = 'toast' + (error ? ' error' : ''); element.textContent = message;
  $('#toast-region').append(element); setTimeout(() => { element.classList.add('fading'); setTimeout(() => element.remove(), 250); }, 3800);
}
let dataGeneration=null, dataStale=false;
function dataChanged(){ dataStale=true; const banner=$('#data-changed-banner');if(banner)banner.hidden=false; }
async function api(path, options = {}) {
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 55000);
  const abort = () => controller.abort();
  if (options.signal?.aborted) controller.abort();
  else options.signal?.addEventListener('abort', abort, {once:true});
  try {
    if(dataStale&&!options.allowDataChange){const error=new Error('学习数据已还原，请重新载入后继续。');error.status=409;throw error;}
    const multipart=typeof FormData!=='undefined'&&options.body instanceof FormData;
    const response = await fetch('/api' + path, { ...options, signal: controller.signal, headers: { ...(multipart?{}:{'Content-Type':'application/json'}), ...(dataGeneration?{'X-Margin-Generation':dataGeneration}:{}), ...options.headers }, body: options.body === undefined ? undefined : multipart?options.body:JSON.stringify(options.body) });
    const generation=response.headers?.get?.('X-Margin-Generation');
    if(generation&&dataGeneration&&generation!==dataGeneration&&!options.allowDataChange){dataChanged();const error=new Error('学习数据已在其他页面还原，请重新载入。');error.status=409;throw error;}
    let payload; try { payload = await response.json(); } catch { throw new Error('服务返回格式异常，请确认 Python 服务正在运行。'); }
    if (!response.ok) { const error=new Error(payload.error || '操作失败，请稍后重试。');error.status=response.status;error.data=payload;throw error; }
    return payload;
  } catch (error) { if (error.name === 'AbortError') throw new Error('请求超时，请稍后重试。'); if (error instanceof TypeError) throw new Error('暂时无法连接本地服务，请确认 Python 程序正在运行。'); throw error; }
  finally { clearTimeout(timeout); options.signal?.removeEventListener('abort',abort); }
}

const state = {articles:[], words:[], stats:{}, settings:{font_size:20,line_height:1.9}, view:'library', article:null,
  category:'全部', difficulty:'全部', query:'', searchMode:'text', source:'', tag:'', minWords:'', maxWords:'', minMinutes:'', maxMinutes:'', readingStatus:'', searchVersion:0, searchOptions:{sources:[],tags:[]}, mastery:'all', wordQuery:'', lookup:null, month:'', routeVersion:0, lookupVersion:0, importMode:'paste', editWord:null};
const app = $('#app');
let interfaceLanguage = typeof MarginInterface === 'undefined' ? 'zh-CN' : MarginInterface.language();
let reader = null;
let flow = null;
let study = null;
let wordbookUI = null;
let reviewUI = null;
let practiceUI = null;
let personalUI = null;
let analyticsUI = null;
let learningEvents = null;
let dataUI = null;
let planUI = null;
let studyReminders = null;
let lastActive = performance.now(), lastTick = performance.now(), activeSession = null, tickCount = 0;
const readingSessions = new Map();
let writingTrainer = null;
const minutes = seconds => Math.floor((seconds || 0) / 60);
const levelTag = level => `<span class="tag level-${escapeHTML(level.toLowerCase())}" title="根据阅读易读度估算，并非正式 CEFR 测评">${escapeHTML(level)} · ${ui(['A2','B1'].includes(level)?'轻松阅读':level==='B2'?'进阶阅读':'挑战阅读')}</span>`;
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
  const goals = stats.daily_goals || {reading_minutes:15,new_words:5,words_today:0};
  const reading = minutes(stats.today_seconds);
  $('#daily-minutes').textContent = (goals.reading_minutes ? reading + ' / ' + goals.reading_minutes : reading) + ui(' 分钟', ' min');
  const goalLink = $('#daily-vocabulary');
  if(goalLink)goalLink.textContent=ui('词汇 ', 'Words ')+goals.words_today+(goals.new_words?' / '+goals.new_words:'')+ui(' 条', '');
  $('#daily-bar').style.width = (goals.reading_minutes ? Math.min(100, (stats.today_seconds || 0) / (goals.reading_minutes * .6)) : 0) + '%';
  $$('.nav-list a').forEach(link => {
    const active = link.dataset.nav === (state.view === 'reader' ? 'library' : state.view);
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  $('#page-label').textContent = ui({library:'发现文章',reader:'沉浸阅读',vocabulary:'我的生词',review:'间隔复习',listening:'听说训练',exercises:'阅读测验',personalization:'难度与推荐',analytics:'学习数据分析',data:'离线与数据管理',plans:'学习计划',activity:'学习足迹'}[state.view] || '发现文章');
}
async function loadData() {
  const [articles, vocabulary, stats, settings] = await Promise.all([api('/articles'),api('/vocabulary'),api('/stats'),api('/settings')]);
  state.articles = articles.articles; state.words = vocabulary.words; state.stats = stats; state.settings = settings; updateSidebar();
}
function dateLabel() { return new Intl.DateTimeFormat(typeof MarginInterface === 'undefined' ? 'zh-CN' : MarginInterface.language(),{year:'numeric',month:'long',day:'numeric',weekday:'long',timeZone:'Asia/Shanghai'}).format(new Date()); }
function weekDots() {
  const today = new Date(state.stats.today + 'T12:00:00'); const monday = new Date(today); monday.setDate(today.getDate() - (today.getDay()+6)%7);
  return ui(['一','二','三','四','五','六','日'], ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']).map((day,index) => {
    const d = new Date(monday); d.setDate(d.getDate()+index); const key = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    const checked = state.stats.days?.some(item=>item.date===key) || state.stats.makeups?.some(item=>item.day===key); return `<div class="week-day"><span>${day}</span><i class="${checked?'done ':''}${key===state.stats.today?'today':''}">${checked?icon('check'):d.getDate()}</i></div>`;
  }).join('');
}

function renderLibrary() {
  const continuing = [...state.articles].filter(a=>a.progress>0 && a.progress<100).sort((a,b)=>(b.last_read_at||'').localeCompare(a.last_read_at||''))[0];
  const featured = continuing || state.articles.find(a=>a.is_demo) || state.articles[0];
  app.innerHTML = `<div class="page-wrap">${intro('A WINDOW TO THE WORLD',ui('把世界，读成自己的见识。'),ui('从一篇好文章开始，让每一次阅读都有所收获。'),`<div class="date-label">${icon('calendar')}${dateLabel()}</div>`)}
    <div class="dashboard-top">${featured?`<section class="featured-card"><div class="feature-copy"><div class="feature-kicker">${icon('book')}${continuing?'PICK UP WHERE YOU LEFT OFF':'THE READING EDIT · 编辑精选'}</div><h2 lang="en" dir="auto">${escapeHTML(featured.title)}</h2><p>${escapeHTML(featured.subtitle || ui('在字里行间，发现新的视角。'))}</p><div class="feature-meta">${levelTag(featured.level)}<span>${featured.reading_minutes}${ui(' 分钟', ' min')}</span><span>·</span><span>${escapeHTML(ui(featured.category))}</span></div><button class="button primary" data-open="${featured.id}">${continuing?ui('继续阅读')+' · '+Math.round(featured.progress)+'%':ui('开始阅读')}${icon('arrow-right')}</button></div><div class="feature-art">${artwork(featured.category,true)}<span class="feature-caption">THE ART OF SLOW READING</span></div></section>`:empty(ui('下一篇好文章，等你带来'),ui('导入一篇文章，开启你的阅读手记。'),'<button class="button primary" data-action="import" data-i18n="导入文章">导入文章</button>')}
    <section class="daily-overview"><div class="section-label"><span data-i18n="一点一滴，正在发生">一点一滴，正在发生</span><span>DAILY MOMENT</span></div><div class="streak-row"><div class="streak-icon">${icon('flame')}</div><div><div class="streak-number">${state.stats.streak}<span data-i18n="天">天</span></div><small data-i18n="连续学习的日子">连续学习的日子</small></div></div><div class="week-dots">${weekDots()}</div><button class="button" data-action="checkin" ${state.stats.checked_in?'disabled':''}>${icon(state.stats.checked_in?'check':'sun')}${state.stats.checked_in?ui('今日已打卡 · 继续保持'):ui('今天，也来读一点')}</button></section></div>
    <section id="personal-library" class="personal-compact" aria-label="为你选文" data-i18n-aria-label="为你选文"><a href="#personalization" data-i18n="查看个性化选文 →">查看个性化选文 →</a></section>
    <div class="library-toolbar"><h2 class="section-heading"><span data-i18n="发现好文章">发现好文章</span> <small>READ SOMETHING GOOD</small></h2><div class="filters">${['全部','科技','文化','科学','生活','环境','其他'].map(cat=>`<button class="filter ${state.category===cat?'active':''}" data-category="${cat}" aria-pressed="${state.category===cat}">${ui(cat)}</button>`).join('')}</div></div>
    <div class="library-controls"><div class="search-box">${icon('search')}<input id="article-search" aria-label="搜索文章" data-i18n-aria-label="搜索文章" placeholder="寻找感兴趣的标题或主题…" data-i18n-placeholder="寻找感兴趣的标题或主题…" value="${escapeHTML(state.query)}"></div><select id="difficulty-filter" class="difficulty-select" aria-label="筛选文章难度" data-i18n-aria-label="筛选文章难度">${['全部','A2','B1','B2','C1','C2'].map(level=>`<option value="${level}" ${level===state.difficulty?'selected':''}>${level==='全部'?ui('所有阅读难度'):level+ui(' 难度', ' level')}</option>`).join('')}</select></div>
    <div id="article-grid" class="article-grid"></div><div class="page-footnote">${icon('leaf')}阅读不必匆忙，理解自有回响。<span>·</span><span data-i18n="内置内容为原创学习示例">内置内容为原创学习示例</span></div></div>`;
  renderArticleCards();
  localizeUI(app);
  if (typeof MarginPersonalization !== 'undefined') { personalUI?.destroy();personalUI=new MarginPersonalization({root:$('#personal-library'),api,toast,compact:true});personalUI.render(); }
}
function renderArticleCards() {
  const articles = state.articles.filter(a=>(state.category==='全部'||a.category===state.category)&&(state.difficulty==='全部'||a.level===state.difficulty)&&`${a.title} ${a.subtitle} ${a.source}`.toLowerCase().includes(state.query.toLowerCase()));
  $('#article-grid').innerHTML = articles.length ? articles.map(a=>`<article class="article-card"><button class="card-art" data-open="${a.id}" aria-label="${ui('阅读 ', 'Read ')}${escapeHTML(a.title)}">${artwork(a.category)}<span class="art-label">${{科技:'TECH & IDEAS',文化:'ARTS & CULTURE',科学:'SCIENCE & NATURE',生活:'LIFE & JOURNEYS',环境:'A GREENER WORLD',其他:'NEW PERSPECTIVES'}[a.category] || 'NEW PERSPECTIVES'}</span></button><div class="card-copy"><div class="card-meta"><span>${escapeHTML(a.source)}</span><span>· ${escapeHTML(ui(a.category))}</span>${levelTag(a.level)}</div><button class="card-title" lang="en" dir="auto" data-open="${a.id}">${escapeHTML(a.title)}</button><p class="card-subtitle">${escapeHTML(a.subtitle || ui('从这篇文章，遇见一个新的视角。'))}</p><div class="card-footer"><span>${icon('clock')}${a.reading_minutes}${ui(' 分钟', ' min')}<span>·</span>${a.word_count}${ui(' 词', ' words')}</span><button class="read-link" data-open="${a.id}">${a.progress>=100?ui('再读一遍'):a.progress>0?ui('继续 ', 'Continue ')+Math.round(a.progress)+'%':ui('去阅读')}${icon('arrow-right')}</button></div></div><div class="card-progress"><span style="width:${a.progress}%"></span></div></article>`).join('') : empty(ui('暂时没有找到匹配文章'),ui('换一个关键词或分类，也许会有新的发现。'),'<button class="button" data-action="reset-filters" data-i18n="查看全部文章">查看全部文章</button>','search');
  localizeUI($('#article-grid'));
}

function tokenizeParagraph(paragraph) {
  const saved = new Set(state.words.map(w=>w.word.toLowerCase()));
  return paragraph.split(/([A-Za-z]+(?:['’-][A-Za-z]+)*)/g).map((part,index)=>index%2?`<span class="word ${saved.has(part.toLowerCase())?'saved':''}" data-word="${escapeHTML(part)}">${escapeHTML(part)}</span>`:escapeHTML(part)).join('');
}
function readerScroller() { return $('.reader-scroll'); }
function renderReader(article) {
  state.article = article;
  state.lookup = null;
  state.lookupRequest = null;
  state.lookupVersion++;
  const paragraphs = article.content.split(/\n\s*\n|\n/).filter(p => p.trim());
  app.classList.add('reader-page');
  app.innerHTML = `
    <div class="reader-toolbar">
      <a class="reader-back" href="#library">${icon('arrow-left')}<span data-i18n="返回文章库">返回文章库</span></a>
      <div class="reader-controls" aria-label="阅读工具栏" data-i18n-aria-label="阅读工具栏">
        <div class="control-group"><span class="control-label" data-i18n="字号">字号</span>
          <button class="icon-button" data-action="font-down" aria-label="减小字号" data-i18n-aria-label="减小字号">${icon('minus')}</button>
          <span class="font-label" id="font-label">${state.settings.font_size}</span>
          <button class="icon-button" data-action="font-up" aria-label="增大字号" data-i18n-aria-label="增大字号">${icon('plus')}</button>
        </div>
        <button class="reader-tool-button" data-reader-action="toc" aria-haspopup="dialog" title="段落目录（T）" data-i18n-title="段落目录（T）">${icon('book')}<span data-i18n="目录">目录</span></button>
        <button class="reader-tool-button" data-reader-action="settings" aria-haspopup="dialog" title="阅读设置（S）" data-i18n-title="阅读设置（S）">${icon('settings')}<span data-i18n="阅读设置">阅读设置</span></button>
        <button class="reader-tool-button" data-reader-action="focus" aria-pressed="false" title="专注模式（F）" data-i18n-title="专注模式（F）">${icon('leaf')}<span data-i18n="专注">专注</span></button>
        <button class="reader-tool-button" data-reader-action="fullscreen" aria-pressed="false" title="全屏阅读（G）" data-i18n-title="全屏阅读（G）">${icon('fullscreen')}<span data-i18n="全屏">全屏</span></button>
        <button class="reader-tool-button" data-reader-action="lookup" title="打开查词面板" data-i18n-title="打开查词面板">${icon('search')}<span data-i18n="查词">查词</span></button>
        <button class="reader-tool-button" data-study-action="open-context" aria-haspopup="dialog">${icon('info')}<span data-i18n="AI 语境">AI 语境</span></button>
        <button class="reader-tool-button" data-study-action="open-translation" aria-haspopup="dialog">${icon('book')}<span data-i18n="翻译">翻译</span></button>
        <button class="reader-tool-button reader-help-button" data-reader-action="help" aria-label="阅读操作帮助" data-i18n-aria-label="阅读操作帮助" title="快捷键与手机手势（?）" data-i18n-title="快捷键与手机手势（?）">${icon('info')}</button>
      </div>
    </div>
    <div class="reading-flow-bar" aria-label="阅读与学习节奏" data-i18n-aria-label="阅读与学习节奏">
      <label class="reading-mode-label"><span data-i18n="模式">模式</span><select data-reader-setting="reading_mode" aria-label="阅读模式" data-i18n-aria-label="阅读模式"><option value="fluent" data-i18n="流畅阅读">流畅阅读</option><option value="intensive" data-i18n="精读模式">精读模式</option></select></label>
      <span id="flow-mode-hint" class="flow-mode-hint"></span>
      <button class="button small" data-flow-action="current-paragraph" data-i18n="本段生词">本段生词</button>
      <button class="button small pending-open" data-flow-action="queue" aria-haspopup="dialog"><span data-i18n="待查词">待查词</span> <span id="pending-word-count">…</span></button>
      <span id="flow-feedback" class="flow-feedback" role="status"></span><button class="button small" data-flow-action="retry-save" hidden data-i18n="重试暂存">重试暂存</button>
    </div>
    <div class="reader-grid"><div class="reader-scroll" tabindex="0" role="region" aria-label="文章阅读区域" data-i18n-aria-label="文章阅读区域"><article class="reader-paper">
      <div class="reader-label"><span>${escapeHTML(ui(article.category))}</span><span> / </span>${levelTag(article.level)}<span title="${escapeHTML(article.difficulty.method)}">${ui("易读度 ", "Readability ")}${article.difficulty_score}</span></div>
      <h1 class="reader-title" lang="en" dir="auto">${escapeHTML(article.title)}</h1>
      ${article.subtitle?`<p class="reader-subtitle">${escapeHTML(article.subtitle)}</p>`:''}
      <div class="reader-byline"><span>${escapeHTML(article.source)}</span><span>${icon('clock')} ${article.reading_minutes}${ui(' 分钟', ' min')}</span><span>${article.word_count}${ui(' 词', ' words')}</span>
        ${article.source_url?`<a href="${escapeHTML(article.source_url)}" target="_blank" rel="noopener noreferrer" data-i18n="阅读原文 ↗">阅读原文 ↗</a>`:(article.is_demo?'<span data-i18n="原创学习示例">原创学习示例</span>':'<span data-i18n="个人导入">个人导入</span>')}
        <div class="reader-tools"><button class="icon-button danger" data-action="delete-article" title="删除文章" data-i18n-title="删除文章" aria-label="删除文章" data-i18n-aria-label="删除文章">${icon('trash')}</button></div>
      </div>
      <div class="reader-practice-links"><a class="button small" href="/writing?article=${article.id}" data-i18n="根据文章写作">根据文章写作</a><a class="button small" href="#listening/${article.id}" data-i18n="听力与口语训练">听力与口语训练</a><a class="button small" href="#exercises/${article.id}" data-i18n="阅读练习与测验">阅读练习与测验</a><a class="button small" href="#personalization/article/${article.id}" data-i18n="个人难度与读后反馈">个人难度与读后反馈</a></div>
      <div class="reader-hint">${icon('pen')}<span data-i18n="单击或划选一个英文单词，查看释义并收藏">单击或划选一个英文单词，查看释义并收藏</span></div>
      <p id="reader-layout-note" class="reader-layout-note" hidden></p>
      <div class="article-body" id="article-body" lang="en">${paragraphs.map((p, i) => `<p class="paragraph" id="reader-p-${i}" data-number="${i+1}" tabindex="-1">${tokenizeParagraph(p.trim())}<button class="paragraph-finish" data-flow-paragraph="${i}" lang="zh-CN" hidden></button></p>`).join('')}</div>
      <div class="reader-end"><small>THE END · A NEW PERSPECTIVE</small><p data-i18n="又多了解了一点世界，也离目标近了一点。">又多了解了一点世界，也离目标近了一点。</p>
        <button class="button primary" data-action="complete-reading" id="complete-reading">${icon('check')}${article.progress>=100?ui('已完成阅读'):ui('标记为已读 · 完成打卡')}</button>
      </div>
    </article></div>
    <aside class="reader-margin"><div class="margin-heading"><span data-i18n="阅读的留白">阅读的留白</span><span>THE MARGIN</span><button class="icon-button margin-close" data-action="close-margin" aria-label="关闭查词面板" data-i18n-aria-label="关闭查词面板">${icon('close')}</button></div>
      <form class="lookup-form" id="lookup-form"><input name="word" aria-label="输入英文单词或短语查词" data-i18n-aria-label="输入英文单词或短语查词" placeholder="输入单词，或直接点击正文" data-i18n-placeholder="输入单词，或直接点击正文" maxlength="60" required><button type="submit" class="icon-button" aria-label="查询单词" data-i18n-aria-label="查询单词">${icon('search')}</button></form>
      ${MarginDictionary.controls()}<div id="learning-panel">${marginPlaceholder()}</div>
    </aside><aside id="flow-reminder" class="flow-reminder" role="status" aria-live="polite" hidden><strong data-i18n="给学习留一点空隙">给学习留一点空隙</strong><p></p><div><button class="button small primary" data-flow-action="queue" data-i18n="整理待查词">整理待查词</button><button class="button small" data-flow-action="reminder-later" data-i18n="5 分钟后提醒">5 分钟后提醒</button><button class="button small" data-flow-action="reminder-dismiss" data-i18n="继续阅读">继续阅读</button></div></aside></div>
    <div id="study-selection-bar" class="study-selection-bar" hidden><span></span><button class="button small" data-study-action="explain-selection" data-i18n="AI 释义所选">AI 释义所选</button><button class="button small" data-study-action="translate-selection" data-i18n="翻译所选">翻译所选</button></div>
    <div class="reading-status"><span class="status-progress"><span class="mini-progress"><span id="reading-bar" style="width:${article.progress}%"></span></span><span id="reading-percent">${Math.round(article.progress)}%${ui(' 已读', ' read')}</span></span><div class="reader-step-controls"><button class="icon-button" data-reader-action="previous" aria-label="上一段" data-i18n-aria-label="上一段" title="上一段（[）" data-i18n-title="上一段（[）">${icon('chevron-left')}</button><span id="reader-position-label"></span><button class="icon-button" data-reader-action="next" aria-label="下一段" data-i18n-aria-label="下一段" title="下一段（]）" data-i18n-title="下一段（]）">${icon('chevron-right')}</button></div><span class="status-saved" id="save-status">${icon('shield')}<span data-i18n="进度自动保存在本机">进度自动保存在本机</span></span></div>`;
  localizeUI(app);
  activeSession = readingSessions.get(article.id) || {id:article.id, seconds:0, position:article.position, progress:article.progress, saving:null, dirty:false};
  readingSessions.set(article.id, activeSession);
  lastActive = lastTick = performance.now();
  $$('.paragraph').forEach((p, i) => { p.dataset.plain = paragraphs[i].trim(); });
  reader = new MarginReader({root:app, settings:state.settings, onChange:saveSettings,
    onLayout:()=>onReaderScroll({layoutOnly:true}), toast});
  writingTrainer = new MarginWriting({root:app, article, reader, api, toast});
  flow = new MarginFlow({root:app, article, reader, settings:state.settings, api, toast,
    getWords:()=>state.words, onLookup:(word,article,index)=>learningEvents?.observe(word,article,index), onWords:async()=>{state.words=(await api('/vocabulary')).words;await refreshStats();}, lookup});
  study = new MarginStudy({root:app,article,reader,settings:state.settings,api,toast,onSettings:saveSettings,getLookup:()=>state.lookup});
  requestAnimationFrame(() => {
    const scroller = readerScroller();
    if (!scroller || state.article!==article || state.view!=='reader' || !activeSession || !reader) return;
    const limit = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    scroller.scrollTop = limit * activeSession.position / 100;
    reader.remember();
    scroller.addEventListener('scroll', onReaderScroll, {passive:true});
  });
  if (!speechPlayer) speechPlayer = new MarginSpeech({api, toast});
  speechPlayer.mount({root:app, article, reader});
}
function marginPlaceholder() { return `<div class="margin-placeholder"><div class="margin-symbol">${icon('book')}</div><h3>${ui("好奇心，值得停留", "Look up a word")}</h3><p>${ui("在上方输入单词，主动查询。释义默认折叠，可以按需展开。", "Enter a word above. Expand the definition when you need it.")}</p></div><div class="margin-tip"><span class="eyebrow">A SMALL READING TIP</span><p>${ui("流畅模式点词先暂存，精读模式点词打开查词卡片。读完一段，再整理遇见的词。", "Queue words in fluent mode or look them up as you read in close-reading mode.")}</p></div>`; }
let settingsQueue = Promise.resolve();
let settingsRevision = 0;
function saveSettings(patch) {
  const anchor = reader?.captureAnchor();
  const previousLookup = state.lookup || state.lookupRequest;
  Object.assign(state.settings,patch); flow?.apply(state.settings); study?.apply(state.settings); reader?.apply(state.settings,anchor);
  if (Object.hasOwn(patch,'dictionary_provider') && previousLookup) lookup(previousLookup.word,previousLookup.context,{paragraphIndex:previousLookup.paragraphIndex,expanded:previousLookup.expanded,manualMeaning:previousLookup.manualMeaning});
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
  $('#reading-bar').style.width=activeSession.progress+'%'; $('#reading-percent').textContent=Math.round(activeSession.progress)+'%'+ui(' 已读', ' read');
}
async function flushSession(session=activeSession, keepalive=false) {
  if(!session) return;
  if(session.saving) { await session.saving; if(session.dirty||session.seconds) return flushSession(session,keepalive); return; }
  if(!session.dirty&&!session.seconds) return;
  const batch=session.pendingProgress || {seconds:Math.min(60,session.seconds),progress:session.progress,position:session.position,
    request_id:window.crypto?.randomUUID?.() || Date.now().toString(36)+'_'+Math.random().toString(36).slice(2)};
  session.pendingProgress=batch;
  const {seconds,progress,position}=batch;
  session.dirty=false;
  session.saving=api(`/articles/${session.id}/progress`,{method:'PATCH',body:batch,keepalive}).then(()=> {
    session.pendingProgress=null;
    if(session.progress!==progress || session.position!==position)session.dirty=true;
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
  state.lookupRequest={word,context,articleId,paragraphIndex:options.paragraphIndex??-1,
    expanded:options.expanded??!state.settings.definition_collapsed,manualMeaning:options.manualMeaning||''};
  const anchor=reader.captureAnchor();
  revealMargin(); $('#learning-panel').innerHTML=`<div class="lookup-loading"><span class="spinner"></span>正在查找 ${escapeHTML(word)}…</div>`;
  reader.restoreAnchor(anchor);
  $$('.word.selected').forEach(n=>n.classList.remove('selected')); $$('.word').filter(n=>n.dataset.word.toLowerCase()===word.toLowerCase()).forEach(n=>n.classList.add('selected'));
  try {
    learningEvents?.observe(word,state.article,options.paragraphIndex??-1);
    const result=await api('/dictionary?word='+encodeURIComponent(word)+'&provider='+encodeURIComponent(state.settings.dictionary_provider||'auto'));
    if(version!==state.lookupVersion||state.view!=='reader') return;
    state.lookup={...state.lookupRequest,...result}; state.lookupRequest=null; renderLookup();
  } catch(error) {if(version===state.lookupVersion&&reader===activeReader&&$('#learning-panel')) $('#learning-panel').innerHTML=`<p class="error-note">${escapeHTML(error.message)}</p><button class="button small" data-action="return-reading">继续阅读</button>`;}
}
function renderLookup() {
  const entry = state.lookup;
  if (!entry) return;
  const saved = state.words.some(w => w.word.toLowerCase() === entry.word.toLowerCase());
  $('#learning-panel').innerHTML = `<div class="lookup-result">
    <div class="lookup-title"><h2 lang="en">${escapeHTML(entry.word)}</h2></div>
    <details class="lookup-definition" ${entry.expanded?'open':''}><summary>${ui(entry.expanded?'收起释义':'展开释义')}</summary>
    ${MarginDictionary.pronunciation(entry)}
    ${entry.found?`<div class="definition"><span class="part-of-speech">${escapeHTML(entry.part_of_speech)}</span>${escapeHTML(entry.meaning)}</div>`:`<p class="lookup-note">${escapeHTML(entry.message)}</p><label>${ui("手动填写释义", "Enter a definition")}<textarea id="manual-meaning" rows="3" maxlength="3000" placeholder="${ui("写下释义后即可收藏", "Enter a definition before saving")}">${escapeHTML(entry.manualMeaning||'')}</textarea></label>`}
    <div class="source-caption">${escapeHTML(entry.source)}${entry.cached?ui(' · 本地缓存', ' · Cached locally'):''}</div>
    ${entry.context?`<blockquote class="context-block" lang="en"><span class="context-label" lang="${interfaceLanguage}">${ui("在这篇文章里", "In this article")}</span>${escapeHTML(entry.context)}</blockquote>`:entry.example?`<blockquote class="context-block" lang="en"><span class="context-label" lang="${interfaceLanguage}">${ui("词典例句", "Dictionary example")}</span>${escapeHTML(entry.example)}</blockquote>`:''}
    ${MarginDictionary.knowledge(entry)}<button class="button small" data-action="retry-lookup">${ui("重新查询", "Look up again")}</button><button class="button primary" data-action="save-lookup" ${saved&&!entry.context?'disabled':''}>${icon(saved?'check':'plus')}${saved?(entry.context?ui('已加入生词本 · 追加此原句', 'Save this sentence'):ui('已加入生词本', 'Saved to vocabulary')):ui('加入生词本', 'Add to vocabulary')}</button>
    </details><div class="lookup-study-actions"><button class="button small" data-study-action="lookup-context">${ui("结合原文释义", "Explain in context")}</button><button class="button small" data-study-action="lookup-translation">${ui("翻译原文表达", "Translate expression")}</button></div><div class="lookup-flow-actions"><button class="button small" data-action="defer-lookup">${ui("暂存，稍后处理", "Queue for later")}</button><button class="button small" data-action="return-reading">继续阅读</button></div>
  </div>`;
}
async function saveLookup() {
  const entry=state.lookup;if(!entry)return;
  const meaning=entry.meaning||$('#manual-meaning')?.value.trim();if(!meaning){toast('先写下这个单词的释义。',true);return;}
  const button=$('[data-action="save-lookup"]');button.disabled=true;
  try {const result=await api('/vocabulary',{method:'POST',body:{word:entry.word,phonetic:entry.phonetic,part_of_speech:entry.part_of_speech,meaning,context:entry.context,article_id:entry.articleId}});
    const vocabulary=await api('/vocabulary');state.words=vocabulary.words;await refreshStats();
    if(state.lookup===entry&&$('#learning-panel')) renderLookup(); flow?.refreshMarks();
    toast(result.duplicate?'原句已追加到已有词条；相同原句不会重复保存。':'已加入生词本，并保存了原文语境。');
  }catch(error){toast(error.message,true);button.disabled=false;}
}
function summaryTiles() {
  const stats=state.stats;return `<div class="summary-strip"><div class="summary-tile"><span>读过的文章${icon('book')}</span><strong>${stats.completed_articles}<small>篇</small></strong></div><div class="summary-tile"><span>收集的生词${icon('bookmark')}</span><strong>${stats.total_words}<small>个</small></strong></div><div class="summary-tile"><span>累计阅读${icon('clock')}</span><strong>${minutes(stats.total_seconds)}<small>分钟</small></strong></div><div class="summary-tile"><span>连续学习${icon('flame')}</span><strong>${stats.streak}<small>天</small></strong></div></div>`;
}
async function renderVocabulary() {
  wordbookUI?.destroy();
  wordbookUI=new MarginWordbook({root:app,api,toast,confirm:confirmDelete,onChange:async()=>{
    state.words=(await api('/vocabulary')).words;await refreshStats();
  }});
  await wordbookUI.render();
}
function renderWordRows(){wordbookUI?.rows();}

async function renderActivity(month='',version=state.routeVersion) {
  const stats=await api('/stats'+(month?'?month='+month:''));if(version!==state.routeVersion||state.view!=='activity')return;state.month=stats.month;
  const [year,monthNum]=stats.month.split('-').map(Number);const first=new Date(year,monthNum-1,1);const dayCount=new Date(year,monthNum,0).getDate();
  const blank=(first.getDay()+6)%7;const activity=new Map(stats.days.map(day=>[day.date,day]));
  for(const makeup of stats.makeups||[])if(!activity.has(makeup.day))activity.set(makeup.day,{date:makeup.day,makeup:true,note:makeup.note});
  app.innerHTML=`<div class="page-wrap"><a class="button small analytics-entry" href="#analytics">查看学习数据分析 →</a> <a class="button small analytics-entry" href="#plans">设定学习目标 →</a>${intro('SMALL STEPS, REAL GROWTH','每一次翻开，都算数。','你的学习足迹，正在把零散的时间变成看得见的积累。')}${summaryTiles()}<div class="calendar-layout"><section class="calendar-card"><div class="calendar-heading"><h2>${year} 年 ${monthNum} 月<span class="eyebrow">YOUR READING DAYS</span></h2><div class="calendar-actions"><button class="button small" data-action="checkin" ${stats.checked_in?'disabled':''}>${icon('check')}${stats.checked_in?'今日已打卡':'今日打卡'}</button><div class="month-nav"><button class="icon-button" data-month-offset="-1" aria-label="上个月">${icon('chevron-left')}</button><button class="icon-button" data-month-offset="1" aria-label="下个月">${icon('chevron-right')}</button></div></div></div><div class="calendar-grid">${['一','二','三','四','五','六','日'].map(d=>`<div class="calendar-weekday">${d}</div>`).join('')}${Array(blank).fill('<div class="calendar-day blank"></div>').join('')}${Array.from({length:dayCount},(_,index)=> {const key=stats.month+'-'+String(index+1).padStart(2,'0');const day=activity.get(key);return `<button class="calendar-day ${day?'active ':''}${day?.makeup?'plan-makeup-day ':''}${key===stats.today?'today ':''}${key>stats.today?'future':''}" data-calendar-day="${key}" aria-label="${key}${day?.makeup?' 已补签':day?' 已学习':' 暂无学习记录'}" title="${day?.makeup?'补签 · 无阅读时长':day?'阅读 '+minutes(day.reading_seconds)+' 分钟 · 完成 '+day.articles_completed+' 篇':'暂无学习记录'}">${index+1}${day?'<span class="dot"></span>':''}</button>`;}).join('')}</div><div class="calendar-legend"><span><i class="legend-square"></i>有学习记录</span><span>描边为今天</span><span>虚线标为补签</span></div><div id="calendar-detail" class="calendar-detail">这个月，你已经在 ${stats.days.length} 天里留下一点进步。</div></section><aside class="insight-card"><h3>时间会看见你的坚持</h3><div class="big-number">${stats.active_days}<small>天</small></div><p>这是你与英语相遇的日子。<br>不必每次都很多，只要常常开始。</p><div class="insight-divider"></div><div class="insight-line"><span>今日阅读</span><strong>${minutes(stats.today_seconds)}${ui(' 分钟', ' min')}</strong></div><div class="insight-line"><span>已掌握词汇</span><strong>${stats.mastered_words} 个</strong></div><div class="quote">“Little by little,<br>a little becomes a lot.”<small>KEEP YOUR CURIOSITY ALIVE</small></div></aside></div><div class="page-footnote">${icon('shield')}数据保存在本机 · 北京时间 · 阅读计时只统计前台活跃时间</div></div>`;
  state.calendarStats=stats;
}

function openSettings() { $('#settings-dialog').showModal(); }
function showWordDialog(id=null) {
  state.editWord=id;const form=$('#word-form');form.reset();$('#word-error').textContent='';
  const word=state.words.find(w=>w.id===id);$('#word-dialog-title').textContent=word?ui('记住 ', 'Edit ')+word.word:ui('添加一个想记住的词');
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
  practiceUI?.destroy();practiceUI=null;personalUI?.destroy();personalUI=null;analyticsUI?.destroy();analyticsUI=null;dataUI?.destroy();dataUI=null;planUI?.destroy();planUI=null;
  if(activeSession){try{await flushSession(activeSession);}catch(error){toast(error.message,true);}activeSession=null;}
  if(version!==state.routeVersion)return;
  study?.destroy();study=null;flow?.destroy();flow=null;reader?.destroy();reader=null;
  wordbookUI?.destroy();wordbookUI=null;reviewUI?.destroy();reviewUI=null;
  const [route,id,sessionId]=(location.hash.slice(1)||'library').split('/');state.view=['library','reader','vocabulary','review','listening','exercises','personalization','analytics','data','plans','activity'].includes(route)?route:'library';
  if (['reader','library'].includes(state.view)) app.removeAttribute('lang');
  else app.setAttribute('lang', 'zh-CN');
  app.classList.remove('reader-page');app.scrollTop=0;updateSidebar();app.innerHTML=loading(ui('正在打开你的阅读手记…'));
  try {
    if(state.view==='reader') {if(!/^\d+$/.test(id||''))throw new Error('文章地址无效。');const article=await api('/articles/'+id);if(version===state.routeVersion)renderReader(article);}
    else {await refreshStats();if(version!==state.routeVersion)return;
      if(state.view==='library'){if(id){try{const category=decodeURIComponent(id);if(['科技','文化','科学','生活','环境','其他'].includes(category)){state.category=category;state.difficulty='全部';state.query='';}}catch{}}state.articles=(await api('/articles')).articles;if(version===state.routeVersion)renderLibrary();}
      else if(state.view==='vocabulary'){state.words=(await api('/vocabulary')).words;if(version===state.routeVersion)await renderVocabulary();}
      else if(state.view==='review'){reviewUI=new MarginSRS({root:app,api,toast});await reviewUI.render();}
      else if(state.view==='plans'){
        planUI=new MarginStudyPlans({root:app,api,toast,confirm:confirmDelete,refreshStats,reminders:studyReminders,
          onOverview:({progress,goals})=>{
            state.stats.today_seconds=progress.seconds;
            state.stats.daily_goals={reading_minutes:goals.reading_minutes,new_words:goals.new_words,words_today:progress.words};
            updateSidebar();
          }});
        await planUI.render();
      }
      else if(state.view==='data'){dataUI=new MarginData({root:app,api,toast,confirm:confirmDelete});await dataUI.render();}
      else if(state.view==='analytics'){analyticsUI=new MarginAnalytics({root:app,api,toast,tracker:learningEvents});await analyticsUI.render();}
      else if(state.view==='personalization'){personalUI=new MarginPersonalization({root:app,api,toast,articleId:id==='article'?sessionId:null});await personalUI.render();}
      else if(state.view==='listening'){practiceUI=new MarginListening({root:app,api,toast,articleId:id});await practiceUI.render();}
      else if(state.view==='exercises'){practiceUI=new MarginExercises({root:app,api,toast,articleId:id==='session'?null:id,sessionId:id==='session'?sessionId:null});await practiceUI.render();}
      else await renderActivity('',version);
    }
  }catch(error){if(version===state.routeVersion)app.innerHTML=empty(ui('这页暂时没有翻开'),escapeHTML(error.message),'<a class="button primary" href="#library">返回文章库</a>','info');}
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
    if(button.dataset.calendarDay){
      const date=button.dataset.calendarDay;
      const day=state.calendarStats.days.find(d=>d.date===date);
      const makeup=state.calendarStats.makeups?.find(d=>d.day===date);
      const detail=day?`${date} · 阅读 ${minutes(day.reading_seconds)}${ui(' 分钟', ' min')} ${day.reading_seconds%60} 秒 · 完成 ${day.articles_completed} 篇${day.manual?' · 手动打卡':''}`:`${date} · 这一天暂无学习记录`;
      $('#calendar-detail').textContent=makeup?`${date} · 补签${makeup.note?' · '+makeup.note:''}${day?' · 已有后续学习记录':''}`:detail;
      return;
    }
    if(button.dataset.importTab){state.importMode=button.dataset.importTab;$$('[data-import-tab]').forEach(b=>b.classList.toggle('active',b===button));$('#paste-fields').hidden=state.importMode!=='paste';$('#url-fields').hidden=state.importMode!=='url';$$('input,textarea',$('#paste-fields')).forEach(input=>input.disabled=state.importMode!=='paste');$('#url-fields input').disabled=state.importMode!=='url';$('#url-fields input').required=state.importMode==='url';$('#import-error').textContent='';return;}
    if(button.dataset.dictionaryTerm){const entry=state.lookup;await lookup(button.dataset.dictionaryTerm,entry?.context||'',{paragraphIndex:entry?.paragraphIndex??-1,expanded:true});return;}
    const action=button.dataset.action;
    if(action==='import'){$('#import-error').textContent='';$('#import-dialog').showModal();}
    else if(action==='close-dialog')button.closest('dialog').close();
    else if(action==='close-margin'){
      $('.reader-margin')?.classList.remove('mobile-open');
      $('[data-reader-action="lookup"]')?.focus({preventScroll:true});
    }
    else if(action==='settings')openSettings();
    else if(action==='reset-filters'){Object.assign(state,{category:'全部',difficulty:'全部',query:'',searchMode:'text',source:'',tag:'',minWords:'',maxWords:'',minMinutes:'',maxMinutes:'',readingStatus:''});renderLibrary();}
    else if(action==='search-articles'){clearTimeout(searchTimer);await renderArticleCards();}
    else if(action==='similar-articles'){
      const id=state.article.id,results=$('#similar-results');button.disabled=true;results.textContent='正在查找；首次使用需下载语义模型，请稍候…';
      try {const data=await api(`/articles/${id}/similar`,{timeoutMs:300000});if(state.view==='reader'&&state.article?.id===id&&$('#similar-results')===results)results.innerHTML=data.articles.length?data.articles.map(a=>`<button class="similar-item" data-open="${a.id}">${escapeHTML(a.title)} <small>${escapeHTML(a.source)} · ${escapeHTML(a.level)}</small></button>`).join(''):'文章库中暂时没有其他文章。';}
      catch(error){if(state.view==='reader'&&state.article?.id===id)results.textContent=error.message;}
      finally{button.disabled=false;}
    }
    else if(action==='checkin'){button.disabled=true;await api('/checkin',{method:'POST',body:{}});await refreshStats();if(state.view==='library')renderLibrary();else if(state.view==='activity')await renderActivity(state.month);toast('今天的学习，已留下足迹。');}
    else if(action==='font-down'||action==='font-up'){saveSettings({font_size:Math.max(16,Math.min(28,state.settings.font_size+(action==='font-up'?1:-1)))});}
    else if(action==='retry-lookup'&&state.lookup){const entry=state.lookup;await lookup(entry.word,entry.context,{paragraphIndex:entry.paragraphIndex,expanded:entry.expanded,manualMeaning:entry.manualMeaning});}
    else if(action==='save-lookup')await saveLookup();
    else if(action==='defer-lookup'&&state.lookup){const entry=state.lookup;await flow.deferWord(entry.word,entry.paragraphIndex);}
    else if(action==='return-reading'){const anchor=reader.captureAnchor();$('.reader-margin')?.classList.remove('mobile-open');reader.scroller.focus({preventScroll:true});reader.restoreAnchor(anchor);}
    else if(action==='complete-reading'){button.disabled=true;activeSession.progress=100;activeSession.dirty=true;await flushSession();updateReaderStatus();button.innerHTML=icon('check')+ui('已完成阅读');await refreshStats();toast('读完一篇，点亮一天。进度已保存。');}
    else if(action==='delete-article'){
      const article=state.article, session=activeSession;
      if(await confirmDelete(`删除文章「${article.title}」？译文与 AI 释义会随文章删除；已收藏的单词与历史学习记录会保留。`)){
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
document.addEventListener('toggle',event=>{if(event.target.matches('.lookup-definition')&&state.lookup){state.lookup.expanded=event.target.open;event.target.querySelector('summary').textContent=ui(event.target.open?'收起释义':'展开释义');}},{capture:true});
document.addEventListener('change',async event=> {
  const input=event.target;
  try {if(input.id==='difficulty-filter'){state.difficulty=input.value;renderArticleCards();}
    else if(input.dataset.wordMastery){const id=Number(input.dataset.wordMastery);input.disabled=true;const result=await api('/vocabulary/'+id,{method:'PATCH',body:{mastery:input.value}});const word=state.words.find(w=>w.id===id);Object.assign(word,result);await refreshStats();if(state.view==='vocabulary')renderVocabulary();toast('掌握度已更新。');}
  }catch(error){input.disabled=false;toast(error.message,true);if(input.dataset.wordMastery)input.value=state.words.find(w=>w.id===Number(input.dataset.wordMastery)).mastery;}
});
document.addEventListener('submit',async event=> {
  event.preventDefault();const form=event.target;
  if(form.closest('#article-qa-dialog'))return;
  if(form.id==='lookup-form'){lookup(form.elements.word.value.trim());return;}
  if(form.closest('#writing-dialog'))return;
  const button=$('button[type="submit"]',form);if(!button)return;button.disabled=true;
  try {if(form.id==='import-form') {
    $('#import-error').textContent='';button.textContent=ui(state.importMode==='url'?'正在提取正文…':'正在导入…');
    const payload=Object.fromEntries(new FormData(form));const result=await api('/articles',{method:'POST',body:payload});$('#import-dialog').close();form.reset();toast(result.duplicate?'文章已在库中，为你打开。':'文章已导入，开始新的阅读。');location.hash='reader/'+result.id;if(location.hash==='#reader/'+result.id&&state.article?.id===result.id)await navigate();
  }else if(form.id==='word-form') {
    $('#word-error').textContent='';const payload=Object.fromEntries(new FormData(form));const result=await api('/vocabulary'+(state.editWord?'/'+state.editWord:''),{method:state.editWord?'PATCH':'POST',body:payload});$('#word-dialog').close();state.words=(await api('/vocabulary')).words;await refreshStats();if(state.view==='vocabulary')renderVocabulary();toast(result.duplicate?'这个单词已经收藏过了。':'生词已保存。');
  }}catch(error){const target=form.id==='import-form'?$('#import-error'):$('#word-error');if(target)target.textContent=error.message;else toast(error.message,true);}
  finally{button.disabled=false;if(form.id==='import-form')button.innerHTML='<span data-i18n="导入并开始阅读">'+ui('导入并开始阅读')+'</span>'+icon('arrow-right');}
});

document.addEventListener('mouseup',event=> {
  if(reader?.ignoreWordTap())return;
  const body=event.target.closest('#article-body');if(!body||event.target.closest('button'))return;
  const selection=window.getSelection(),selected=selection?.toString().trim();
  if(speechPlayer?.clickToPlay && !selected)return;
  if(selected&&selection.anchorNode&&body.contains(selection.anchorNode)&&selection.focusNode&&body.contains(selection.focusNode)) {
    const paragraph=event.target.closest('.paragraph');
    if(MarginDictionary.valid(selected)&&paragraph?.contains(selection.anchorNode)&&paragraph.contains(selection.focusNode))flow?.selectWord(selected,paragraph);
    return;
  }
  const target=event.target.closest('[data-word]');if(target)flow?.selectWord(target.dataset.word,target.closest('.paragraph'));
});
document.addEventListener('keydown', event => { lastActive = performance.now();if(event.target.id==='article-search'&&event.key==='Enter'){event.preventDefault();clearTimeout(searchTimer);renderArticleCards();} });
$('#confirm-dialog').addEventListener('cancel',()=> {if(confirmResolve){confirmResolve(false);confirmResolve=null;}});
window.addEventListener('hashchange', async () => {
  const focus = document.activeElement;
  const expected = state.routeVersion + 1;
  await navigate();
  if (expected !== state.routeVersion) return;
  const announcement = $('#route-status');
  if (announcement) announcement.textContent = $('#page-label').textContent;
  if (document.activeElement === focus || document.activeElement === document.body) app.focus({preventScroll:true});
});
document.addEventListener('margin-interface-change', () => {
  if (interfaceLanguage === MarginInterface.language()) return;
  interfaceLanguage = MarginInterface.language();
  updateSidebar();
  if (state.view === 'library' && $('#article-grid')) renderLibrary();
  else if (state.view === 'reader') {
    flow?.updateLocalizedHints();
    reader?.updateCurrent();
  }
  const network = $('#network-status-text');
  if (network) network.textContent = ui(typeof MarginData !== 'undefined' && MarginData.offline ? '离线模式 · 本机服务' : '本地学习空间');
});
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
async function boot(){
  if(typeof MarginData!=='undefined'){const info=await api('/data/state');dataGeneration=info.generation;MarginData.prepare(info);}
  if(typeof MarginLearningEvents!=='undefined')learningEvents=new MarginLearningEvents({api,toast});
  await loadData();
  if(typeof MarginStudyReminders!=='undefined')studyReminders=new MarginStudyReminders(api);
  await navigate();
}
boot().catch(error=> {app.innerHTML=empty(ui('暂时无法打开阅读空间'),escapeHTML(error.message),'<button class="button primary" data-action="retry-app">重试连接</button>','info');});
document.addEventListener('click',event=> {if(event.target.closest('[data-action="retry-app"], [data-action="reload-data"]'))location.reload();});
