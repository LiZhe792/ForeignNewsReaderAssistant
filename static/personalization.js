/* Local reading preferences, explainable recommendations and reversible feedback. */
'use strict';
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const options = (items, value) => items.map(([v, label]) => `<option value="${esc(v)}" ${v === value ? 'selected' : ''}>${esc(label)}</option>`).join('');
  const btn = (action, label, attrs = '') => `<button type="button" class="button small" data-personal="${action}" ${attrs}>${label}</button>`;
  class Personalization {
    constructor({root, api, toast, articleId = null, compact = false}) {
      Object.assign(this, {root, api, toast, articleId, compact});
      this.abort = new AbortController(); this.sequence = 0; this.mode = 'recommendations'; this.items = new Map();
      for (const type of ['click', 'change', 'submit']) root.addEventListener(type, event => this.handle(event), {signal:this.abort.signal});
    }
    request(path, method = 'GET', body) { return this.api('/personalization' + path, {method, body, signal:this.abort.signal}); }
    destroy() { this.dead = true; this.abort.abort(); this.sequence++; }
    async render() {
      this.mode = 'recommendations';
      const seq=++this.sequence;
      try {
        const data = await this.request(this.articleId ? '/articles/' + this.articleId : '/recommendations?limit=' + (this.compact ? 3 : 12));
        if (this.dead || seq!==this.sequence) return;
        this.profile = data.profile; this.evidence = data.evidence;
        if (this.compact) {
          this.root.innerHTML = `<div class="personal-compact-heading"><div><span class="eyebrow">A READING PATH OF YOUR OWN</span><h2>为你选文</h2></div><a class="button small" href="#personalization">调整偏好与查看推荐 →</a></div><p>${this.profile.configured ? '结合你的水平、兴趣与阅读时间，从文章库挑选。' : '先按默认 B1 水平估算。设置你的偏好，让选文更合适。'}</p><div class="personal-compact-grid">${data.articles.map(a => `<a class="personal-mini" href="#reader/${a.id}"><small>${esc(a.category)} · 个人难度${esc(a.analysis.label)} · 约 ${a.analysis.minutes} 分钟</small><strong>${esc(a.title)}</strong><span>${esc(a.reasons[1] || a.reasons[0])} →</span></a>`).join('') || '<p>暂时没有新推荐。可查看已读文章或调整反馈。</p>'}</div>`;
          return;
        }
        if (this.articleId) {
          this.root.innerHTML = `<div class="page-wrap personal-page"><a class="reader-back" href="#reader/${this.articleId}">← 继续阅读</a><div class="page-intro"><div><span class="eyebrow">FIND YOUR READING PACE</span><h1>这篇文章适合我吗？</h1><p>依据学习记录估算，读后反馈可以帮助调整后续选文。</p></div><a class="button small" href="#personalization">设置偏好与推荐</a></div><div id="personal-evidence"></div><div id="personal-results"></div><p id="personal-message" role="status"></p></div>`;
          this.showEvidence(data.evidence); this.showArticles([{...data.article, analysis:data.analysis, feedback:data.feedback, reasons:[]}]);
          return;
        }
        this.root.innerHTML = `<div class="page-wrap personal-page"><div class="page-intro"><div><span class="eyebrow">A READING PATH OF YOUR OWN</span><h1>难度与推荐</h1><p>读感合适，也读你感兴趣的内容。每次选文都可以知道为什么。</p></div></div>
          <details class="personal-profile" ${!this.profile.configured ? 'open' : ''}><summary>我的阅读偏好 <span>水平 · 主题 · 时间</span></summary><div id="personal-profile-form"></div></details>
          <div id="personal-evidence"></div><div class="personal-list-heading"><h2>下一篇，读什么？</h2><div class="personal-tabs">${btn('recommendations','为我推荐','aria-pressed="true"')}${btn('feedback','反馈记录','aria-pressed="false"')}</div></div>
          <form id="personal-filters" class="personal-filters"><label>主题<select name="category">${options([['','所有主题'], ...['科技','文化','科学','生活','环境','其他'].map(s=>[s,s])], '')}</select></label><label>单篇用时<select name="max_minutes">${options([['','不限篇幅'],['5','5 分钟内'],['10','10 分钟内'],['20','20 分钟内'],['30','30 分钟内'],['60','60 分钟内']], '')}</select></label><label class="personal-check"><input type="checkbox" name="include_read" ${this.profile.preferences.exclude_read ? '' : 'checked'}>包含已读</label>${btn('refresh','刷新推荐')}</form>
          <p id="personal-list-note"></p><div id="personal-results"></div><p id="personal-message" role="status"></p><p class="personal-footnote">推荐来自本地文章库，不会自动获取外部文章。匹配分用于排序；难度标签与词频假设供选文参考。</p></div>`;
        this.showProfile(); this.showEvidence(data.evidence); this.showArticles(data.articles);
        this.root.querySelector('#personal-list-note').textContent = data.note;
      } catch (error) {
        if (!this.dead && seq===this.sequence) this.root.innerHTML = `<div class="personal-error"><p role="alert">${esc(error.message)}</p>${btn('reload','重新载入')}</div>`;
      }
    }
    showProfile() {
      const p = this.profile.preferences;
      this.root.querySelector('#personal-profile-form').innerHTML = `<form id="personal-profile"><fieldset class="personal-fields"><legend class="sr-only">阅读偏好</legend><label>自评阅读水平<select name="level">${options(['A2','B1','B2','C1','C2'].map(s=>[s,s+' · 自评参考']),p.level)}</select></label><label>希望的挑战程度<select name="challenge">${options([['gentle','轻松巩固'],['balanced','难度适中'],['stretch','适度挑战']],p.challenge)}</select></label><label>每次阅读时间（分钟）<input type="number" name="minutes" min="2" max="90" required value="${p.minutes}"></label><label>估计阅读速度（词/分钟）<input type="number" name="speed" min="60" max="400" required value="${p.speed}"></label></fieldset><fieldset class="personal-interests"><legend>感兴趣的主题 · 可多选</legend>${['科技','文化','科学','生活','环境','其他'].map(s=>`<label><input type="checkbox" name="interests" value="${s}" ${p.interests.includes(s)?'checked':''}>${s}</label>`).join('')}</fieldset><div class="personal-switches"><label><input type="checkbox" name="adaptive" ${p.adaptive?'checked':''}>根据近期有效记录小幅调整难度</label><label><input type="checkbox" name="exclude_read" ${p.exclude_read?'checked':''}>推荐默认排除已读文章</label></div><p class="personal-footnote">自评是选文起点，不是正式等级测评。阅读速度由你设置，不从页面停留时间推断。</p><div class="personal-save"><button type="submit" class="button primary">保存偏好并更新推荐</button>${btn('reset','恢复默认偏好')}<span id="personal-profile-status" role="status"></span></div></form>`;
    }
    showEvidence(e) {
      const text = !e.active ? '已关闭自动调整，继续使用你的自评水平。' : e.articles < 3 ? '记录待积累，当前以自评水平为起点。' : e.adjustment > 0 ? '近期反馈较轻松，选文难度小幅上调。' : e.adjustment < 0 ? '近期反馈较吃力，选文难度小幅下调。' : '近期记录支持保持当前难度。';
      this.root.querySelector('#personal-evidence').innerHTML = `<section class="personal-evidence"><strong>${text}</strong><p>${e.articles} 篇独立材料 · ${e.quiz_articles} 篇有效测验 · ${e.feedback_articles} 篇难度反馈（同篇合并）</p><small>${esc(e.note)} 测验仅采用内置题库中首轮有效理解题；抄写、错题重做和未作答不作为提级依据。</small></section>`;
    }
    metrics(a) {
      const m = a.analysis, v = m.vocabulary;
      const groupNames = {mastered:'手动已掌握', stable_review:'稳定复习', learning:'已记生词', assumed_familiar:'常用词假设', to_explore:'超出词频假设', unlisted:'词库未覆盖'};
      return `<details class="personal-analysis"><summary>查看难度依据与词汇分布</summary><div class="personal-metrics"><span>文本易读度<strong>${m.ease} / 100</strong></span><span>平均句长<strong>${m.sentence_length} 词</strong></span><span>手动掌握覆盖<strong>${m.confirmed_mastered_percent}%</strong></span><span>估计学习负荷<strong>${m.estimated_load_per_100} / 100 词</strong></span></div><dl class="personal-vocabulary">${Object.entries(groupNames).map(([key,label])=>`<div><dt>${label}</dt><dd>${v[key]} 词次</dd></div>`).join('')}</dl>${m.preview.length?`<p>可先关注：${m.preview.map(w=>`<span lang="en" title="${esc(w.basis)}">${esc(w.word)} ×${w.count}</span>`).join(' · ')}</p>`:''}<p class="personal-footnote">${esc(m.method)} 词频范围随自评水平变化，并非 CEFR 官方词表。</p></details>`;
    }
    showArticles(articles) {
      this.items = new Map(articles.map(a=>[a.id,a]));
      const cards = articles.map(a => {
        const f = a.feedback, m = a.analysis;
        return `<article class="personal-card" data-personal-card="${a.id}"><div class="personal-card-meta"><span>${esc(a.category || '文章分析')} · ${m.word_count} 词 · 约 ${m.minutes} 分钟</span><span class="personal-badge">个人难度 · ${esc(m.label)}</span>${a.progress >= 100 ? '<span>已读</span>' : ''}</div><h2><a href="#reader/${a.id}">${esc(a.title)}</a></h2>${a.subtitle?`<p>${esc(a.subtitle)}</p>`:''}<div class="personal-reasons">${a.reasons.map(r=>`<span>${esc(r)}</span>`).join('')}${a.score === undefined ? '' : `<small>匹配分 ${a.score}</small>`}</div>${this.metrics(a)}<div class="personal-feedback"><fieldset><legend>读后感受</legend>${[['easy','偏易'],['fit','合适'],['hard','偏难']].map(([value,label])=>btn('difficulty',label,`data-value="${value}" aria-pressed="${f.difficulty===value}"`)).join('')}</fieldset><fieldset><legend>选文偏好</legend>${[['more','多推荐这篇'],['less','少推荐这篇']].map(([value,label])=>btn('interest',label,`data-value="${value}" aria-pressed="${f.interest===value}"`)).join('')}</fieldset>${btn('clear','撤销反馈',!f.difficulty && f.interest==='neutral'?'disabled':'')}<a class="button small primary" href="#reader/${a.id}">去阅读 →</a></div></article>`;
      }).join('');
      this.root.querySelector('#personal-results').innerHTML = cards || `<div class="empty-state"><h2>${this.mode==='feedback'?'暂无匹配的反馈记录':'暂时没有匹配文章'}</h2><p>调整筛选、包含已读，或导入新的文章。少推荐的文章可以在反馈记录中恢复。</p><a class="button small" href="#library">查看文章库</a></div>`;
    }
    async refresh() {
      if (this.articleId) return this.render();
      const seq = ++this.sequence, filters = new FormData(this.root.querySelector('#personal-filters'));
      const query = new URLSearchParams({limit:'50', include_read:filters.has('include_read')?'1':'0'});
      for (const key of ['category','max_minutes']) if (filters.get(key)) query.set(key,filters.get(key));
      this.root.querySelector('#personal-message').textContent = '正在更新选文…';
      try {
        const mode = this.mode;
        const data = await this.request(mode==='feedback'?'/feedback':'/recommendations?'+query);
        if (this.dead || seq!==this.sequence) return;
        this.evidence=data.evidence; this.showEvidence(data.evidence);
        let items=data.articles;
        if(mode==='feedback') items=items.filter(a=>(!filters.get('category') || a.category===filters.get('category')) && (!filters.get('max_minutes') || a.analysis.minutes<=Number(filters.get('max_minutes'))) && (filters.has('include_read') || a.progress<100));
        this.showArticles(items); this.root.querySelector('#personal-list-note').textContent = mode==='feedback'?'这里可修改或撤销反馈；「少推荐」只影响选文，不删除文章。':data.note;
        this.root.querySelector('#personal-message').textContent = `已更新，共 ${items.length} 篇${data.matched>items.length?'（优先显示前 50 篇）':''}。`;
      } catch (error) {
        if (this.dead || seq!==this.sequence) return;
        this.root.querySelector('#personal-message').innerHTML = `${esc(error.message)} ${btn('refresh','重试更新')}`;
      }
    }
    async handle(event) {
      if (this.dead || this.compact) return;
      const target=event.target, action=target.closest('[data-personal]')?.dataset.personal;
      if (event.type==='change' && target.closest('#personal-filters')) { this.refresh(); return; }
      if (event.type==='submit') {
        if (!target.matches('#personal-profile, #personal-filters')) return;
        event.preventDefault(); event.stopPropagation();
        if (target.id==='personal-filters') { this.refresh(); return; }
        if (this.busy) return;
        const f=new FormData(target);
        const values={level:f.get('level'), challenge:f.get('challenge'), minutes:Number(f.get('minutes')), speed:Number(f.get('speed')), interests:f.getAll('interests'), adaptive:f.has('adaptive'), exclude_read:f.has('exclude_read')};
        return this.mutate(async()=> {
          this.profile=await this.request('/profile','PATCH',{...values, version:this.profile.version});
          if(this.dead) return;
          this.root.querySelector('#personal-profile-status').textContent='已保存';
          this.root.querySelector('#personal-filters [name="include_read"]').checked=!values.exclude_read;
          await this.refresh();
        });
      }
      if(event.type!=='click' || !action) return;
      const button=target.closest('[data-personal]');
      if(action==='reload') return this.render();
      if(action==='refresh') return this.refresh();
      if(action==='recommendations' || action==='feedback') {
        this.mode=action;
        this.root.querySelectorAll('.personal-tabs button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.personal===action)));
        if(action==='feedback') this.root.querySelector('#personal-filters [name="include_read"]').checked=true;
        return this.refresh();
      }
      if(this.busy) return;
      if(action==='reset') return this.mutate(async()=> {
        this.profile=await this.request('/profile','DELETE',{version:this.profile.version}); if(this.dead) return; this.showProfile();
        this.root.querySelector('#personal-filters [name="include_read"]').checked=false;
        this.toast('已恢复默认偏好，学习记录与反馈保留。'); await this.refresh();
      });
      if(['difficulty','interest','clear'].includes(action)) {
        const id=Number(button.closest('[data-personal-card]').dataset.personalCard), item=this.items.get(id);
        const change=action==='clear'?{difficulty:'',interest:'neutral'}:{[action]:button.dataset.value};
        return this.mutate(async()=> {
          await this.request('/articles/'+id+'/feedback','PUT',{...change,version:item.feedback.version,article_hash:item.analysis.hash});
          if(this.dead) return;
          this.toast(action==='clear'?'反馈已撤销。':'反馈已保存。'); await this.refresh();
        });
      }
    }
    async mutate(work) {
      this.busy=true;
      const controls=[...this.root.querySelectorAll('button, #personal-profile input, #personal-profile select')].filter(n=>!n.disabled);
      controls.forEach(n=>n.disabled=true);
      try { await work(); }
      catch(error) {
        if(this.dead) return;
        this.toast(error.message,true);
        this.root.querySelector('#personal-message').innerHTML=`${esc(error.message)} ${error.status===409?btn('reload','重新载入'):''}`;
      } finally {this.busy=false; controls.forEach(n=>{if(n.isConnected)n.disabled=false;});}
    }
  }
  window.MarginPersonalization=Personalization;
})();
