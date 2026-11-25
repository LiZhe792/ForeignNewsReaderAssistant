'use strict';
window.MarginOffline={
 async db(){return new Promise((resolve,reject)=>{const r=indexedDB.open('margin-offline-v1',1);r.onupgradeneeded=()=>{r.result.createObjectStore('bundles',{keyPath:'key'});r.result.createObjectStore('operations',{keyPath:'id'});};r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(Error('浏览器无法保存离线数据。'));});},
 async transaction(store,action){const db=await this.db();try{return await new Promise((resolve,reject)=>{const tx=db.transaction(store,'readwrite');const req=action(tx.objectStore(store));let result;req.onsuccess=()=>result=req.result;tx.oncomplete=()=>resolve(result);tx.onerror=()=>reject(Error('离线数据保存失败；请检查可用空间。'));});}finally{db.close()}},
 list(store){return this.transaction(store,s=>s.getAll())},
 put(store,value){return this.transaction(store,s=>s.put(value))},
 remove(store,id){return this.transaction(store,s=>s.delete(id))},
 async save(id,scope){
  const response=await fetch('/api/platform/offline-bundle/'+id,{headers:{'X-Margin-Account':scope}});
  if(!response.ok)throw Error('下载失败，请检查连接。');
  const bundle=await response.json();
  if(bundle.scope!==scope)throw Error('账户已切换，请刷新。');
  bundle.key=scope+':'+id;await this.put('bundles',bundle);localStorage.setItem('margin-offline-scope',scope);
 },
 async queue(operation){
  const value={...operation,id:crypto.randomUUID()};
  if(value.kind==='progress')for(const old of await this.list('operations'))if(old.scope===value.scope&&old.kind==='progress'&&old.article_id===value.article_id)await this.remove('operations',old.id);
  await this.put('operations',value);return value;
 }
};
(async()=>{
 if(!document.getElementById('offline-articles'))return;
 const $=s=>document.querySelector(s),e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let scope=localStorage.getItem('margin-offline-scope')||'guest',selected=null;
 window.addEventListener('storage',event=>{if(event.key==='margin-offline-scope'&&event.newValue!==scope)location.reload();});
 const note=message=>$('#offline-status').textContent=message;
 try{const info=await fetch('/api/platform/identity').then(r=>r.ok?r.json():Promise.reject());scope=info.scope;localStorage.setItem('margin-offline-scope',scope);}catch{}
 async function render(){
  const bundles=(await MarginOffline.list('bundles')).filter(b=>b.scope===scope);
  $('#offline-articles').innerHTML=bundles.map(b=>'<button data-open="'+b.article.id+'">'+e(b.article.title)+'</button>').join('')||'<p>尚未保存文章。联网打开文章，在文末选择“保存到离线阅读”。</p>';
  const ops=(await MarginOffline.list('operations')).filter(o=>o.scope===scope);
  $('#offline-count').textContent=ops.length+' 条待同步';
 }
 document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  try{
   if(button.dataset.open){
    selected=(await MarginOffline.list('bundles')).find(b=>b.scope===scope&&String(b.article.id)===button.dataset.open);
    $('#offline-title').textContent=selected.article.title;
    $('#offline-body').textContent=selected.article.content;
    $('#offline-body').lang=selected.language||'en';
    $('#offline-reader').hidden=false;
    requestAnimationFrame(()=>{const body=$('#offline-body');body.scrollTop=(body.scrollHeight-body.clientHeight)*selected.article.position/100});
   }else if(button.id==='offline-save-progress'){
    if(!selected)throw Error('请先打开一篇已保存的文章。');
    const el=$('#offline-body'),position=Math.round(100*el.scrollTop/Math.max(1,el.scrollHeight-el.clientHeight));
    await MarginOffline.queue({scope,kind:'progress',article_id:selected.article.id,content_hash:selected.fingerprint,position,progress:Math.max(selected.article.progress,position),base_read_at:selected.article.last_read_at});
    note('阅读位置已在本设备保存。');await render();
   }else if(button.id==='offline-sync'){
    const response=await fetch('/api/platform/identity');if(!response.ok)throw Error('无法连接服务，请联网并登录原账户。');
    const info=await response.json();if(info.scope!==scope)throw Error('当前账户不同，请登录保存这些文章时的账户。');
    for(const operation of (await MarginOffline.list('operations')).filter(o=>o.scope===scope)){
     const reply=await fetch('/api/platform/sync',{method:'POST',headers:{'Content-Type':'application/json','X-Margin-Account':scope},body:JSON.stringify({operations:[operation]})});
     const data=await reply.json();if(!reply.ok)throw Error(data.error||'同步失败，记录仍保留。');
     const result=data.results[0];
     if(result.status==='saved')await MarginOffline.remove('operations',operation.id);
     else{
      $('#offline-conflicts').innerHTML='<p>'+e(result.reason)+'</p>'+(operation.kind==='progress'?'<button data-resolve="server" data-id="'+operation.id+'">保留服务端位置</button><button data-resolve="local" data-id="'+operation.id+'">采用本机位置</button>':'<p>原文已变化，请先导出待同步记录后重新保存文章；旧记录不会自动丢弃。</p>');
      note('有同步冲突，请选择处理方式。');await render();return;
     }
    }
    $('#offline-conflicts').replaceChildren();note('全部已同步。');await render();
   }else if(button.dataset.resolve){
    const op=(await MarginOffline.list('operations')).find(o=>o.id===button.dataset.id&&o.scope===scope);
    if(op){op.resolution=button.dataset.resolve;await MarginOffline.put('operations',op);$('#offline-sync').click();}
   }else if(button.id==='offline-export'){
    const data=(await MarginOffline.list('operations')).filter(o=>o.scope===scope);
    const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='margin-offline-pending.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
   }else if(button.id==='offline-delete'){
    if(selected&&confirm('移除本设备的这篇离线副本？服务端文章和待同步记录保留。')){await MarginOffline.remove('bundles',selected.key);selected=null;$('#offline-reader').hidden=true;await render();}
   }else if(button.id==='offline-lookup'){
    const word=$('#offline-word').value.trim().toLowerCase();
    const found=selected?.vocabulary.find(w=>w.word.toLowerCase()===word);
    const dictionary=await fetch('/offline-dictionary.json').then(r=>r.ok?r.json():{}).catch(()=>({}));
    $('#offline-meaning').value=found?.meaning||dictionary[word]||'';
    note($('#offline-meaning').value?'已从离线词典找到释义。':'离线词典未收录，可手动填写释义后保存。');
   }
  }catch(error){note(error.message)}
 });
 $('#offline-word-form').addEventListener('submit',async event=>{event.preventDefault();try{
  if(!selected)throw Error('请先打开文章。');
  await MarginOffline.queue({scope,kind:'word',article_id:selected.article.id,content_hash:selected.fingerprint,word:$('#offline-word').value,meaning:$('#offline-meaning').value,context:''});
  note('生词已暂存，恢复连接后可同步。');await render();
 }catch(error){note(error.message)}});
 $('#offline-note-form').addEventListener('submit',async event=>{event.preventDefault();try{
  if(!selected)throw Error('请先打开文章。');
  await MarginOffline.queue({scope,kind:'note',article_id:selected.article.id,content_hash:selected.fingerprint,note:$('#offline-note').value,quote:''});
  $('#offline-note').value='';note('笔记已暂存。');await render();
 }catch(error){note(error.message)}});
 $('#offline-size').addEventListener('input',event=>$('#offline-body').style.fontSize=event.target.value+'px');
 $('#offline-theme').addEventListener('change',event=>$('#offline-body').className=event.target.value);
 await render();
})();
