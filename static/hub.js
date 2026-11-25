'use strict';
const $=s=>document.querySelector(s);
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let identity={scope:'guest'},catalog=[],groups=[],collections=[],assessment=null,sharedReadings=[];
const notice=message=>{$('#notice').textContent=message;};
function clearSession(){for(const key of Object.keys(sessionStorage))if(key.startsWith('margin'))sessionStorage.removeItem(key);}
async function call(path,method='GET',data){
 const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-Margin-Account':identity.scope},body:data===undefined?undefined:JSON.stringify(data)});
 const result=await response.json();
 if(!response.ok)throw Error(result.error||({invalid_credentials:'用户名或密码不正确。',username_taken:'用户名已使用。',invalid_invite:'邀请码无效。',rate_limited:'登录过于频繁，请稍后重试。'}[result.code])||result.code||'操作失败');
 return result;
}
const selected=form=>[...form.querySelector('[name=article_ids]').selectedOptions].map(o=>Number(o.value));
const busy=async(button,action)=>{button.disabled=true;try{await action()}catch(e){notice(e.message)}finally{button.disabled=false}};
async function refreshCollections(){
 collections=(await call('/platform/collections')).collections;
 $('#collection-list').innerHTML=collections.map(c=>'<article class="card"><h3>'+escape(c.title)+'</h3><p>'+escape(c.description)+'</p><p>'+c.article_ids.length+' 篇文章</p><button data-action="edit-collection" data-id="'+c.id+'">编辑</button><button data-action="delete-collection" data-id="'+c.id+'">删除合集</button><p>'+c.article_ids.map(id=>'<a href="/#reader/'+id+'">'+escape(catalog.find(a=>a.id===id)?.title||'原文章已删除')+'</a>').join(' · ')+'</p></article>').join('')||'<p>尚无合集。</p>';
}
async function refreshGroups(){
 if(!identity.account){$('#group-detail').textContent='登录后可创建或加入小组。';return;}
 const selectedId=$('#group-select').value;
 groups=(await call('/groups')).groups;
 $('#group-select').innerHTML='<option value="">请选择</option>'+groups.map(g=>'<option value="'+g.id+'">'+escape(g.title)+'</option>').join('');
 if(groups.some(g=>g.id===selectedId))$('#group-select').value=selectedId;
}
async function showGroup(){
 const id=$('#group-select').value;
 if(!id){$('#group-detail').replaceChildren();$('#shared-list').replaceChildren();return;}
 const data=await call('/groups/'+id),owner=data.group.role==='owner',teacher=['owner','teacher'].includes(data.group.role);
 $('#group-detail').innerHTML='<h3>'+escape(data.group.title)+'</h3><p>我的权限：'+escape(data.group.role)+'</p>'+(data.group.invite?'<p>邀请代码：<code>'+escape(data.group.invite)+'</code></p>':'')+(owner?'<button data-action="rotate-invite">更新邀请代码</button>':'')+'<ul>'+data.members.map(m=>'<li>'+escape(m.username)+' · '+escape(m.role)+(owner&&m.id!==identity.account.id?' <select aria-label="成员权限" data-role-for="'+m.id+'">'+['reader','editor','teacher','remove'].map(role=>'<option value="'+role+'" '+(m.role===role?'selected':'')+'>'+({reader:'只读/学习',editor:'编辑分享',teacher:'教师',remove:'移除'}[role])+'</option>').join('')+'</select><button data-action="role" data-id="'+m.id+'">更新权限</button>':'')+'</li>').join('')+'</ul>';
 sharedReadings=data.readings;
 $('#shared-list').innerHTML=data.readings.map(r=>'<article class="card"><h3>'+escape(r.title)+'</h3><p>'+escape(r.kind==='assignment'?'阅读任务':'共享合集')+' · '+escape(r.payload.due_date||'无截止日期')+'</p><p>'+escape(r.payload.instructions)+'</p><ol>'+r.payload.articles.map(a=>'<li>'+escape(a.title)+'</li>').join('')+'</ol><button data-action="open-reading" data-id="'+r.id+'">导入自己的学习库</button>'+(r.kind==='assignment'?'<button data-action="submission" data-id="'+r.id+'">填写 / 查看作答</button>':'')+((teacher||data.group.role==='editor'&&r.kind==='collection')?'<button data-action="edit-reading" data-id="'+r.id+'">编辑要求</button>':'')+(teacher?'<button data-action="progress" data-id="'+r.id+'">查看学生进度</button>':'')+((teacher||r.creator===identity.account.id)?'<button data-action="unshare" data-id="'+r.id+'">撤销分享</button>':'')+'<div id="reading-'+r.id+'"></div></article>').join('')||'<p>小组还没有分享内容。</p>';
}
async function boot(){
 identity=await call('/platform/identity');
 localStorage.setItem('margin-offline-scope',identity.scope);
 $('#identity').textContent=identity.account?'当前账户：'+identity.account.username:'未登录 · 本机学习空间';
 $('#logout').hidden=!identity.account;
 const prefs=identity.preferences;
 for(const [key,value]of Object.entries(prefs))if($('#locale').elements[key])$('#locale').elements[key].value=value;
 if(identity.shared&&!identity.account){notice('这是共享学习服务，请先登录。');return;}
 catalog=(await call('/platform/catalog')).articles;
 document.querySelectorAll('.article-options').forEach(select=>{select.innerHTML=catalog.map(a=>'<option value="'+a.id+'">'+escape(a.title)+' · '+escape(a.level)+'</option>').join('');});
 const requested=new URLSearchParams(location.search).get('article');if(catalog.some(a=>String(a.id)===requested))$('#learning-article').value=requested;
 const [ai,study]=await Promise.all([call('/ai/settings'),call('/assistance/status')]);
 $('#ai-status').textContent='问答/写作：'+(ai.configured?'已配置 '+ai.model:'未配置')+'；语境/翻译/解析/练习：'+(study.configured?'已配置 '+study.model:'未配置');
 $('#ai').elements.model.value=ai.model;
 await Promise.all([refreshCollections(),refreshGroups()]);
}
document.addEventListener('submit',event=>{
 event.preventDefault();const form=event.target,button=form.querySelector('button');if(!button)return;
 busy(button,async()=>{
 const data=Object.fromEntries(new FormData(form));
 switch(form.getAttribute('id')){
 case'login':data.register=form.elements.register.checked;await call('/writing-workspace/session','POST',data);form.elements.password.value='';clearSession();location.reload();break;
 case'ai':await call('/ai/settings','PATCH',data);form.elements.api_key.value='';notice('统一 AI 设置已保存。');break;
 case'locale':await call('/platform/preferences','PATCH',data);notice('日期与语言设置已保存，新阅读记录按所选时区计算。');break;
 case'background':await call('/platform/dimensions/'+Number($('#learning-article').value),'POST',data);notice('背景熟悉程度已保存。');$('#dimensions-load').click();break;
 case'collection':data.article_ids=selected(form);if(!data.id){delete data.id;delete data.version;}else data.version=Number(data.version);await call('/platform/collections','POST',data);form.reset();await refreshCollections();notice('合集已保存。');break;
 case'group-create':{const result=await call('/groups','POST',data);await refreshGroups();$('#group-select').value=result.group.id;await showGroup();form.reset();break;}
 case'group-join':{const result=await call('/groups/join','POST',data);await refreshGroups();$('#group-select').value=result.group.id;await showGroup();form.reset();break;}
 case'share':if(!$('#group-select').value)throw Error('请先选择小组。');data.article_ids=selected(form);await call('/groups/'+$('#group-select').value+'/readings','POST',data);form.reset();await showGroup();notice('已发布到所选小组。');break;
 case'path':{const r=await call('/platform/reading-path?'+new URLSearchParams(data));$('#reading-path').innerHTML='<p>'+escape(r.method)+'</p><ol>'+r.articles.map(a=>'<li><a href="/#reader/'+a.id+'">'+escape(a.title)+'</a> · '+escape(a.level)+' · '+a.reading_minutes+' 分钟</li>').join('')+'</ol>';break;}
 default:if(form.dataset.submission){await call('/groups/readings/'+form.dataset.submission+'/submission','POST',{answer:data.answer,version:Number(data.version)});notice('作答已保存。');await showGroup();}
 else if(form.dataset.feedback){await call('/groups/readings/'+form.dataset.feedback+'/feedback','PATCH',{...data,version:Number(data.version)});notice('反馈已保存。');}
 else if(form.id==='assessment-form'){const answers={};for(const [key,value]of Object.entries(data))answers[key]=value;const result=await call('/platform/assessment/'+assessment.id,'POST',{answers});$('#assessment').innerHTML='<p>本次正确率 '+result.percent+'%，建议参考 '+escape(result.level)+'。</p><p>'+escape(result.note)+'</p>'+result.items.map(item=>'<p>'+escape(item.prompt)+'：'+(item.correct?'正确':'需复习')+'。'+escape(item.explanation)+'</p>').join('');}
 }
 });
});
document.addEventListener('click',event=>{
 const button=event.target.closest('button');if(!button||button.type==='submit'&&button.closest('form'))return;
 busy(button,async()=>{
 const action=button.dataset.action,id=button.dataset.id;
 if(button.id==='logout'){const pending=(await MarginOffline.list('operations')).filter(o=>o.scope===identity.scope);if(pending.length&&!confirm('当前账户还有 '+pending.length+' 条未同步记录，退出后会留在本设备，重新登录原账户才能继续同步。确定退出？'))return;await fetch('/api/writing-workspace/session',{method:'DELETE',headers:{'Content-Type':'application/json','X-Margin-Writing-Account':identity.account.id},body:'{}'}).then(r=>{if(!r.ok)throw Error('退出失败，请重试。')});localStorage.setItem('margin-offline-scope','guest');clearSession();location.reload();}
 else if(button.id==='dimensions-load'){const d=await call('/platform/dimensions/'+Number($('#learning-article').value));$('#dimensions').innerHTML='<p>每百词预计生词负荷 '+d.vocabulary.load_per_100+' · '+escape(d.vocabulary.method)+'</p><p>句法负荷 '+escape(d.syntax.level)+'，平均句长 '+d.syntax.average_sentence_length+' · '+escape(d.syntax.method)+'</p><p>背景熟悉程度：'+escape({familiar:'熟悉',some:'了解一些',unfamiliar:'不熟悉',unknown:'尚未自评'}[d.background.familiarity])+'</p>';if(d.background.familiarity!=='unknown')$('#background').elements.familiarity.value=d.background.familiarity;$('#background').elements.note.value=d.background.note||'';}
 else if(button.id==='collection-new')$('#collection').reset();
 else if(action==='edit-collection'){const item=collections.find(c=>c.id===id),f=$('#collection');for(const k of ['id','version','title','description'])f.elements[k].value=item[k];for(const o of f.elements.article_ids.options)o.selected=item.article_ids.includes(Number(o.value));f.scrollIntoView();}
 else if(action==='delete-collection'){if(confirm('删除这个合集？文章仍保留。')){await call('/platform/collections/'+id,'DELETE',{version:collections.find(c=>c.id===id).version});await refreshCollections();}}
 else if(action==='rotate-invite'){await call('/groups/'+$('#group-select').value+'/rotate-invite','POST',{});await showGroup();}
 else if(action==='role'){await call('/groups/'+$('#group-select').value+'/members','PATCH',{account_id:id,role:document.querySelector('[data-role-for="'+id+'"]').value});await showGroup();}
 else if(action==='edit-reading'){const r=sharedReadings.find(x=>x.id===id);const title=prompt('分享标题',r.title);if(title===null)return;const instructions=prompt('要求 / 阶段问题',r.payload.instructions);if(instructions===null)return;await call('/groups/readings/'+id,'PATCH',{title,instructions,version:r.version});await showGroup();notice('分享已更新。');}
 else if(action==='open-reading'){const data=await call('/groups/readings/'+id+'/open','POST',{});const el=$('#reading-'+id);el.innerHTML='<p>已导入：'+data.article_ids.map(a=>'<a href="/#reader/'+a+'">去阅读</a>').join(' · ')+'</p>';}
 else if(action==='unshare'){if(confirm('撤销这份分享？已导入到成员学习库的文章会保留。')){await call('/groups/readings/'+id,'DELETE',{});await showGroup();}}
 else if(action==='submission'){const data=(await call('/groups/readings/'+id+'/submission')).submission;if(!data)throw Error('请先导入任务文章。');$('#reading-'+id).innerHTML='<form data-submission="'+id+'"><input name="version" type="hidden" value="'+data.version+'"><label>阅读作答<textarea name="answer" required maxlength="10000">'+escape(data.answer)+'</textarea></label><button>提交作答</button></form><p>教师反馈：'+escape(data.feedback||'暂无')+'</p>';}
 else if(action==='progress'){const data=await call('/groups/readings/'+id+'/progress');$('#student-progress').innerHTML='<h3>学生阅读与作答</h3>'+data.students.map(s=>'<article class="card"><h4>'+escape(s.username)+'</h4><p>阅读 '+Math.round(s.progress)+'% · '+Math.round(s.reading_seconds/60)+' 分钟</p><p>'+escape(s.answer||'尚未提交')+'</p>'+(s.version?'<form data-feedback="'+id+'"><input name="account_id" type="hidden" value="'+s.id+'"><input name="version" type="hidden" value="'+s.version+'"><label>教师反馈<textarea name="feedback" maxlength="4000" required>'+escape(s.feedback)+'</textarea></label><button>保存反馈</button></form>':'')+'</article>').join('');}
 else if(button.id==='summary-load'){const article=Number($('#learning-article').value),r=await call('/platform/reading-summary/'+article);$('#learning-summary').innerHTML='<h3>'+escape(r.article.title)+'</h3><p>待查词 '+r.pending.length+' 条，摘录/笔记 '+r.notes.length+' 条。</p><p><a href="/#reader/'+article+'">返回原文处理待查词</a> · <a href="/#knowledge">整理摘录</a> · <a href="/#exercises/'+article+'">错题与测验</a> · <a href="/#lab/'+article+'">回忆、表达与迁移</a></p>'+r.words.map(w=>'<label class="inline"><input type="checkbox" data-review-word="'+w.id+'">'+escape(w.word)+' · '+escape(w.meaning)+'</label>').join('')+'<button data-action="review-words">把所选词安排到今日复习</button>'+r.offline_notes.map(n=>'<p>离线笔记：'+escape(n.note)+'</p>').join('');}
 else if(action==='review-words'){const ids=[...document.querySelectorAll('[data-review-word]:checked')].map(e=>Number(e.dataset.reviewWord));await call('/platform/review-words','POST',{word_ids:ids});notice('所选词已进入今日复习。');}
 else if(button.id==='assessment-start'){assessment=await call('/platform/assessment','POST',{});$('#assessment').innerHTML='<form id="assessment-form">'+assessment.questions.map(q=>'<fieldset><legend>'+escape(q.prompt)+'</legend><details><summary>阅读材料</summary><p lang="en">'+escape(q.material)+'</p></details>'+q.options.map((o,i)=>'<label class="inline"><input type="radio" name="'+q.id+'" value="'+i+'" required>'+escape(o)+'</label>').join('')+'</fieldset>').join('')+'<button>提交自测</button></form>';}
 });
});
$('#group-select').addEventListener('change',()=>showGroup().catch(e=>notice(e.message)));
boot().catch(e=>notice(e.message));
