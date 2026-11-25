'use strict';
(()=>{
 let recall=null,task=null,pack=null;
 const selectedArticle=()=>Number(document.getElementById('learning-article').value);
 const q=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const link=(article,index)=>'<a href="/#reader/'+article+'/'+index+'">定位原文第 '+(index+1)+' 段</a>';
 document.addEventListener('click',event=>{
  const b=event.target.closest('button');if(!b||!b.id.startsWith('coach-')&&!b.dataset.coach)return;
  (async()=>{b.disabled=true;try{
   if(b.id==='coach-recall'){
    const id=selectedArticle(),meta=await call('/learning-coach/articles/'+id);
    const r=await call('/learning-coach/recall','POST',{article_id:id,article_hash:meta.article_hash,answer:q('#coach-answer').value});recall=r.result;
    q('#coach-recall-result').innerHTML='<p>'+esc(recall.note)+(r.cached?' · 已读取缓存':'')+'</p>'+recall.points.map((p,i)=>'<section><h4>要点 '+(i+1)+' · '+({covered:'已覆盖',missing:'待补充',incorrect:'需修正',uncertain:'需核对'}[p.status])+'</h4><blockquote>'+esc(p.source_quote)+'</blockquote>'+link(id,p.paragraph_index)+'<p>'+esc(p.explanation)+'</p>'+(p.answer_quote?'<p>回答依据：'+esc(p.answer_quote)+'</p>':'')+(p.status!=='covered'?'<label>'+esc(p.exercise||'结合原文重新解释这一点。')+'<textarea data-coach-followup="'+i+'" maxlength="2000"></textarea></label>':'')+'</section>').join('')+(recall.points.some(p=>p.status!=='covered')?'<button type="button" data-coach="followup">保存补练回答</button>':'<p>三个候选要点均已覆盖，仍可回原文复核。</p>');
   }else if(b.dataset.coach==='followup'){
    const answers=Object.fromEntries([...document.querySelectorAll('[data-coach-followup]')].map(el=>[el.dataset.coachFollowup,el.value]));
    recall=(await call('/learning-coach/recall/'+recall.id+'/followup','POST',{version:recall.version,answers})).result;notice('补练回答已保存，回看原文核对后可重新复述。');
   }else if(b.id==='coach-task'){
    const r=await call('/learning-coach/diagnosis-task','POST',{});task=r.task;if(!task){q('#coach-task-result').textContent=r.note;return;}
    const question=task.question;q('#coach-task-result').innerHTML='<p>'+esc(task.note)+'</p><p>'+esc(question.prompt)+'</p>'+(question.options.length&&question.type!=='order'?question.options.map((o,i)=>'<label class="inline"><input type="radio" name="coach-task-answer" value="'+i+'">'+esc(o)+'</label>').join(''):'<label>回答（多个空或排序编号用竖线 | 分隔）<input id="coach-task-answer" maxlength="6000"></label>')+'<button type="button" data-coach="task-answer">提交并核对</button><div id="coach-task-feedback"></div>';
   }else if(b.dataset.coach==='task-answer'){
    const answer=q('[name=coach-task-answer]:checked')?.value??q('#coach-task-answer')?.value??'';
    const r=(await call('/learning-coach/diagnosis-task/'+task.id,'POST',{answer:answer.split('|').map(s=>s.trim())})).result;
    q('#coach-task-feedback').innerHTML='<p>'+({true:'回答正确',false:'需要再练',null:'请结合原句自评'}[String(r.correct)])+'</p><p>参考答案：'+esc(r.answer.join(' | '))+'</p><p>'+esc(r.explanation)+'</p><blockquote>'+esc(r.evidence)+'</blockquote>';
   }else if(b.id==='coach-pack'){
    const r=await call('/learning-coach/pack-suggestion');pack=r.suggestion;
    q('#coach-pack-result').innerHTML='<p>'+esc(r.note)+'</p>'+(pack?'<ol>'+pack.articles.map(a=>'<li>'+esc(a.title)+' · '+esc(a.level)+'</li>').join('')+'</ol><p>共同目标词：'+esc(pack.words.join(', '))+'</p><button type="button" data-coach="pack-create">确认创建任务包</button>':'');
   }else if(b.dataset.coach==='pack-create'){
    const {articles,...payload}=pack;await call('/learning-lab/packs','POST',payload);q('#coach-pack-result').innerHTML='<p>已创建。<a href="/#lab">进入拓展学习按顺序完成任务</a></p>';
   }else if(b.id==='coach-expressions'){
    const r=await call('/learning-lab/records?kind=expression');q('#coach-expression-list').innerHTML=r.records.map(x=>'<p>'+esc(x.payload.topic)+' <button type="button" data-coach="expression" data-id="'+x.id+'">AI 核对</button></p>').join('')||'<p>尚无表达迁移记录。<a href="/#lab">去完成一次练习</a></p>';
   }else if(b.dataset.coach==='expression'){
    const r=(await call('/learning-coach/expression/'+b.dataset.id,'POST',{})).result;
    q('#coach-expression-result').innerHTML='<p>'+esc(r.note)+'</p>'+r.expressions.map(x=>'<section><h4>'+({appropriate:'用法合适',incorrect:'需修正',uncertain:'需核对',absent:'未找到使用'}[x.judgment])+'</h4><blockquote>'+esc(x.source_quote)+'</blockquote><p>写作依据：'+esc(x.answer_quote||'未找到')+'</p><p>'+esc(x.explanation)+'</p><p>'+esc(x.suggestion)+'</p></section>').join('');
   }
  }catch(error){notice(error.message)}finally{b.disabled=false}})();
 });
})();
