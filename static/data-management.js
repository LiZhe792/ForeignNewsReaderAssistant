/* The local server owns learning data. Backups are explicit, portable local files. */
'use strict';
(() => {
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const bytes=n=>n>=1024*1024?(n/1024/1024).toFixed(1)+' MB':(n/1024).toFixed(1)+' KB';
  const token=()=>window.crypto?.randomUUID?.()||Date.now().toString(36)+'_'+Math.random().toString(36).slice(2);
  const button=(id,label,attrs='')=>`<button type="button" class="button small" data-manage="${id}" ${attrs}>${label}</button>`;
  const date=value=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(value));
  class Data {
    static prepare(info){
      try{
        const previous=sessionStorage.getItem('margin.data-generation');
        if(previous&&previous!==info.generation){
          const keys=[];for(let i=0;i<sessionStorage.length;i++){const key=sessionStorage.key(i);if(key.startsWith('margin.')||key.startsWith('margin-reading-draft-'))keys.push(key);}
          keys.forEach(key=>sessionStorage.removeItem(key));
        }
        sessionStorage.setItem('margin.data-generation',info.generation);
      }catch{}
      Data.network(info);
    }
    static network(info){Data.offline=!!info.offline;const text=document.querySelector('#network-status-text');if(text)text.textContent=info.offline?'离线模式 · 本机服务':'本地学习空间';}
    constructor({root,api,toast,confirm}){Object.assign(this,{root,api,toast,confirm});this.abort=new AbortController();this.previews={};this.version={restore:0,articles:0};
      for(const type of ['click','change','submit'])root.addEventListener(type,e=>this.handle(e),{signal:this.abort.signal});
    }
    request(path,method='GET',body,extra={}){return this.api('/data'+path,{method,body,signal:this.abort.signal,...extra});}
    destroy(){this.dead=true;this.abort.abort();}
    async render(){
      this.root.innerHTML=`<div class="page-wrap data-page"><div class="page-intro"><div><span class="eyebrow">YOUR READING, IN YOUR HANDS</span><h1>离线与数据管理</h1><p>把文章留在本机，为学习记录保留一份可靠副本。</p></div></div><p id="data-message" role="status"></p>
        <section class="data-panel"><h2>离线使用</h2><label class="data-switch"><input type="checkbox" id="data-offline">启用离线模式</label><p>本地文章、英汉词典、生词、复习、已保存的译文和学习分析可继续使用。网页导入与新的 AI 生成暂停，在线词典仅使用已有缓存。</p><p class="data-note">需要保持本机 Python 服务运行；关闭服务后浏览器页面不能独立工作。离线开关限制本应用的外部请求，不改变电脑网络设置。</p><label class="data-switch"><input type="checkbox" id="data-auto">每日首次启动程序时自动备份</label><p class="data-note">自动备份保留最近 7 份；手动备份与还原前备份由你管理。启动备份失败时保留原数据，可在这里手动重试。</p></section>
        <section class="data-panel"><div class="data-heading"><h2>完整备份与迁移</h2>${button('backup','创建完整备份')}</div><p>备份包含文章、词汇、笔记、复习计划、学习记录、译文、图片与录音。先创建，再下载到自己保存的位置；换电脑后可选择文件预览还原。</p><p class="data-note">备份文件最多 128 MB，数据库解压后最多 256 MB。备份不包含 Python 环境、程序文件或服务器环境变量中的 AI 密钥。</p><div id="data-backups"></div></section>
        <section class="data-panel"><h2>从完整备份还原</h2><p>还原会替换当前学习数据。确认前先核对两份数据；程序会额外保留一份还原前备份，成功后重新载入页面。</p><form id="data-restore-form"><label>选择 Margin 备份 ZIP<input type="file" name="file" accept=".zip,application/zip" required></label><button class="button small" type="submit">校验并预览备份</button></form><div id="data-restore-preview"></div></section>
        <section class="data-panel"><div class="data-heading"><h2>离线文章包</h2><a class="button small" href="/api/data/articles/export" download>导出全部文章 JSON ↓</a></div><p>在电脑之间分享和添加正文，重复正文自动跳过。文章包只含文章内容和来源，不携带自己的阅读进度或词汇记录。</p><form id="data-articles-form"><label>选择文章包 JSON · 最多 500 篇 / 20 MB<input type="file" name="file" accept=".json,application/json" required></label><button class="button small" type="submit">预览文章包</button></form><div id="data-articles-preview"></div><p class="data-note">词汇可在「我的生词」中使用已有的 JSON / CSV 导入导出。完整迁移复习计划、图片与录音，请使用完整备份。</p></section>
        <section class="data-panel"><div class="data-heading"><h2>存储空间与缓存</h2>${button('refresh','刷新统计')}</div><div id="data-storage"></div>${button('cache','清理词典缓存')}<p class="data-note">清理可重新查询的释义缓存，保留文章、词汇、译文与学习记录。清理后空间可由数据库复用，文件大小可能不立即缩小。</p></section></div>`;
      await this.reload();
    }
    async reload(){
      try{const info=await this.request('/state');if(this.dead)return;this.info=info;Data.network(info);this.root.querySelector('#data-offline').checked=info.offline;this.root.querySelector('#data-auto').checked=info.auto_backup;
        this.root.querySelector('#data-storage').innerHTML=`<div class="data-storage-grid">${[['学习数据库',bytes(info.storage.database_bytes)],['媒体内容',bytes(info.storage.media_bytes)],['词典缓存',info.storage.cache_entries+' 条 / '+bytes(info.storage.cache_bytes)],['本机备份',info.backups.length+' 份 / '+bytes(info.backups.reduce((n,b)=>n+b.size,0))]].map(([name,value])=>`<div><span>${name}</span><strong>${value}</strong></div>`).join('')}</div>`;
        this.root.querySelector('#data-backups').innerHTML=info.backups.length?`<div class="data-table-scroll"><table><thead><tr><th>创建时间</th><th>类型</th><th>内容与大小</th><th>操作</th></tr></thead><tbody>${info.backups.map(item=>`<tr data-backup-id="${esc(item.id)}"><th scope="row">${esc(date(item.created_at))}</th><td>${{manual:'手动备份',auto:'自动备份',rescue:'还原前备份'}[item.kind]||'备份'}</td><td>${item.summary.articles} 篇 / ${item.summary.words} 词 · ${bytes(item.size)}</td><td><a class="button small" href="/api/data/backups/${esc(item.id)}/download" download>下载</a>${button('delete-backup','删除',`data-id="${esc(item.id)}"`)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="data-empty">暂无备份。可以先创建一份完整备份，再下载保存。</p>';
      }catch(error){if(!this.dead)this.message(error.message,true);}
    }
    message(text,error=false){const target=this.root.querySelector('#data-message');if(target){target.textContent=text;target.classList.toggle('data-error',error);}}
    showPreview(kind,preview){
      this.previews[kind]=preview;const container=this.root.querySelector('#data-'+kind+'-preview');
      if(kind==='restore'){
        const labels={articles:'文章',words:'词条',reviews:'复习记录',translations:'AI 学习结果',recordings:'听说记录',exercises:'阅读练习'};
        container.innerHTML=`<div class="data-preview"><h3>已通过文件与数据库检查</h3><table><thead><tr><th>数据</th><th>当前</th><th>备份内</th></tr></thead><tbody>${Object.entries(labels).map(([key,label])=>`<tr><th scope="row">${label}</th><td>${preview.current[key]}</td><td>${preview.incoming[key]}</td></tr>`).join('')}</tbody></table><p class="data-note">备份内离线模式：${preview.incoming.offline?'开启':'关闭'}；自动备份：${preview.incoming.auto_backup?'开启':'关闭'}。这些设置会一并还原。</p><label class="data-switch"><input type="checkbox" data-restore-confirm>我已核对预览，同意用备份替换当前数据</label>${button('apply-restore','确认还原','disabled')}${button('cancel-preview','取消预览','data-kind="restore"')}<p class="data-note">预览 15 分钟内有效；预览后若发生新的学习操作，需要重新预览。其他页面的旧操作会暂停，重新载入后继续使用。</p></div>`;
      }else container.innerHTML=`<div class="data-preview"><h3>文章包预览</h3><p>有效正文 ${preview.incoming.articles} 篇，预计新增 ${preview.incoming.new_articles} 篇，其余跳过重复。</p><ul>${preview.incoming.titles.slice(0,20).map(title=>`<li>${esc(title)}</li>`).join('')}</ul>${button('apply-articles','确认导入 · 跳过重复')}${button('cancel-preview','取消预览','data-kind="articles"')}<p class="data-note">确认时再次去重，不覆盖已有文章或阅读进度。仅展示前 20 个标题。</p></div>`;
    }
    async preview(kind,file){
      const version=++this.version[kind];const previous=this.previews[kind];
      if(previous)await this.request('/previews/'+previous.token,'DELETE',{});
      delete this.previews[kind];this.root.querySelector('#data-'+kind+'-preview').innerHTML='';
      const form=new FormData();form.append('file',file);
      const result=await this.request('/'+(kind==='restore'?'restore':'articles')+'/preview','POST',form);
      if(this.dead||version!==this.version[kind])return;
      this.showPreview(kind,result.preview);this.message('预览完成，请核对后确认。');
    }
    async handle(event){
      const target=event.target;
      if(event.type==='submit'&&target.matches('#data-restore-form,#data-articles-form')){
        event.preventDefault();event.stopPropagation();if(this.busy)return;const file=target.elements.file.files[0];if(!file)return;
        return this.mutate(()=>this.preview(target.id==='data-restore-form'?'restore':'articles',file));
      }
      if(event.type==='change'){
        if(target.matches('[data-restore-confirm]')){this.root.querySelector('[data-manage=apply-restore]').disabled=!target.checked;return;}
        if(target.matches('#data-offline,#data-auto')&&!this.busy){const key=target.id==='data-offline'?'offline':'auto_backup',value=target.checked;return this.mutate(async()=>{await this.request('/preferences','PATCH',{[key]:value});if(!this.dead){await this.reload();this.message('设置已保存。');}});}
        return;
      }
      if(event.type!=='click')return;const clicked=target.closest('[data-manage]'),id=clicked?.dataset.manage;if(!id||this.busy)return;
      if(id==='refresh')return this.reload();
      if(id==='delete-backup'||id==='cache'){
        const accepted=await this.confirm(id==='cache'?'清理可重新查询的词典缓存？已保存的文章与学习结果将保留。':'删除这份本机备份文件？请先确认已下载需要的副本。');
        if(!accepted||this.dead)return;
      }
      return this.mutate(async()=>{
        if(id==='backup'){const result=await this.request('/backups','POST',{});if(this.dead)return;await this.reload();this.message('完整备份已创建，请点击下载保存。');this.root.querySelector(`[data-backup-id="${result.backup.id}"]`)?.scrollIntoView({block:'nearest'});}
        if(id==='delete-backup'){await this.request('/backups/'+clicked.dataset.id,'DELETE',{confirm:true});if(!this.dead){await this.reload();this.message('选中的备份文件已删除。');}}
        if(id==='cache'){const result=await this.request('/cache/clear','POST',{confirm:true});if(!this.dead){await this.reload();this.message(`已清理 ${result.removed} 条词典缓存。`);}}
        if(id==='cancel-preview'){const kind=clicked.dataset.kind;await this.request('/previews/'+this.previews[kind].token,'DELETE',{});if(!this.dead){delete this.previews[kind];this.root.querySelector('#data-'+kind+'-preview').innerHTML='';}}
        if(id==='apply-restore'||id==='apply-articles'){
          const kind=id==='apply-restore'?'restore':'articles',preview=this.previews[kind];if(!preview)return;
          preview.request_id ||= token();
          const payload={token:preview.token,request_id:preview.request_id,...(kind==='restore'?{replace:true,generation:preview.generation,revision:preview.revision}:{})};
          const result=await this.request('/'+kind+'/apply','POST',payload,{allowDataChange:kind==='restore'});
          if(this.dead)return;
          if(kind==='restore'){this.message('还原完成，已保留还原前备份，正在重新载入…');location.reload();}
          else{delete this.previews[kind];this.root.querySelector('#data-articles-preview').innerHTML='';await this.reload();this.message(`已新增 ${result.added} 篇文章，跳过 ${result.skipped} 篇重复正文。`);}
        }
      });
    }
    async mutate(work){
      if(this.busy)return;this.busy=true;
      const controls=[...this.root.querySelectorAll('button,input')].filter(n=>!n.disabled);controls.forEach(n=>n.disabled=true);this.message('正在处理，请稍候…');
      try{await work();}catch(error){if(!this.dead){this.message(error.message,true);this.toast(error.message,true);}}
      finally{this.busy=false;controls.forEach(n=>{if(n.isConnected)n.disabled=false;});}
    }
  }
  window.MarginData=Data;
})();
