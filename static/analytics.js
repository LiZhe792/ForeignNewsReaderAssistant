/* Charts stay local and include exact tables. Every date is a China calendar date. */
'use strict';
(() => {
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=v=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:1}).format(v);
  const rate=v=>v===null?'暂无样本':number(v)+'%';
  const minutes=v=>number(v/60);
  const action=(id,label,extra='')=>`<button type="button" class="button small" data-analytics="${id}" ${extra}>${label}</button>`;
  const token=()=>window.crypto?.randomUUID?.() || Date.now().toString(36)+'_'+Math.random().toString(36).slice(2);
  class LearningEvents {
    constructor({api,toast}) {
      Object.assign(this,{api,toast});this.pending=[];
      try {const saved=JSON.parse(sessionStorage.getItem('margin.lookup-outbox.v22')||'[]');if(Array.isArray(saved))this.pending=saved.filter(p=>p && typeof p.word==='string' && typeof p.request_id==='string').slice(-500);}catch{}
      window.addEventListener('online',()=>this.flush());this.flush();
    }
    persist(){try{sessionStorage.setItem('margin.lookup-outbox.v22',JSON.stringify(this.pending));}catch{}}
    observe(word,article,paragraph=-1){
      if(!article?.content_hash)return;
      this.pending.push({request_id:token(),word,article_id:article.id,article_hash:article.content_hash,paragraph_index:paragraph,occurred_at:new Date().toISOString()});
      if(this.pending.length>500){this.pending.shift();if(!this.warned){this.warned=true;this.toast('待保存查词记录已达 500 条，最早记录已移出。',true);}}
      this.persist();this.flush();
    }
    async flush(){
      if(this.saving)return this.saving;
      this.saving=(async()=>{
        while(this.pending.length){
          const item=this.pending[0];
          try{await this.api('/analytics/lookup',{method:'POST',body:item});}
          catch(error){if(![400,404,409].includes(error.status))break;}
          this.pending.shift();this.persist();
        }
      })().finally(()=>{this.saving=null;});return this.saving;
    }
  }
  class Analytics {
    constructor({root,api,toast,tracker}){Object.assign(this,{root,api,toast,tracker});this.abort=new AbortController();this.seq=0;this.period='week';this.anchor=new Intl.DateTimeFormat('en-CA',{timeZone:window.MarginPlatform?.preferences?.timezone||'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());this.readMetric='seconds';this.wordMetric='vocabulary_total';
      for(const type of ['click','change','submit','keydown'])root.addEventListener(type,e=>this.handle(e),{signal:this.abort.signal});
    }
    destroy(){this.dead=true;this.seq++;this.abort.abort();}
    async render(){
      this.root.innerHTML=`<div class="page-wrap analytics-page"><div class="page-intro"><div><span class="eyebrow">MAKE YOUR PROGRESS VISIBLE</span><h1>学习数据分析</h1><p>看看读了多少、记住多少，也找到下一步值得花时间的地方。</p></div></div><form id="analytics-controls" class="analytics-controls"><fieldset><legend>报告周期</legend>${['week','month','year'].map((p,i)=>action('period',['周报','月报','年度回顾'][i],`data-period="${p}" aria-pressed="${p===this.period}"`)).join('')}</fieldset><label>参考日期<input name="date" type="date" min="1901-01-01" max="9998-12-31" value="${this.anchor}" required></label><div class="analytics-date-nav">${action('previous','← 上一期')}${action('next','下一期 →')}<button class="button small primary" type="submit">查看报告</button></div></form><p id="analytics-status" role="status"></p><div id="analytics-content"><div class="loading-screen"><span class="spinner"></span><p>正在整理学习记录…</p></div></div></div>`;
      await this.load();
    }
    async load(){
      const seq=++this.seq;this.root.querySelector('#analytics-status').textContent='正在读取报告…';
      try{
        const data=await this.api('/analytics/report?'+new URLSearchParams({period:this.period,date:this.anchor}),{signal:this.abort.signal});
        if(this.dead||seq!==this.seq)return;this.data=data;this.paint();this.root.querySelector('#analytics-status').textContent=`已更新：${data.start} — ${data.end} · 日期采用北京时间`;
      }catch(error){if(!this.dead&&seq===this.seq)this.root.querySelector('#analytics-status').innerHTML=`${esc(error.message)}${this.data?' 以下保留上一份报告，请以报告日期为准。':''} ${action('retry','重试读取')}`;}
    }
    chart(series,key,label,format,unit){
      const values=series.map(s=>key==='lookups'&&!s.lookup_observed?null:key==='seconds'?s[key]/60:s[key]),present=values.filter(v=>v!==null),maximum=Math.max(1,...present),w=820,h=210,left=48,bottom=175,step=(w-left-20)/Math.max(1,series.length),width=Math.max(3,Math.min(38,step*.65));
      const bars=series.map((s,i)=>{
        const value=values[i],x=left+step*(i+.5),y=value===null?bottom:bottom-(value/maximum)*135;
        return `${value===null?`<line x1="${x-3}" y1="${bottom-3}" x2="${x+3}" y2="${bottom-3}" stroke="#8a968c"/>`:`<rect x="${x-width/2}" y="${y}" width="${width}" height="${Math.max(1,bottom-y)}" rx="2" fill="#48735b"/>`}<g class="analytics-point" role="button" tabindex="0" data-analytics-point="${esc(s.label)}" aria-label="${esc(s.label+'，'+label+'：'+(value===null?'暂无样本':format(s[key])))}"><title>${esc(s.label)} · ${value===null?'暂无样本':esc(format(s[key]))}</title><rect x="${x-step/2}" y="25" width="${step}" height="150" fill="transparent"/></g>${series.length<=12||i%Math.ceil(series.length/7)===0?`<text x="${x}" y="197" text-anchor="middle">${esc(s.label.slice(this.period==='year'?5:5))}</text>`:''}`;
      }).join('');
      return `<div class="analytics-chart"><svg viewBox="0 0 ${w} ${h}" role="group" aria-label="${esc(label+'趋势（'+unit+'）')}" focusable="false"><text x="3" y="30">${number(maximum)}</text><text x="23" y="179">0</text><path d="M${left} 38V${bottom}H800" fill="none" stroke="#dce3db"/>${bars}</svg></div><details class="analytics-table-details"><summary>查看${esc(label)}数据表</summary><div class="analytics-table-scroll"><table><thead><tr><th scope="col">日期</th><th scope="col">${esc(label)}</th></tr></thead><tbody>${series.map(s=>`<tr><th scope="row">${esc(s.label)}</th><td>${s[key]===null||(key==='lookups'&&!s.lookup_observed)?'暂无样本':esc(format(s[key]))}</td></tr>`).join('')}</tbody></table></div></details>`;
    }
    paint(){
      const d=this.data,t=d.totals;
      const metric=(title,value,note)=>`<div class="analytics-tile"><span>${title}</span><strong>${value}</strong><small>${note}</small></div>`;
      const names={week:'学习周报',month:'学习月报',year:'年度学习回顾'};
      const top=d.topics[0],has=t.seconds||t.completed||t.reviews||t.vocabulary_added||t.lookups;
      const summary=has?`这段时间活跃阅读 ${minutes(t.seconds)} 分钟，完成 ${t.completed} 篇文章，新增 ${t.vocabulary_added} 条词条。${t.reviews?`复习 ${t.reviews} 次，自评记住率 ${rate(t.remembered_rate)}。`:''}${top&&top.seconds?`有记录的阅读中，「${esc(top.category)}」投入时间最多。`:''}`:'这个周期还没有可统计的学习记录。读一篇文章、收藏词条或开始复习后，再回来看看。';
      const url='/api/analytics/export?'+new URLSearchParams({period:d.period,date:d.start});
      this.root.querySelector('#analytics-content').innerHTML=`<section class="analytics-summary"><div class="analytics-section-heading"><div><span class="eyebrow">${d.start} — ${d.end}</span><h2>${names[d.period]}</h2></div><a class="button small" href="${esc(url)}" download>导出每日数据 CSV ↓</a></div><p>${summary}</p><small>过程记录始于 ${esc(d.tracking_since.slice(0,10))}；早期阅读时长和完成篇数沿用已有记录。</small></section><div class="analytics-tiles">${metric('活跃阅读',minutes(t.seconds)+'<em>分钟</em>',t.active_reading_days+' 个阅读日')}${metric('完成阅读',t.completed+'<em>篇</em>',number(t.completed_words)+' 词 · 可追溯正文')}${metric('新增词条',t.vocabulary_added+'<em>条</em>','移除 '+t.vocabulary_removed+' 条 · 净增 '+t.vocabulary_net)}${metric('主动查词（已采集）',d.lookup_coverage==='none'?'未采集':t.lookups+'<em>次</em>',d.lookup_coverage==='partial'?'本期含未采集日期 · 仅汇总已有记录':'含未查到的查询 · 不含待查暂存')}</div>
        <section class="analytics-panel"><div class="analytics-section-heading"><h2>阅读与查词趋势</h2><label>阅读指标<select id="analytics-read-metric">${[['seconds','阅读时长'],['completed','完成篇数'],['completed_words','可追溯完成词数'],['speed_wpm','首读推进速度（估算）'],['lookups','主动查词次数']].map(([value,label])=>`<option value="${value}" ${value===this.readMetric?'selected':''}>${label}</option>`).join('')}</select></label></div><div id="analytics-reading-chart"></div><p class="analytics-note">速度只统计有进度推进且活跃计时至少 10 秒的保存段，手动标记完成不参与；无样本处显示短横线，详情见统计口径。</p><p id="analytics-point-detail" class="analytics-point-detail" role="status">点击或用键盘选择图中日期，查看对应记录。</p></section>
        <div class="analytics-two"><section class="analytics-panel"><div class="analytics-section-heading"><h2>词汇增长</h2><label>词汇指标<select id="analytics-word-metric">${[['vocabulary_total','词库累计'],['vocabulary_added','新增词条'],['vocabulary_removed','移除词条']].map(([value,label])=>`<option value="${value}" ${value===this.wordMetric?'selected':''}>${label}</option>`).join('')}</select></label></div><div id="analytics-word-chart"></div><p class="analytics-note">新增包括导入与短语、句型；合并或删除计入移除，不等于遗忘。</p></section><section class="analytics-panel"><h2>当前掌握度分布</h2><p class="analytics-note">当前词库快照，非所选周期末的历史状态。共 ${d.vocabulary_current} 条。</p><div class="analytics-mastery" role="img" aria-label="${esc('初次遇见 '+d.mastery.new+'，正在学习 '+d.mastery.learning+'，已掌握 '+d.mastery.mastered)}">${Object.entries(d.mastery).map(([key,value])=>`<span class="mastery-${key}" style="width:${d.vocabulary_current?value/d.vocabulary_current*100:0}%"></span>`).join('')}</div><dl class="analytics-distribution">${[['new','初次遇见'],['learning','正在学习'],['mastered','已掌握']].map(([key,label])=>`<div><dt>${label}</dt><dd>${d.mastery[key]} 条 <small>${d.vocabulary_current?rate(d.mastery[key]/d.vocabulary_current*100):'暂无样本'}</small></dd></div>`).join('')}</dl><a class="read-link" href="#vocabulary">去整理词库 →</a></section></div>
        <div class="analytics-two"><section class="analytics-panel"><h2>复习正确率与遗忘</h2><div class="analytics-review-grid">${metric('自评记住率',rate(t.remembered_rate),t.remembered+' / '+t.reviews+' 次评分')}${metric('拼写核对正确率',rate(t.spelling_rate),t.spelling_correct+' / '+t.spelling_total+' 次有效拼写')}${metric('遗忘',t.forgotten+'<em>次</em>','已学词再次未记住')}${metric('首学未记住',t.first_learning_misses+'<em>次</em>','单独统计，不算遗忘')}</div><p class="analytics-note">当前暂停 ${d.current_review.suspended} 词、困难 ${d.current_review.difficult} 词。自评与实际拼写核对分别统计，重复评分确认不重复计数。</p>${this.wordList(d.forgotten_words,'本期遗忘较多的词','次遗忘')}<a class="read-link" href="#review">安排下一轮复习 →</a></section><section class="analytics-panel"><h2>主题偏好</h2><p class="analytics-note">按本期可追溯的阅读时长排序；早期没有按文章采集的时长不会强行分配主题。</p>${d.topics.length?`<table class="analytics-topic-table"><thead><tr><th>主题</th><th>活跃分钟</th><th>完成篇数</th><th>查词</th></tr></thead><tbody>${d.topics.map(row=>`<tr><th scope="row">${esc(row.category)}</th><td>${minutes(row.seconds)}</td><td>${row.completed}</td><td>${row.lookups}</td></tr>`).join('')}</tbody></table>`:'<p class="analytics-empty">暂无主题活动记录。</p>'}</section></div>
        <div class="analytics-two"><section class="analytics-panel"><h2>查词频率</h2>${this.wordList(d.top_lookups,'本期主动查询','次查询')}<p class="analytics-note">同一次请求重试只记一次；主动切换词典会产生新的查询。</p></section><section class="analytics-panel"><h2>反复遇见的词</h2>${d.repeated_words.length?`<ul class="analytics-word-list">${d.repeated_words.map(w=>`<li><strong lang="en">${esc(w.word)}</strong><span>${w.count} 次 · ${w.articles} 篇正文 · ${w.days} 天</span></li>`).join('')}</ul>`:'<p class="analytics-empty">暂无重复遇见记录。在正文点词暂存或展开待查释义后记录。</p>'}<p class="analytics-note">同词、同一正文段落、同一天去重，跨段或跨日计为再次遇见。按实际拼写分组，不合并词形。</p></section></div>
        <section class="analytics-panel"><h2>文章生词率与阅读记录</h2><p class="analytics-note">已记生词率是当前词库中待掌握词在正文中的词次占比；没有收藏的词不直接视为生词。文章改动或删除后保留活动快照，旧正文不计算当前生词率。显示前 ${d.articles.length} / ${d.article_count} 条正文记录。</p><div class="analytics-table-scroll"><table id="analytics-articles"><thead><tr><th>文章</th><th>本期活跃分钟</th><th>本期查词</th><th>当前已记生词率</th></tr></thead><tbody>${d.articles.map(a=>`<tr><th scope="row">${a.current?`<a href="#reader/${a.id}">${esc(a.title)}</a>`:esc(a.title)}<small>${esc(a.category)}${a.current?'':' · 正文已改动或删除'}</small></th><td>${minutes(a.seconds)}</td><td>${d.lookup_coverage==='none'?'未采集':a.lookups}</td><td>${a.unmastered_rate===null?'无法计算':rate(a.unmastered_rate)}${a.word_count?`<small>${a.recorded_words} / ${a.word_count} 词次</small>`:''}</td></tr>`).join('')||'<tr><td colspan="4">暂无文章记录。</td></tr>'}</tbody></table></div></section>
        <details class="analytics-panel analytics-notes"><summary>统计口径与历史数据范围</summary><ul>${d.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul><p id="analytics-pending">待保存查词记录 ${this.tracker?.pending.length||0} 条 ${action('save-events','重试保存')}</p></details>`;
      this.paintCharts();
    }
    wordList(items,title,unit){return `<h3>${title}</h3>${items.length?`<ul class="analytics-word-list">${items.map(w=>`<li><strong lang="en">${esc(w.word)}</strong><span>${w.count} ${unit}</span></li>`).join('')}</ul>`:'<p class="analytics-empty">暂无记录。</p>'}`;}
    paintCharts(){
      const names={seconds:'阅读时长',completed:'完成篇数',completed_words:'可追溯完成词数',speed_wpm:'首读推进速度',lookups:'主动查词次数',vocabulary_total:'词库累计',vocabulary_added:'新增词条',vocabulary_removed:'移除词条'};
      const key=this.readMetric,format=key==='seconds'?v=>minutes(v)+' 分钟':key==='speed_wpm'?v=>number(v)+' 词/分钟':v=>number(v)+(key==='completed'?' 篇':key==='lookups'?' 次':' 词');
      this.root.querySelector('#analytics-reading-chart').innerHTML=this.chart(this.data.series,key,names[key],format,key==='seconds'?'分钟':key==='speed_wpm'?'词/分钟':key==='completed'?'篇':key==='lookups'?'次':'词');
      this.root.querySelector('#analytics-word-chart').innerHTML=this.chart(this.data.series,this.wordMetric,names[this.wordMetric],v=>number(v)+' 条','条');
    }
    point(label){const row=this.data.series.find(r=>r.label===label);if(row)this.root.querySelector('#analytics-point-detail').textContent=`${label} · 活跃阅读 ${minutes(row.seconds)} 分钟 · 完成 ${row.completed} 篇 · 新增 ${row.vocabulary_added} 条 · ${row.lookup_observed?'查词 '+row.lookups+' 次':'查词未采集'} · 自评记住率 ${rate(row.remembered_rate)}`;}
    async handle(event){
      const target=event.target;
      if(event.type==='keydown'){if(['Enter',' '].includes(event.key)&&target.closest('[data-analytics-point]')){event.preventDefault();this.point(target.closest('[data-analytics-point]').dataset.analyticsPoint);}return;}
      if(event.type==='submit'&&target.id==='analytics-controls'){event.preventDefault();event.stopPropagation();if(!target.reportValidity())return;this.anchor=target.elements.date.value;return this.load();}
      if(event.type==='change'){
        if(target.id==='analytics-read-metric'){this.readMetric=target.value;this.paintCharts();}
        if(target.id==='analytics-word-metric'){this.wordMetric=target.value;this.paintCharts();}
        return;
      }
      if(event.type!=='click')return;
      const point=target.closest('[data-analytics-point]');if(point){this.point(point.dataset.analyticsPoint);return;}
      const button=target.closest('[data-analytics]'),id=button?.dataset.analytics;
      if(id==='retry')return this.load();
      if(id==='period'){this.period=button.dataset.period;this.anchor=this.root.querySelector('[name=date]').value||this.anchor;this.root.querySelectorAll('[data-analytics=period]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));return this.load();}
      if(id==='previous'||id==='next'){
        const step=id==='previous'?-1:1,base=new Date((this.root.querySelector('[name=date]').value||this.anchor)+'T12:00:00Z');
        if(this.period==='week')base.setUTCDate(base.getUTCDate()-(base.getUTCDay()+6)%7);else if(this.period==='month')base.setUTCDate(1);else base.setUTCMonth(0,1);
        if(this.period==='week')base.setUTCDate(base.getUTCDate()+step*7);else if(this.period==='month')base.setUTCMonth(base.getUTCMonth()+step);else base.setUTCFullYear(base.getUTCFullYear()+step);
        if(base.getUTCFullYear()<1901||base.getUTCFullYear()>9998)return;
        this.anchor=base.toISOString().slice(0,10);this.root.querySelector('[name=date]').value=this.anchor;return this.load();
      }
      if(id==='save-events'){button.disabled=true;await this.tracker?.flush();if(!this.dead){this.toast(this.tracker?.pending.length?'仍有记录待保存，请检查本地服务。':'查词记录已保存。');await this.load();}}
    }
  }
  window.MarginLearningEvents=LearningEvents;window.MarginAnalytics=Analytics;
})();
