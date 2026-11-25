/* Sequential imports keep successful results and retry only unfinished links. */
'use strict';
class MarginImportQueue {
  constructor({root, api, changed}) {
    Object.assign(this, {root, api, changed});
    this.rows=[];this.running=false;this.cancelled=false;
    root.addEventListener('click',event=>{
      const button=event.target.closest('[data-import-queue]');if(!button)return;
      event.preventDefault();event.stopPropagation();
      if(button.dataset.importQueue==='stop')this.cancel();
      else this.run(button.dataset.importQueue==='all'?null:Number(button.dataset.importQueue));
    });
  }
  escape(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  render(){
    const done=this.rows.filter(r=>r.state==='saved').length;
    this.root.innerHTML=`<p>${this.running?'正在处理':this.cancelled?'已停止后续导入':'本批处理结束'}：完成 ${done} / ${this.rows.length} 篇。</p>`+
      this.rows.map((r,i)=>`<div class="batch-row ${r.state==='failed'?'fail':r.state==='saved'?'ok':''}"><span class="batch-mark">${({pending:'…',working:'↻',saved:'✓',failed:'✕'})[r.state]}</span><div><strong>${this.escape(r.url)}</strong><small>${this.escape(r.message)}</small>${!this.running&&r.state!=='saved'?`<button type="button" class="button small" data-import-queue="${i}">重试此项</button>`:''}</div></div>`).join('')+
      (this.running?'<button type="button" class="button small" data-import-queue="stop">停止后续导入</button><p>正在处理的链接会先完成，成功结果会保留。</p>':this.rows.some(r=>r.state!=='saved')?'<button type="button" class="button small" data-import-queue="all">重试未完成项</button>':'');
  }
  cancel(){if(this.running){this.cancelled=true;this.render();}}
  async start(urls,category){
    if(this.running)return;
    if(!urls.length||urls.length>10||urls.some(u=>!/^https?:\/\//i.test(u)))throw Error('请填写 1–10 个有效的 http(s) 链接，每行一个。');
    this.category=category;this.rows=[...new Set(urls)].map(url=>({url,state:'pending',message:'等待处理'}));
    await this.run();
  }
  async run(index=null){
    if(this.running)return;
    this.running=true;this.cancelled=false;this.render();
    try{
      for(let i=0;i<this.rows.length;i++){
        const row=this.rows[i];if(this.cancelled)break;
        if(row.state==='saved'||index!==null&&index!==i)continue;
        row.state='working';row.message='正在提取正文并保存…';this.render();
        try{
          const result=await this.api('/articles/batch',{method:'POST',body:{urls:[row.url],category:this.category}});
          const entry=result.results[0];if(!entry?.ok)throw Error(entry?.error||'导入失败，请重试。');
          row.state='saved';row.message=entry.duplicate?'文章已存在，已保留原记录。':entry.message||'文章已保存。';
        }catch(error){row.state='failed';row.message=error.message;}
        this.render();
      }
    }finally{this.running=false;this.render();await this.changed().catch(()=>{});}
  }
}
