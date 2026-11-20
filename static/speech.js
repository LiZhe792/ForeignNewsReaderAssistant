/* One audio element and one explicit playback session, shared across playlist articles. */
'use strict';

window.MarginSpeech = class MarginSpeech {
  constructor({api, toast}) {
    Object.assign(this, {api, toast});
    this.audio = new Audio();
    this.audio.preload = 'auto';
    this.audio.preservesPitch = true;
    this.settings = {voice:'en-US-AriaNeural',rate:1,follow:true,click_to_play:false,loop:'none'};
    this.voices = [];
    this.items = [];
    this.sequence = [];
    this.position = 0;
    this.version = this.mountVersion = this.cacheVersion = this.settingsVersion = 0;
    this.requests = new Map();
    this.saveQueue = Promise.resolve();
    this.state = 'idle';
    this.desired = 'paused';
    document.addEventListener('click', event=>this.onClick(event));
    document.addEventListener('change', event=>this.onChange(event));
    document.addEventListener('input', event=>{
      if (event.target.id==='tts-rate') {
        this.settings.rate=Number(event.target.value);this.audio.defaultPlaybackRate=this.settings.rate;this.audio.playbackRate=this.settings.rate;
        this.$('#tts-rate-label').textContent=this.settings.rate.toFixed(1)+'×';
      }
    });
    document.addEventListener('selectionchange', ()=>this.captureSelection());
    document.addEventListener('keydown', event=>{
      if (!this.clickToPlay || !['Enter',' '].includes(event.key) || event.ctrlKey || event.altKey || event.metaKey) return;
      const sentence=event.target.closest('.tts-sentence');
      if (!sentence || !this.reader?.body.contains(sentence)) return;
      event.preventDefault();this.startAt(Number(sentence.dataset.sentenceIndex));
    },true);
    window.addEventListener('pagehide', ()=>this.stop());
  }

  $(selector) { return this.panel?.querySelector(selector); }
  escape(value) { return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  get clickToPlay() { return Boolean(this.root && this.settings.click_to_play); }
  get current() { return this.sequence[this.position]; }
  status(message) { if(this.panel)this.$('#tts-status').textContent=message; }

  mount({root, article, reader}) {
    Object.assign(this,{root,article,reader});
    this.mountEvents=new AbortController();
    root.querySelector('.reading-status').before(document.getElementById('speech-player-template').content.cloneNode(true));
    this.panel=root.querySelector('#speech-player');
    const version=++this.mountVersion;
    this.focusIndex=0;this.followSuspended=false;
    const suspend=()=>{
      if(this.state==='playing' && this.settings.follow){this.followSuspended=true;this.status('已暂缓跟随。点击“定位当前句”可继续跟随。');}
    };
    reader.scroller.addEventListener('wheel',suspend,{passive:true,signal:this.mountEvents.signal});
    reader.scroller.addEventListener('touchstart',suspend,{passive:true,signal:this.mountEvents.signal});
    this.render();
    this.ready=this.initialize(version);
  }

  async initialize(version) {
    try {
      await this.saveQueue.catch(()=>{});
      const [config,list,cache]=await Promise.all([this.api('/tts/settings'),this.api('/tts/playlist'),this.api('/tts/cache')]);
      if(version!==this.mountVersion || !this.panel)return;
      this.settings=config.settings;this.voices=config.voices;this.items=list.items;
      this.hydrate();this.renderList();this.renderCache(cache);this.render();
      if(this.pendingItem && this.pendingItem.article_id===this.article.id){
        const item=this.pendingItem;this.pendingItem=null;this.show();await this.playItem(item);
      }
    } catch(error){if(version===this.mountVersion){this.pendingItem=null;this.status(error.message);}}
  }

  hydrate() {
    if(!this.panel)return;
    const voice=this.voices.find(v=>v.id===this.settings.voice);
    this.$('#tts-accent').value=voice?.accent || 'en-US';
    this.$('#tts-voice').innerHTML=this.voices.filter(v=>v.accent===this.$('#tts-accent').value).map(v=>`<option value="${v.id}">${this.escape(v.name)}</option>`).join('');
    this.$('#tts-voice').value=this.settings.voice;
    this.$('#tts-rate').value=this.settings.rate;
    this.$('#tts-rate-label').textContent=this.settings.rate.toFixed(1)+'×';
    this.$('#tts-follow').checked=this.settings.follow;
    this.$('#tts-click').checked=this.settings.click_to_play;
    this.$('#tts-loop').value=this.settings.loop;
    this.reader.body.classList.toggle('tts-click-enabled',this.clickToPlay);
    this.reader.body.querySelectorAll('.tts-sentence').forEach(span=>{
      if(this.clickToPlay){span.tabIndex=0;span.setAttribute('role','button');span.setAttribute('aria-label','从此句开始朗读：'+span.textContent.trim());}
      else{span.removeAttribute('tabindex');span.removeAttribute('role');span.removeAttribute('aria-label');}
    });
  }

  save(patch) {
    const revision=++this.settingsVersion;
    Object.assign(this.settings,patch);this.hydrate();
    const mount=this.mountVersion;
    this.saveQueue=this.saveQueue.catch(()=>{}).then(()=>this.api('/tts/settings',{method:'PATCH',body:patch}));
    this.saveQueue.then(()=>{
      if(this.panel && revision===this.settingsVersion && mount===this.mountVersion)this.$('#tts-settings-status').textContent='朗读设置已保存。';
    }).catch(error=>{
      if(this.panel && revision===this.settingsVersion && mount===this.mountVersion)this.$('#tts-settings-status').textContent=error.message+' 请重新调整此选项以重试保存。';
    });
  }

  show() {
    if(!this.panel)return;
    const anchor=this.reader.captureAnchor();
    this.panel.hidden=false;
    this.root.querySelector('[data-tts-action="toggle"]').setAttribute('aria-expanded','true');
    this.reader.restoreAnchor(anchor);
  }

  hide() {
    const anchor=this.reader.captureAnchor();this.panel.hidden=true;
    this.root.querySelector('[data-tts-action="toggle"]').setAttribute('aria-expanded','false');
    this.reader.restoreAnchor(anchor);
  }

  captureSelection() {
    if(!this.root || document.querySelector('dialog[open]'))return;
    const selection=window.getSelection();
    if(!selection?.rangeCount || selection.isCollapsed || !this.reader.body.contains(selection.anchorNode) || !this.reader.body.contains(selection.focusNode))return;
    const text=selection.toString().trim();
    const node=selection.anchorNode.nodeType===Node.ELEMENT_NODE?selection.anchorNode:selection.anchorNode.parentElement;
    const sentence=node.closest('.tts-sentence');
    if(sentence)this.focusIndex=Number(sentence.dataset.sentenceIndex);
    if(/^[A-Za-z]+(?:['’-][A-Za-z]+)*$/.test(text) && text.length<=60){
      this.$('#tts-word').value=text;this.$('#tts-scope').value='word';this.$('#tts-word').hidden=false;
    }
  }

  scopeRequest() {
    const scope=this.$('#tts-scope').value;
    if(scope==='word'){
      const word=this.$('#tts-word').value.trim();
      if(!/^[A-Za-z]+(?:['’-][A-Za-z]+)*$/.test(word) || word.length>60)throw new Error('请先划选或输入一个英文单词。');
      return {scope,word};
    }
    const segments=this.article.tts_segments;
    let index=this.current?.index ?? this.focusIndex;
    if(!segments[index] || segments[index].paragraph!==this.reader.current)index=segments.find(s=>s.paragraph===this.reader.current)?.index || 0;
    return {scope,article_id:this.article.id,unit_index:scope==='paragraph'?this.reader.current:scope==='sentence'?index:0};
  }

  units(item) {
    if(item.scope==='word')return [{scope:'word',text:item.word,word:item.word}];
    const segments=this.article.tts_segments || [];
    return segments.filter(s=>item.scope==='article' || (item.scope==='sentence'?s.index===item.unit_index:s.paragraph===item.unit_index))
      .map(s=>({...s,scope:'sentence',article_id:this.article.id}));
  }

  async startScope() {
    const mount=this.mountVersion;
    await this.ready;
    if(!this.panel || mount!==this.mountVersion)return;
    const item=this.scopeRequest();
    this.activeItemId=null;this.start(this.units(item));
  }

  startAt(index) {
    this.focusIndex=index;this.activeItemId=null;this.show();
    this.$('#tts-scope').value='article';this.$('#tts-word').hidden=true;
    this.start(this.units({scope:'article'}),index);
  }

  start(sequence,position=0) {
    this.sequence=sequence;this.position=position;this.followSuspended=false;this.desired='playing';
    if(!sequence[position]){this.status('没有可朗读的内容。');return;}
    this.show();this.loadSegment(position);
  }

  audioRequest(unit,voice=this.settings.voice) {
    const body=unit.scope==='word'?{scope:'word',word:unit.word,voice}:{article_id:unit.article_id,sentence_index:unit.index,voice};
    const key=JSON.stringify(body);
    if(!this.requests.has(key)){
      const promise=this.api('/tts/audio',{method:'POST',body,timeoutMs:65000});
      this.requests.set(key,promise);
      promise.catch(()=>{if(this.requests.get(key)===promise)this.requests.delete(key);});
    }
    return this.requests.get(key);
  }

  async loadSegment(position) {
    const version=++this.version;
    this.mediaEvents?.abort();this.audio.pause();this.audio.removeAttribute('src');this.audio.load();
    this.position=position;this.state='loading';this.highlight(false);this.render();
    this.status(this.desired==='paused'?'正在准备音频，完成后保持暂停。':'正在准备音频…');
    const unit=this.current;
    try {
      const result=await this.audioRequest(unit);
      if(version!==this.version || !this.panel)return;
      this.mediaEvents=new AbortController();
      const options={signal:this.mediaEvents.signal};
      this.audio.addEventListener('timeupdate',()=>{if(version===this.version)this.renderProgress();},options);
      this.audio.addEventListener('loadedmetadata',()=>{if(version===this.version)this.renderProgress();},options);
      this.audio.addEventListener('ended',()=>{if(version===this.version && this.state==='playing')this.advance();},options);
      this.audio.addEventListener('error',()=>{
        if(version!==this.version)return;
        this.requests.clear();this.desired='paused';this.state='error';this.highlight(false);this.render();
        this.status('音频无法播放或缓存已清理，点击“重试”重新获取本句。');
      },options);
      this.audio.src=result.url;this.audio.defaultPlaybackRate=this.settings.rate;this.audio.load();this.audio.playbackRate=this.settings.rate;
      this.sourceNote=result.cached?'正在使用本机缓存。':'语音已生成并缓存到本机。';
      if(this.desired==='playing')await this.tryPlay(version);
      else{this.state='paused';this.highlight(true);this.render();this.status('已暂停，可从本句继续。');}
      if(version===this.version)this.updateCacheStats();
    } catch(error){
      if(version!==this.version || !this.panel)return;
      this.state='error';this.desired='paused';this.highlight(false);this.render();this.status(error.message+' 点击“重试”可再次获取本句。');
    }
  }

  async tryPlay(version=this.version) {
    try {
      await this.audio.play();
      if(version!==this.version)return;
      if(this.desired!=='playing'){this.audio.pause();this.state='paused';this.render();return;}
      this.state='playing';this.highlight(true);this.follow();this.render();this.status(this.sourceNote);
      const next=this.nextPosition();
      if(next!=null && next!==this.position)this.audioRequest(this.sequence[next]).catch(()=>{});
    } catch(error){
      if(version!==this.version || !this.panel)return;
      this.desired='paused';this.state=error.name==='NotAllowedError'?'paused':'error';this.render();
      this.status(error.name==='NotAllowedError'?'浏览器需要一次手动点击，请点击“继续”播放。':'播放未成功，请点击“重试”重新获取本句。');
    }
  }

  pause() {
    this.desired='paused';this.audio.pause();
    if(this.state!=='loading')this.state='paused';
    this.render();this.status(this.state==='loading'?'正在准备音频，完成后保持暂停。':'已暂停，点击“继续”从原处播放。');
  }

  togglePlay() {
    if(this.desired==='playing' && ['playing','loading'].includes(this.state)){this.pause();return;}
    if(this.state==='idle' || !this.current){this.startScope().catch(error=>this.status(error.message));return;}
    this.desired='playing';
    if(this.state==='complete'){this.loadSegment(0);return;}
    if(this.state==='error'){this.loadSegment(this.position);return;}
    if(this.state==='loading'){this.render();this.status('音频准备好后开始播放。');return;}
    this.tryPlay();
  }

  nextPosition() {
    if(this.settings.loop==='sentence')return this.position;
    const next=this.position+1;
    if(this.settings.loop==='paragraph' && this.current?.paragraph!=null){
      if(this.sequence[next]?.paragraph===this.current.paragraph)return next;
      return this.sequence.findIndex(s=>s.paragraph===this.current.paragraph);
    }
    return next<this.sequence.length?next:null;
  }

  async advance(manual=false) {
    const next=manual?(this.position+1<this.sequence.length?this.position+1:null):this.nextPosition();
    if(next!=null){this.desired='playing';this.loadSegment(next);return;}
    if(this.activeItemId){
      const version=this.version;
      try{
        await this.refreshList();
        if(version!==this.version || !this.panel)return;
        const index=this.items.findIndex(item=>item.id===this.activeItemId);
        if(index>=0 && this.items[index+1]){await this.playItem(this.items[index+1]);return;}
      }catch(error){this.status(error.message);this.pause();return;}
    }
    this.desired='paused';this.state='complete';this.highlight(false);this.render();
    this.status(this.activeItemId?'播放列表已播放完毕。':'本次朗读已完成。');
  }

  highlight(on) {
    if(!this.reader)return;
    this.reader.body.querySelectorAll('.tts-speaking').forEach(span=>{span.classList.remove('tts-speaking');span.removeAttribute('aria-current');});
    if(on && this.current?.article_id===this.article?.id){
      const span=this.reader.body.querySelector(`[data-sentence-index="${this.current.index}"]`);
      if(span){span.classList.add('tts-speaking');span.setAttribute('aria-current','true');this.focusIndex=this.current.index;}
    }
  }

  follow(force=false) {
    if(!this.reader || document.querySelector('dialog[open]') || !this.current || this.current.scope==='word')return;
    if(force)this.followSuspended=false;
    if(!force && (!this.settings.follow || this.followSuspended))return;
    const span=this.reader.body.querySelector(`[data-sentence-index="${this.current.index}"]`);
    if(!span)return;
    const rect=span.getBoundingClientRect(), viewport=this.reader.scroller.getBoundingClientRect();
    if(force || rect.top<viewport.top+12 || rect.bottom>viewport.bottom-20){
      this.reader.scroller.scrollBy({top:rect.top-viewport.top-Math.min(65,viewport.height*.2),behavior:'instant'});
      this.reader.remember();
    }
  }

  renderProgress() {
    if(!this.panel)return;
    const duration=this.audio.duration, current=this.audio.currentTime || 0;
    const time=value=>Number.isFinite(value)?`${Math.floor(value/60)}:${String(Math.floor(value%60)).padStart(2,'0')}`:'0:00';
    this.$('#tts-time').textContent=time(current)+' / '+time(duration);
    this.$('#tts-progress').disabled=!Number.isFinite(duration) || duration<=0;
    if(document.activeElement!==this.$('#tts-progress'))this.$('#tts-progress').value=duration>0?current/duration*1000:0;
  }

  render() {
    if(!this.panel)return;
    const active=this.desired==='playing' && ['playing','loading'].includes(this.state);
    this.$('#tts-play').textContent=active?'暂停':this.state==='error'?'重试':this.state==='complete'?'重播':['paused','loading'].includes(this.state)?'继续':'播放';
    this.$('[data-tts-action="previous"]').disabled=!this.current || this.position<=0;
    this.$('[data-tts-action="next"]').disabled=!this.current || (this.position>=this.sequence.length-1 && !this.activeItemId);
    this.$('[data-tts-action="locate"]').disabled=!this.current || this.current.scope==='word';
    this.$('#tts-position').textContent=this.current?(this.current.scope==='word'?'单词':`第 ${this.current.paragraph+1} 段 · 第 ${this.current.index+1} 句`):'';
    if(this.current)this.$('#tts-current-text').textContent=this.current.text;
    this.root.querySelector('[data-tts-action="toggle"] span').textContent=active?'朗读 · 播放中':'朗读';
    this.renderProgress();this.renderList();
  }

  stop() {
    this.version++;this.mediaEvents?.abort();this.audio.pause();this.audio.removeAttribute('src');this.audio.load();
    this.highlight(false);this.desired='paused';this.state='idle';this.sequence=[];this.position=0;this.activeItemId=null;
    this.render();this.status('已停止。选择范围可重新开始。');
  }

  async refreshList() {
    const result=await this.api('/tts/playlist');this.items=result.items;this.renderList();
  }

  renderList() {
    if(!this.panel)return;
    this.$('#tts-list-count').textContent=this.items.length;
    this.$('#tts-playlist-items').innerHTML=this.items.length?this.items.map((item,index)=>`<li class="${item.id===this.activeItemId?'tts-list-current':''}">
      <button class="tts-list-title" data-tts-item="${item.id}" ${item.id===this.activeItemId?'aria-current="true"':''}>${index+1}. ${this.escape(item.label)}<small>${this.escape(item.article_title || '单词朗读')}</small></button>
      <div class="tts-list-edit"><button data-tts-move="${item.id}" data-direction="-1" aria-label="上移第 ${index+1} 项" ${index===0?'disabled':''}>↑</button><button data-tts-move="${item.id}" data-direction="1" aria-label="下移第 ${index+1} 项" ${index===this.items.length-1?'disabled':''}>↓</button><button data-tts-remove="${item.id}" aria-label="移除第 ${index+1} 项">×</button></div></li>`).join(''):'<li class="tts-note">列表为空。可将单词、句子、段落或全文加入，按顺序连续播放。</li>';
  }

  async playItem(item) {
    if(!item)return;
    if(item.article_id && item.article_id!==this.article.id){
      this.stop();this.pendingItem=item;location.hash='reader/'+item.article_id;return;
    }
    this.activeItemId=item.id;this.$('#tts-scope').value=item.scope;this.$('#tts-word').hidden=item.scope!=='word';
    if(item.scope==='word')this.$('#tts-word').value=item.word;
    this.start(this.units(item));
  }

  async mutateList(url,options) {
    if(this.listBusy)return;
    this.listBusy=true;
    try{
      const result=await this.api(url,options);this.items=result.items;
      if(this.activeItemId && !this.items.some(i=>i.id===this.activeItemId))this.stop();
      this.renderList();return result;
    }finally{this.listBusy=false;}
  }

  renderCache(cache) {
    if(this.panel)this.$('#tts-cache-size').textContent=`${cache.files} 段 · ${(cache.bytes/1024/1024).toFixed(1)} / ${Math.round(cache.limit_bytes/1024/1024)} MB`;
  }

  async updateCacheStats() {
    try{this.renderCache(await this.api('/tts/cache'));}catch{/* A statistics failure does not interrupt playback. */}
  }

  async cacheArticle() {
    if(this.caching)return;
    this.caching=true;const version=++this.cacheVersion, mount=this.mountVersion, voice=this.settings.voice;
    const units=this.units({scope:'article'});
    this.$('[data-tts-action="cache-article"]').disabled=true;this.$('[data-tts-action="cancel-cache"]').hidden=false;
    try{
      for(let index=0;index<units.length;index++){
        if(version!==this.cacheVersion || mount!==this.mountVersion)return;
        this.$('#tts-cache-status').textContent=`正在缓存 ${index+1} / ${units.length} 句，可随时取消。`;
        await this.audioRequest(units[index],voice);
      }
      if(version===this.cacheVersion && this.panel)this.$('#tts-cache-status').textContent=`本文 ${units.length} 句已缓存（当前音色）。`;
    }catch(error){if(version===this.cacheVersion && this.panel)this.$('#tts-cache-status').textContent=error.message+' 已完成的部分会保留，可再次缓存。';}
    finally{
      if(mount===this.mountVersion && version===this.cacheVersion){
        this.caching=false;this.$('[data-tts-action="cache-article"]').disabled=false;this.$('[data-tts-action="cancel-cache"]').hidden=true;this.updateCacheStats();
      }
    }
  }

  cancelCache() {
    this.cacheVersion++;this.caching=false;
    if(this.panel){this.$('[data-tts-action="cache-article"]').disabled=false;this.$('[data-tts-action="cancel-cache"]').hidden=true;this.$('#tts-cache-status').textContent='已取消。当前正在生成的一句可能仍会保存，已缓存内容会保留。';}
  }

  onChange(event) {
    if(!this.panel?.contains(event.target))return;
    const target=event.target;
    if(target.id==='tts-scope'){this.$('#tts-word').hidden=target.value!=='word';return;}
    if(target.id==='tts-progress'){
      if(Number.isFinite(this.audio.duration))this.audio.currentTime=this.audio.duration*Number(target.value)/1000;return;
    }
    if(target.id==='tts-rate'){this.save({rate:Number(target.value)});return;}
    if(target.id==='tts-follow'){this.followSuspended=false;this.save({follow:target.checked});this.follow();return;}
    if(target.id==='tts-click'){this.save({click_to_play:target.checked});return;}
    if(target.id==='tts-loop'){this.save({loop:target.value});return;}
    if(['tts-accent','tts-voice'].includes(target.id)){
      const voice=target.id==='tts-accent'?this.voices.find(v=>v.accent===target.value)?.id:target.value;
      if(!voice)return;
      this.cancelCache();this.save({voice});
      if(this.current && this.state!=='complete')this.loadSegment(this.position);
    }
  }

  async onClick(event) {
    if(!this.root || !this.root.contains(event.target))return;
    const mount=this.mountVersion;
    const sentence=event.target.closest('.tts-sentence');
    if(sentence && this.clickToPlay && !window.getSelection()?.toString().trim() && !this.reader.ignoreWordTap()){
      if(!event.ctrlKey && !event.metaKey && !event.altKey)this.startAt(Number(sentence.dataset.sentenceIndex));return;
    }
    const button=event.target.closest('button');if(!button)return;
    try{
      if(button.dataset.ttsWord){this.show();this.$('#tts-scope').value='word';this.$('#tts-word').hidden=false;this.$('#tts-word').value=button.dataset.ttsWord;await this.startScope();return;}
      if(button.dataset.ttsItem){await this.playItem(this.items.find(i=>i.id===Number(button.dataset.ttsItem)));return;}
      if(button.dataset.ttsRemove){await this.mutateList('/tts/playlist/'+button.dataset.ttsRemove,{method:'DELETE',body:{}});return;}
      if(button.dataset.ttsMove){
        const ids=this.items.map(i=>i.id),index=ids.indexOf(Number(button.dataset.ttsMove)),target=index+Number(button.dataset.direction);
        if(target<0 || target>=ids.length)return;
        [ids[index],ids[target]]=[ids[target],ids[index]];
        await this.mutateList('/tts/playlist/order',{method:'PATCH',body:{ids}});return;
      }
      switch(button.dataset.ttsAction){
        case 'toggle': if(this.panel.hidden)this.show();else this.hide();break;
        case 'hide': this.hide();break;
        case 'play': this.togglePlay();break;
        case 'stop': this.stop();break;
        case 'start': await this.startScope();break;
        case 'previous': if(this.position>0){this.desired='playing';this.loadSegment(this.position-1);}break;
        case 'next': if(this.current)await this.advance(true);break;
        case 'locate': this.follow(true);break;
        case 'add':{
          const result=await this.mutateList('/tts/playlist',{method:'POST',body:this.scopeRequest()});
          if(result)this.status(result.duplicate?'列表中已有这项内容。':'已加入播放列表，重启后仍会保留。');break;
        }
        case 'playlist': await this.refreshList();if(mount!==this.mountVersion || !this.panel)return;if(this.items.length)await this.playItem(this.items[0]);else this.status('请先将内容加入列表。');break;
        case 'refresh-list': await this.refreshList();break;
        case 'clear-list': this.$('#tts-clear-list-confirm').hidden=false;break;
        case 'cancel-clear-list': this.$('#tts-clear-list-confirm').hidden=true;break;
        case 'confirm-clear-list': await this.mutateList('/tts/playlist',{method:'DELETE',body:{}});if(this.panel)this.$('#tts-clear-list-confirm').hidden=true;break;
        case 'cache-article': await this.cacheArticle();break;
        case 'cancel-cache': this.cancelCache();break;
        case 'clear-cache': this.$('#tts-clear-cache-confirm').hidden=false;break;
        case 'cancel-clear-cache': this.$('#tts-clear-cache-confirm').hidden=true;break;
        case 'confirm-clear-cache':{
          this.cancelCache();this.stop();this.requests.clear();
          const result=await this.api('/tts/cache',{method:'DELETE',body:{}});
          if(this.panel){this.renderCache(result);this.$('#tts-clear-cache-confirm').hidden=true;this.$('#tts-cache-status').textContent=result.files?'部分正在读取的音频已保留，可稍后再清理。':'音频缓存已清理，播放列表保留。';}break;
        }
      }
    }catch(error){this.status(error.message);}
  }

  unmount() {
    if(!this.root)return;
    const handoff=this.pendingItem && location.hash==='#reader/'+this.pendingItem.article_id?this.pendingItem:null;
    this.stop();this.cancelCache();this.mountEvents?.abort();this.mountVersion++;
    this.pendingItem=handoff;this.root=this.panel=this.reader=this.article=null;
  }
};
