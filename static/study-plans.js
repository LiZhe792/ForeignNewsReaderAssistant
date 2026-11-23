/* Local study plans, goal progress and visible-page reminders. */
'use strict';
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
  const categories = ['科技', '文化', '科学', '生活', '环境', '其他'];
  const label = {seconds:'阅读', articles:'完成文章', words:'新增词条', reviews:'复习'};
  function amount(key, value) {
    if (key === 'seconds') return value < 60 ? value + ' 秒' : (Math.round(value / 6) / 10) + ' 分钟';
    return value + {articles:' 篇', words:' 条', reviews:' 次'}[key];
  }
  function dayAt(value, offset = 0) {
    const result = new Date(value + 'T12:00:00Z');
    result.setUTCDate(result.getUTCDate() + offset);
    return result.toISOString().slice(0, 10);
  }
  function monday(value) {
    const day = new Date(value + 'T12:00:00Z').getUTCDay();
    return dayAt(value, -((day + 6) % 7));
  }
  function btn(action, text, attrs = '') {
    return `<button type="button" class="button small" data-plan="${action}" ${attrs}>${text}</button>`;
  }
  function gauge(title, received, goal, format) {
    if (!goal) return `<div class="plan-gauge"><span>${title}</span><strong>${format(received)}</strong><small>未设目标</small></div>`;
    const percent = Math.min(received / goal, 1);
    return `<div class="plan-gauge"><span>${title}</span><strong>${format(received)} <small>/ ${format(goal)}</small></strong><progress value="${percent}" max="1" aria-label="${title}进度"></progress><small>${received >= goal ? '目标达成' : '还差 ' + format(goal - received)}</small></div>`;
  }

  class StudyPlans {
    constructor({root, api, toast, confirm, refreshStats, reminders, onOverview}) {
      Object.assign(this, {root, api, toast, confirm, refreshStats, reminders, onOverview});
      this.reflections = new Map();
      this.controller = new AbortController();
      this.selection = 0;
      for (const type of ['click', 'change', 'submit', 'input']) {
        root.addEventListener(type, event => this.handle(event), {signal:this.controller.signal});
      }
    }
    destroy() {
      this.dead = true;
      this.controller.abort();
    }
    request(path = '', method = 'GET', body) {
      return this.api('/planning' + path, {method, body, signal:this.controller.signal});
    }
    async render() {
      this.root.innerHTML = `<div class="page-wrap plan-page">
        <div class="page-intro"><div><h1>学习计划</h1><p>设置每天的目标，安排本周和专题学习，回顾每一阶段的进展。</p></div><a class="button small" href="#activity">查看打卡日历</a></div>
        <p id="plan-status" role="status"></p>
        <div id="plan-daily"></div>
        <section class="plan-panel">
          <div class="plan-heading"><h2>周计划与专题计划</h2><div>${btn('new-week','新建周计划')} ${btn('new-topic','新建专题计划')}</div></div>
          <div id="plan-list"></div><div id="plan-detail"></div>
        </section>
        <div class="plan-columns"><div id="plan-streak"></div><div id="plan-achievements"></div></div>
      </div>`;
      await this.reload();
    }
    status(text, error = false) {
      const node = this.root.querySelector('#plan-status');
      if (!node) return;
      node.textContent = text;
      node.classList.toggle('plan-error', error);
    }
    async reload() {
      const data = await this.request();
      if (this.dead) return;
      this.data = data;
      this.onOverview(data);
      this.drawDaily();
      this.drawList();
      this.drawStreak();
    }
    drawDaily() {
      const {today, goals, progress} = this.data;
      this.root.querySelector('#plan-daily').innerHTML = `<section class="plan-panel">
        <div class="plan-heading"><h2>今天的目标</h2><span>${today} · 北京时间</span></div>
        <div class="plan-goals">${gauge('阅读', progress.seconds, goals.reading_minutes * 60, v=>amount('seconds',v))}
          ${gauge('新增词条', progress.words, goals.new_words, v=>amount('words',v))}</div>
        <p class="plan-note">新增按实际保存的新词条计数，含批量导入。修改已有释义或重复收藏原句不增加条数。</p>
        <details class="plan-settings"><summary>设置目标与提醒</summary>
          <form id="plan-goals-form">
            <div class="plan-fields">
              <label>每日阅读（分钟）<input type="number" name="reading_minutes" min="0" max="720" value="${goals.reading_minutes}" required></label>
              <label>每日新增词条<input type="number" name="new_words" min="0" max="500" value="${goals.new_words}" required></label>
              <label>每日阅读提醒<input type="time" name="reading_reminder" value="${goals.reading_reminder}"></label>
              <label>到期复习提醒<input type="time" name="review_reminder" value="${goals.review_reminder}"></label>
            </div>
            <p class="plan-note">目标填 0 可停用。提醒留空可关闭，按北京时间每天最多显示一次，请保持应用页面打开。到点时还有目标未达成或有到期复习才提醒。</p>
            <button type="submit" class="button primary small">保存目标与提醒</button>
          </form>
        </details></section>`;
    }
    drawList() {
      const {plans, today} = this.data;
      this.root.querySelector('#plan-list').innerHTML = plans.length
        ? `<ul class="plan-list">${plans.map(p => `<li><button type="button" data-plan="open" data-id="${p.id}" aria-pressed="${this.current?.id === p.id}">
            <strong>${escape(p.title)}</strong><span>${p.kind==='week'?'周计划':escape(p.category)} · ${p.start_day} 至 ${p.end_day}</span>
            <small>${today<p.start_day?'未开始':today>p.end_day?'已结束':'进行中'}</small>
          </button></li>`).join('')}</ul>`
        : '<p class="plan-note">还没有计划。可以安排一周的学习，也可以围绕一个文章分类制定专题计划。</p>';
    }
    drawStreak() {
      const {continuity, makeup_candidates, makeups, achievements} = this.data;
      this.root.querySelector('#plan-streak').innerHTML = `<section class="plan-panel">
        <h2>连续学习与补签</h2><dl class="plan-streak-values">
          <div><dt>当前连续</dt><dd>${continuity.current} 天</dd></div><div><dt>最长连续</dt><dd>${continuity.longest} 天</dd></div>
          <div><dt>实际打卡</dt><dd>${continuity.study_days} 天</dd></div><div><dt>补签</dt><dd>${continuity.makeups} 天</dd></div>
        </dl>
        <p class="plan-note">补签只用于延续打卡记录，可补过去 7 天内未打卡的日期。阅读时长、词汇、复习和成就仍按实际记录计算。</p>
        ${makeup_candidates.length ? `<form id="plan-makeup-form">
          <label>补签日期<select name="day" required><option value="">请选择日期</option>${makeup_candidates.map(d=>`<option>${d}</option>`).join('')}</select></label>
          <label>备注（选填）<input name="note" maxlength="200" placeholder="记录当天做了什么"></label>
          <button type="submit" class="button small">记录补签</button></form>`
          : '<p class="plan-note">近 7 天没有可补签的日期。</p>'}
        ${makeups.length ? `<ul class="plan-makeups">${makeups.map(m=>`<li><time>${m.day}</time> · 补签${m.note?' · '+escape(m.note):''}</li>`).join('')}</ul>` : ''}
      </section>`;
      this.root.querySelector('#plan-achievements').innerHTML = `<section class="plan-panel"><h2>学习成就</h2>
        <ul class="plan-achievements">${achievements.map(a=>`<li><div><strong>${escape(a.title)}</strong><span>${a.earned?'已达成':'进行中'}</span></div>
          <small>${a.actual} / ${a.target} ${escape(a.unit)}</small>
          <progress value="${Math.min(1,a.actual/a.target)}" max="1" aria-label="${escape(a.title)}进度"></progress></li>`).join('')}</ul>
        </section>`;
    }
    editor(kind, plan = null) {
      const start = kind === 'week' ? monday(this.data.today) : this.data.today;
      const draft = plan || {
        kind, title: kind === 'week' ? '本周学习计划' : '', category:'科技', start_day:start,
        end_day:dayAt(start,6), reading_minutes:60, articles:2, new_words:kind==='week'?15:0, steps:[],
      };
      this.editing = plan;
      const detail = this.root.querySelector('#plan-detail');
      detail.innerHTML = `<form id="plan-editor" class="plan-editor" data-kind="${kind}">
        <h3>${plan?'编辑': '新建'}${kind==='week'?'周计划':'专题计划'}</h3>
        <label>计划名称<input name="title" required maxlength="80" value="${escape(draft.title)}"></label>
        <div class="plan-fields">
          <label>${kind==='week'?'选择本周任意一天':'开始日期'}<input type="date" name="start_day" min="1901-01-01" max="9998-12-31" value="${draft.start_day}" required></label>
          ${kind==='topic'?`<label>结束日期<input type="date" name="end_day" min="1901-01-01" max="9998-12-31" value="${draft.end_day}" required></label>
          <label>专题分类<select name="category">${categories.map(c=>`<option ${draft.category===c?'selected':''}>${c}</option>`).join('')}</select></label>`:''}
          <label>阅读目标（分钟）<input type="number" name="reading_minutes" min="0" max="14400" value="${draft.reading_minutes}" required></label>
          <label>完成文章（篇）<input type="number" name="articles" min="0" max="500" value="${draft.articles}" required></label>
          ${kind==='week'?`<label>新增词条（条）<input type="number" name="new_words" min="0" max="5000" value="${draft.new_words}" required></label>`:''}
        </div>
        <label>拆分为可勾选的步骤<textarea name="steps" rows="4" maxlength="1940" placeholder="每行一个步骤，例如：阅读一篇环境报道并写下三条要点">${escape(draft.steps.map(s=>s.text).join('\n'))}</textarea></label>
        <p class="plan-note">${kind==='week'?'周计划按所选日期所在的周一至周日统计。':'专题计划统计选定分类的阅读时长和完成篇数。词汇任务可写入步骤。'} 单项目标可填 0，最多 12 个步骤。</p>
        <button type="submit" class="button primary small">保存计划</button> ${btn('cancel','取消')}
      </form>`;
      detail.querySelector('[name=title]').focus({preventScroll:true});
    }
    show(plan) {
      this.current = plan;
      this.drawList();
      const rows = plan.days.map(d => `<tr><th scope="row">${d.day}</th>${Object.keys(plan.targets).filter(k=>plan.targets[k]).map(k=>
        `<td>${d.day>this.data.today?'未到日期':amount(k,d.actual?.[k]||0)} / ${amount(k,d.target[k])}</td>`).join('')}</tr>`).join('');
      const detail = this.root.querySelector('#plan-detail');
      detail.innerHTML = `<article class="plan-detail">
        <div class="plan-heading"><h3>${escape(plan.title)}</h3><div>${btn('edit','编辑计划')} ${btn('delete','删除计划')}</div></div>
        <p class="plan-note">${plan.start_day} 至 ${plan.end_day}${plan.category?' · '+escape(plan.category):''} · ${plan.completed?'已达成':{active:'进行中',upcoming:'未开始',ended:'已结束'}[plan.status]}</p>
        ${plan.category?`<p><a class="read-link" href="#library/${encodeURIComponent(plan.category)}">浏览${escape(plan.category)}文章 →</a></p>`:''}
        <div class="plan-goals">${Object.keys(plan.targets).filter(k=>plan.targets[k]).map(k=>gauge(label[k],plan.totals[k],plan.targets[k],v=>amount(k,v))).join('')}</div>
        ${plan.steps.length?`<fieldset class="plan-steps"><legend>计划步骤 · ${plan.steps_done} / ${plan.steps.length}</legend>${plan.steps.map((step,i)=>`<label><input type="checkbox" data-step="${i}" ${step.done?'checked':''}>${escape(step.text)}</label>`).join('')}</fieldset>`:''}
        ${Object.values(plan.targets).some(Boolean)?`<details class="plan-split"><summary>查看每日目标拆分</summary>
          <p class="plan-note">将目标均匀分摊到计划日期。当天未完成可在之后补做，达成按整个计划累计。</p>
          <div class="plan-table-wrap"><table><thead><tr><th scope="col">日期</th>${Object.keys(plan.targets).filter(k=>plan.targets[k]).map(k=>`<th scope="col">${label[k]}（实际 / 目标）</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>
          </details>`:''}
        <section class="plan-summary"><h4>阶段总结</h4>
          <p>本阶段阅读 ${amount('seconds',plan.totals.seconds)}，完成 ${plan.totals.articles} 篇文章。${plan.kind==='week'?`新增 ${plan.totals.words} 条词条，复习 ${plan.totals.reviews} 次（含新词练习）。`:''}
            ${plan.steps.length?`完成 ${plan.steps_done} / ${plan.steps.length} 个步骤。`:''}</p>
          ${plan.category?'<p class="plan-note">专题只统计可关联到该分类的阅读记录。升级前只有每日汇总的时长无法分配到专题。</p>':''}
          <form id="plan-reflection"><label>阶段笔记<textarea name="reflection" maxlength="2000" rows="3" placeholder="记下收获、遇到的困难和下一步">${escape(this.reflections.get(plan.id) ?? plan.reflection)}</textarea></label><button type="submit" class="button small">保存阶段笔记</button></form>
        </section></article>`;
    }
    async open(identity) {
      const sequence = ++this.selection;
      const {plan} = await this.request('/plans/' + identity);
      if (!this.dead && sequence === this.selection) this.show(plan);
    }
    async change(data) {
      const current = this.current;
      const {plan} = await this.request('/plans/' + current.id, 'PATCH', {...data,version:current.version});
      if (!this.dead) {
        if (Object.hasOwn(data,'reflection')) this.reflections.delete(current.id);
        this.show(plan);
      }
    }
    async handle(event) {
      const target = event.target;
      if (event.type==='input' && target.matches('#plan-reflection textarea')) {
        this.reflections.set(this.current.id, target.value);
      }
      if (event.type==='submit' && ['plan-goals-form','plan-editor','plan-reflection','plan-makeup-form'].includes(target.id)) {
        event.preventDefault();
        event.stopPropagation();
        if (this.busy) return;
        const values = Object.fromEntries(new FormData(target));
        return this.run(async () => {
          if (target.id==='plan-goals-form') {
            const {goals} = await this.request('/goals','PATCH',{
              version:this.data.goals.version, reading_minutes:Number(values.reading_minutes),
              new_words:Number(values.new_words), reading_reminder:values.reading_reminder, review_reminder:values.review_reminder,
            });
            if (this.dead) return;
            this.data.goals = goals;
            this.reminders?.check();
            await this.refreshStats();
            await this.reload();
            this.status('目标与提醒已保存。');
          } else if (target.id==='plan-editor') {
            const prior = this.editing;
            const kind = target.dataset.kind;
            const start = kind==='week'?monday(values.start_day):values.start_day;
            const data = {
              kind, title:values.title, category:kind==='week'?'':values.category,
              start_day:start, end_day:kind==='week'?dayAt(start,6):values.end_day,
              reading_minutes:Number(values.reading_minutes), articles:Number(values.articles), new_words:Number(values.new_words||0),
              steps:values.steps.split('\n').map(s=>s.trim()).filter(Boolean).map(text=>({text,done:prior?.steps.find(s=>s.text===text)?.done||false})),
            };
            const {plan} = prior
              ? await this.request('/plans/'+prior.id,'PATCH',{...data,version:prior.version})
              : await this.request('/plans','POST',data);
            if (this.dead) return;
            await this.reload();
            if (!this.dead) this.show(plan);
            this.status('计划已保存。');
          } else if (target.id==='plan-makeup-form') {
            await this.request('/makeups','POST',values);
            if (this.dead) return;
            await this.refreshStats();
            await this.reload();
            this.status('补签已记录。');
          } else {
            await this.change({reflection:values.reflection});
            this.status('阶段笔记已保存。');
          }
        });
      }
      if (event.type==='change' && target.matches('[data-step]')) {
        if (this.busy) return;
        const index = Number(target.dataset.step);
        const original = this.current.steps[index].done;
        const steps = this.current.steps.map((step,i)=>i===index?{...step,done:target.checked}:step);
        return this.run(async()=>{
          try { await this.change({steps}); }
          catch(error) { target.checked=original; throw error; }
        });
      }
      if (event.type!=='click' || this.busy) return;
      const button = target.closest('[data-plan]');
      if (!button) return;
      const action = button.dataset.plan;
      if (action==='new-week'||action==='new-topic') {
        this.selection++;
        this.editor(action==='new-week'?'week':'topic');
      } else if (action==='open') {
        return this.run(()=>this.open(Number(button.dataset.id)));
      } else if (action==='edit') {
        this.editor(this.current.kind,this.current);
      } else if (action==='cancel') {
        if (this.current) this.show(this.current);
        else this.root.querySelector('#plan-detail').replaceChildren();
      } else if (action==='delete') {
        const current = this.current;
        if (!await this.confirm(`删除计划「${current.title}」及其中的阶段笔记？已记录的阅读与词汇会保留。`) || this.dead) return;
        return this.run(async()=>{
          await this.request('/plans/'+current.id,'DELETE',{version:current.version});
          if (this.dead) return;
          this.reflections.delete(current.id);
          this.current=null;
          this.root.querySelector('#plan-detail').replaceChildren();
          await this.reload();
          this.status('计划已删除。');
        });
      }
    }
    async run(action) {
      this.busy = true;
      const controls = [...this.root.querySelectorAll('form input, form textarea, form select, button, [data-step]')].filter(n=>!n.disabled);
      controls.forEach(n=>n.disabled=true);
      this.status('');
      try { await action(); }
      catch(error) {
        if (!this.dead) {
          this.status(error.message,true);
          this.toast(error.message,true);
        }
      } finally {
        controls.forEach(n=>n.disabled=false);
        this.busy=false;
      }
    }
  }

  class Reminders {
    constructor(api) {
      this.api = api;
      this.box = document.querySelector('#study-reminders');
      document.addEventListener('visibilitychange', ()=>this.check());
      window.addEventListener('focus', ()=>this.check());
      this.box.addEventListener('click', event => {
        if (event.target.closest('button, a')) {
          event.target.closest('li').remove();
          this.box.hidden = !this.box.querySelector('li');
        }
      });
      setInterval(()=>this.check(), 45000);
      this.check();
    }
    async check() {
      if (document.hidden || this.pending) return;
      this.pending = true;
      try {
        const {reminders} = await this.api('/planning/reminders/claim',{method:'POST',body:{}});
        for (const item of reminders) {
          const review = item.kind==='review';
          this.box.querySelector('ul').insertAdjacentHTML('beforeend',`<li>
            <span>${review?`有 ${item.due} 张卡片到期。`:'今天的阅读或词汇目标还未达成。'}</span>
            <a href="#${review?'review':'plans'}">${review?'去复习':'查看今日目标'}</a>
            <button type="button" class="button small" aria-label="关闭这条提醒">关闭</button></li>`);
        }
        this.box.hidden = !this.box.querySelector('li');
      } catch {
        // The next visible check retries after a connection failure.
      } finally {
        this.pending = false;
      }
    }
  }
  window.MarginStudyPlans = StudyPlans;
  window.MarginStudyReminders = Reminders;
})();
