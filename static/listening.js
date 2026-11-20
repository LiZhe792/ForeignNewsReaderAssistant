/* Listening and speaking practice. Recording begins only after a user action. */
'use strict';
(() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names={dictation:'逐句听写',keywords:'关键词填空',readalong:'跟读录音',shadow:'影子跟读'};
  const btn=(action,label,attributes='')=>`<button type="button" class="button small" data-listen="${action}" ${attributes}>${label}</button>`;
  const date=value=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value));
  class Listening {
    constructor({root,api,toast,articleId}){
      Object.assign(this,{root,api,toast,articleId});this.abort=new AbortController();this.drafts=new Map();this.mode='dictation';this.index=0;this.paragraph=0;this.end=0;this.blind=true;this.rate=1;this.repeats=3;this.gap=1;this.tab='practice';this.message='';this.recordState='idle';this.voices=[];
      for(const type of ['click','change','input'])root.addEventListener(type,e=>this.handle(e),{signal:this.abort.signal});
      document.addEventListener('visibilitychange',()=>{if(document.hidden){this.stopPlayback();this.stopRecording();}},{signal:this.abort.signal});
      window.addEventListener('beforeunload',e=>{if([...this.drafts.values()].some(d=>d.blob&&!d.saved)){e.preventDefault();e.returnValue='';}},{signal:this.abort.signal});
      this.voiceListener=()=>this.updateVoices();window.speechSynthesis?.addEventListener?.('voiceschanged',this.voiceListener);
    }
    async request(path,method='GET',body){return this.api('/training'+path,{method,body,signal:this.abort.signal});}
    key(){return `${this.manifest?.article.hash}:${this.paragraph}:${this.index}:${this.mode}`;}
    draft(){const key=this.key();if(!this.drafts.has(key))this.drafts.set(key,{attempt:'',answers:[],started:performance.now(),blob:null,url:null,duration:0,voiced:0,pauses:0,analysis:false,saved:false,result:null,pending:null,revision:0});return this.drafts.get(key);}
    async render(){this.articles=(await this.api('/articles',{signal:this.abort.signal})).articles;if(this.dead)return;this.articleId=Number(this.articleId)||this.articles[0]?.id;if(this.articleId)await this.load();else this.draw();}
    async load(preserve=false){const oldIndex=this.index,oldEnd=this.end;const generation=this.loadGeneration=(this.loadGeneration||0)+1;this.stopPlayback();const data=await this.request(`/articles/${this.articleId}?paragraph=${this.paragraph}`);if(this.dead||generation!==this.loadGeneration)return;this.manifest=data;this.index=preserve?Math.min(oldIndex,data.units.length-1):0;this.end=preserve?Math.max(this.index,Math.min(oldEnd,data.units.length-1)):0;this.draw();this.updateVoices();}
    destroy(){this.dead=true;this.abort.abort();this.stopPlayback();this.stopRecording();window.speechSynthesis?.removeEventListener?.('voiceschanged',this.voiceListener);for(const d of this.drafts.values())if(d.url)URL.revokeObjectURL(d.url);}
    draw(){
      if(this.dead)return;const m=this.manifest;
      this.root.innerHTML=`<div class="page-wrap practice-page"><div class="page-intro"><div><span class="eyebrow">LISTEN, REPEAT, FIND YOUR VOICE</span><h1>把读懂的英语，听见、说出来。</h1><p>逐句回想，录下自己的声音，再回到原文核对。</p></div><a class="button" href="#exercises">阅读测验</a></div><div class="practice-tabs">${btn('practice','听说练习',`aria-pressed="${this.tab==='practice'}"`)}${btn('history','学习记录',`aria-pressed="${this.tab==='history'}"`)}</div><p id="listen-message" class="practice-message" role="status">${esc(this.message)}</p><div id="listen-main"></div></div>`;
      if(this.tab==='history'){this.showHistory().catch(error=>{if(!this.dead&&this.tab==='history'){this.message=error.message;this.root.querySelector('#listen-message').textContent=error.message;}});return;}
      if(!m){this.root.querySelector('#listen-main').innerHTML='<div class="empty-state"><h2>先导入一篇英文文章</h2><a class="button primary" href="#library">返回文章库</a></div>';return;}
      const d=this.draft(),recording=this.recordState!=='idle',pending=Boolean(d.pending),busy=recording||this.saving||pending;
      this.root.querySelector('#listen-main').innerHTML=`<div class="practice-panel"><fieldset id="listen-controls" class="practice-fieldset" ${busy?'disabled':''}><div class="practice-grid"><label>练习文章<select id="listen-article">${this.articles.map(a=>`<option value="${a.id}" ${a.id===this.articleId?'selected':''}>${esc(a.title)}</option>`).join('')}</select></label><label>段落<select id="listen-paragraph">${m.paragraphs.map(p=>`<option value="${p.index}" ${p.index===this.paragraph?'selected':''}>第 ${p.index+1} 段 · ${esc(p.preview.slice(0,45))}</option>`).join('')}</select></label><label>练习模式<select id="listen-mode">${Object.entries(names).map(([key,name])=>`<option value="${key}" ${key===this.mode?'selected':''}>${name}</option>`).join('')}</select></label></div><div class="practice-grid"><label>当前原句 / 循环起点<select id="listen-unit">${m.units.map((u,i)=>`<option value="${i}" ${i===this.index?'selected':''}>第 ${i+1} 句${this.blind?'':' · '+esc(u.text.slice(0,35))}</option>`).join('')}</select></label><label>循环终点<select id="listen-end">${m.units.map((u,i)=>`<option value="${i}" ${i===this.end?'selected':''} ${i<this.index?'disabled':''}>第 ${i+1} 句</option>`).join('')}</select></label><label>英文语音<select id="listen-voice"><option value="">本机英文语音</option></select></label></div><div class="practice-grid"><label>语速 <output id="listen-rate-value">${this.rate.toFixed(1)}×</output><input id="listen-rate" type="range" min="0.6" max="1.5" step="0.1" value="${this.rate}"></label><label>循环次数（1–10）<input id="listen-repeats" type="number" min="1" max="10" value="${this.repeats}"></label><label>句间停顿（秒）<input id="listen-gap" type="number" min="0" max="10" value="${this.gap}"></label></div></fieldset><div class="practice-toolbar">${btn('play','播放当前句',busy?'disabled':'')}${btn('loop','精听循环',busy?'disabled':'')}${btn('stop','停止播放')}<span id="listen-playing" role="status"></span></div><label class="practice-check"><input id="listen-blind" type="checkbox" ${this.blind?'checked':''}>盲听 · 隐藏原文（可随时切换）</label><p id="listen-source" class="practice-source" lang="en" ${this.blind?'hidden':''}>${esc(m.units[this.index]?.text)}</p><details class="practice-reference"><summary>为当前句上传原音（可选）</summary><p>已有原音优先播放；否则使用本机英文语音。请上传与当前句对应的音频，最多 6 MB。</p><input id="listen-reference" type="file" accept="audio/webm,audio/ogg,audio/wav,audio/mpeg,audio/mp4,.m4a" ${busy?'disabled':''}>${m.units[this.index]?.reference?'<p>这句已有保存的原音。</p>':''}</details></div>
      <section class="practice-panel"><h2>${names[this.mode]}</h2><fieldset class="practice-fieldset" id="listen-draft" ${pending||this.saving?'disabled':''}>${this.mode==='dictation'?`<label>写下听到的句子<textarea id="listen-attempt" rows="4" maxlength="4000" placeholder="先听，再写；提交后对照差异。">${esc(d.attempt)}</textarea></label>`:this.mode==='keywords'?`<p class="practice-gap-source" lang="en">${m.units[this.index].keyword_parts.map((part,i)=>esc(part)+(i<m.units[this.index].gap_count?`<input data-listen-gap="${i}" aria-label="第 ${i+1} 个关键词" maxlength="100" autocomplete="off" value="${esc(d.answers[i]||'')}">`:'')).join('')}</p>`:`<p class="practice-hint">录音先留在本页预览，点击保存才写入学习记录；离开前请保存或下载。最多录音 2 分钟。</p><label class="practice-check"><input id="listen-asr" type="checkbox" ${this.recognitionEnabled?'checked':''} ${busy?'disabled':''}>同时识别英文（可选；浏览器可能把音频发送给其识别服务）</label>${this.mode==='shadow'?`<label class="practice-check"><input id="listen-headphones" type="checkbox" ${this.headphones?'checked':''} ${busy?'disabled':''}>已戴耳机，避免原音进入录音</label><p class="practice-hint">影子跟读时跟在原音后同步说出内容；完成后停止录音，分别回放原音与自己的声音。</p>`:''}<div class="practice-toolbar">${btn('record',this.mode==='shadow'?'开始影子跟读录音':'开始跟读录音',busy?'disabled':'')}${btn('record-stop','停止录音',this.recordState==='recording'?'':'disabled')}${this.recordState==='requesting'?btn('record-cancel','取消等待麦克风'):''}<span id="listen-record-status" role="status">${this.recordState==='recording'?'正在录音…':this.recordState==='requesting'?'正在等待麦克风许可…':'麦克风未开启'}</span></div><label>或选择本地跟读录音<input id="listen-record-file" type="file" accept="audio/webm,audio/ogg,audio/wav,audio/mpeg,audio/mp4,.m4a" ${busy?'disabled':''}></label><div id="listen-record-preview">${this.recordPreview(d)}</div><label>识别文字 / 手动核对文本<textarea id="listen-attempt" maxlength="4000" rows="3" ${busy?'readonly':''} placeholder="无语音识别时可手动填写；不填写也可保存录音回放。">${esc(d.attempt)}</textarea></label><p class="practice-hint">识别差异可帮助定位需复听的词，不等同专业声学发音评分。语速与停顿为本机估算。</p>`}</fieldset><div class="practice-toolbar">${btn('save',pending?'重试保存本次记录':'保存并查看反馈',recording||this.saving?'disabled':'')}${btn('download','下载本页录音',d.blob?'':'disabled')}</div><div id="listen-feedback">${d.result?this.feedbackMarkup(d.result):''}</div></section>`;
    }
    recordPreview(d){return d.url?`<audio controls preload="metadata" src="${esc(d.url)}"></audio><p class="practice-hint">${d.duration?'录音 '+(d.duration/1000).toFixed(1)+' 秒':'上传录音 · 时长由播放器显示'} · ${d.saved?'已保存':'尚未保存'}</p>`:'';}
    updateVoices(){if(this.dead)return;this.voices=(window.speechSynthesis?.getVoices()||[]).filter(v=>/^en[-_]/i.test(v.lang)&&v.localService!==false);const select=this.root.querySelector('#listen-voice');if(select)select.innerHTML='<option value="">本机英文语音</option>'+this.voices.map((v,i)=>`<option value="${i}" ${String(this.voiceIndex)===String(i)?'selected':''}>${esc(v.name)} · ${esc(v.lang)}</option>`).join('');}
    stopPlayback(){this.playGeneration=(this.playGeneration||0)+1;clearTimeout(this.playTimer);this.referenceAudio?.pause();this.referenceAudio=null;if(this.utterance)window.speechSynthesis?.cancel();this.utterance=null;this.playing=false;const status=this.root.querySelector('#listen-playing');if(status)status.textContent='播放已停止。';}
    async play(loop=false,shadow=false){
      this.stopPlayback();const generation=this.playGeneration,start=this.index,end=loop?this.end:this.index,repeats=loop?this.repeats:1;
      if(!Number.isInteger(repeats)||repeats<1||repeats>10||!Number.isFinite(this.gap)||this.gap<0||this.gap>10)throw new Error('循环次数限 1–10，句间停顿限 0–10 秒。');
      let index=start,round=1;this.playing=true;
      const active=()=>!this.dead&&generation===this.playGeneration;
      const next=()=>{if(!active())return;if(index<end)index++;else if(round<repeats){round++;index=start;}else{this.playing=false;this.root.querySelector('#listen-playing').textContent='本次播放已完成。';return;}this.playTimer=setTimeout(speak,this.gap*1000);};
      const failure=message=>{if(!active())return;this.playing=false;this.root.querySelector('#listen-playing').textContent=message;};
      const speak=()=>{
        if(!active())return;const unit=this.manifest.units[index];this.root.querySelector('#listen-playing').textContent=`第 ${index+1} 句 · 第 ${round}/${repeats} 遍${shadow?' · 请跟在原音后说':''}`;
        if(unit.reference){const audio=this.referenceAudio=new Audio('/api/training/audio/'+unit.reference.id);audio.playbackRate=this.rate;audio.onended=next;audio.onerror=()=>failure('原音无法播放，可重新上传音频或选择其他句子。');audio.play().catch(()=>failure('播放被中断，请再次点击播放。'));}
        else{this.updateVoices();if(!window.speechSynthesis||!window.SpeechSynthesisUtterance||!this.voices.length){failure('没有可用的本机英文语音，请为当前句上传原音或稍后重试。');return;}const voice=this.voices[Number(this.voiceIndex)]||this.voices[0];const utterance=this.utterance=new SpeechSynthesisUtterance(unit.text);utterance.voice=voice;utterance.lang=voice.lang;utterance.rate=this.rate;utterance.onend=next;utterance.onerror=()=>failure('英文语音播放失败，请重试或上传原音。');window.speechSynthesis.speak(utterance);}
      };speak();
    }
    async beginRecording(){
      if(this.recordState!=='idle')return;if(this.mode==='shadow'&&!this.headphones)throw new Error('影子跟读请先戴耳机并勾选确认。');
      if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw new Error('当前环境不支持录音，请使用 localhost / HTTPS 或选择本地录音文件。');
      this.stopPlayback();const generation=this.micGeneration=(this.micGeneration||0)+1,d=this.draft();this.recordState='requesting';this.draw();
      let stream;
      try{
        stream=await navigator.mediaDevices.getUserMedia({audio:true});
        if(this.dead||generation!==this.micGeneration){stream.getTracks().forEach(t=>t.stop());return;}
        const mime=['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4'].find(t=>MediaRecorder.isTypeSupported?.(t));
        const recorder=new MediaRecorder(stream,mime?{mimeType:mime,audioBitsPerSecond:64000}:undefined);
        const session=this.recordSession={stream,recorder,draft:d,chunks:[],size:0,start:performance.now(),voiced:0,pauses:0,frames:0,lastVoice:0,voiceEnded:false};
        recorder.ondataavailable=e=>{if(e.data.size){session.size+=e.data.size;session.chunks.push(e.data);if(session.size>6*1024*1024){this.message='录音达到 6 MB 上限，已停止。';this.stopRecording();}}};
        recorder.onerror=()=>{this.message='录音设备发生错误，请检查后重试。';this.stopRecording();};
        recorder.onstop=()=>{
          if(this.dead||session!==this.recordSession)return;this.recordState='idle';
          if(session.size&&session.size<=6*1024*1024){if(d.url)URL.revokeObjectURL(d.url);d.blob=new Blob(session.chunks,{type:(recorder.mimeType||session.chunks[0]?.type||'audio/webm').split(';')[0]});d.url=URL.createObjectURL(d.blob);d.duration=Math.min(120000,Math.max(1,Math.round(performance.now()-session.start)));d.voiced=Math.min(d.duration,session.voiced);d.pauses=session.pauses;d.analysis=session.frames>0;d.saved=false;d.result=null;d.pending=null;}
          if(this.tab==='practice')this.draw();
        };
        recorder.start(200);this.recordState='recording';this.monitor(session);if(this.recognitionEnabled)this.recognize(d,session);
        this.recordTimer=setTimeout(()=>{this.message='已达到 2 分钟录音上限。';this.stopRecording();},120000);this.draw();if(this.mode==='shadow')await this.play(false,true);
      }catch(error){stream?.getTracks().forEach(t=>t.stop());if(this.dead||generation!==this.micGeneration)return;this.recordState='idle';this.message=error.name==='NotAllowedError'?'麦克风许可未开启，可在浏览器设置中调整或选择本地录音。':error.name==='NotFoundError'?'没有检测到麦克风，可选择本地录音。':error.message||'无法开始录音。';this.draw();}
    }
    monitor(session){
      try{const Context=window.AudioContext||window.webkitAudioContext;if(!Context)return;const context=session.context=new Context(),analyser=context.createAnalyser();analyser.fftSize=1024;context.createMediaStreamSource(session.stream).connect(analyser);context.resume().catch(()=>{});const samples=new Float32Array(analyser.fftSize);
        this.meterTimer=setInterval(()=>{if(this.recordState!=='recording'||context.state!=='running')return;session.frames++;analyser.getFloatTimeDomainData(samples);const rms=Math.sqrt(samples.reduce((sum,v)=>sum+v*v,0)/samples.length),current=performance.now();if(rms>0.015){session.voiced+=100;session.lastVoice=current;session.voiceEnded=false;}else if(session.lastVoice&&current-session.lastVoice>=500&&!session.voiceEnded){session.pauses++;session.voiceEnded=true;}const status=this.root.querySelector('#listen-record-status');if(status)status.textContent=`正在录音 · ${Math.floor((current-session.start)/1000)} 秒`;},100);
      }catch{this.message='录音已开始；本机语速与停顿估算暂不可用。';}
    }
    recognize(d,session){
      const Constructor=window.SpeechRecognition||window.webkitSpeechRecognition;if(!Constructor){this.message='此浏览器没有英文语音识别；录音仍可保存，之后可手动填写文本。';return;}
      const recognition=this.recognition=new Constructor();const revision=d.revision;recognition.lang='en-US';recognition.continuous=true;recognition.interimResults=true;
      recognition.onresult=event=>{if(this.dead||session!==this.recordSession||d.revision!==revision)return;const fragments=[];for(let i=0;i<event.results.length;i++)fragments.push(event.results[i][0]?.transcript||'');d.attempt=fragments.join(' ').trim();const input=this.root.querySelector('#listen-attempt');if(input&&this.draft()===d)input.value=d.attempt;};
      recognition.onerror=event=>{if(this.dead||session!==this.recordSession)return;this.message=`英文识别未完成（${event.error||'服务不可用'}）；录音保留，可手动核对文本。`;const message=this.root.querySelector('#listen-message');if(message)message.textContent=this.message;};
      try{recognition.start();}catch{this.message='英文识别未能启动；录音仍可保存。';}
    }
    stopRecording(){
      this.micGeneration=(this.micGeneration||0)+1;clearTimeout(this.recordTimer);clearInterval(this.meterTimer);const session=this.recordSession;
      if(session){if(session.recorder.state!=='inactive'){try{session.recorder.stop();}catch{}}session.stream.getTracks().forEach(t=>t.stop());session.context?.close().catch(()=>{});}
      try{this.recognition?.stop();}catch{}this.recognition=null;
      if(this.recordState==='requesting'){this.recordState='idle';if(!this.dead)this.draw();}
    }
    async readFile(file){if(!file||file.size>6*1024*1024)throw new Error('请选择最多 6 MB 的音频。');const bytes=new Uint8Array(await file.arrayBuffer());let raw='';for(let i=0;i<bytes.length;i+=16384)raw+=String.fromCharCode(...bytes.subarray(i,i+16384));let mime=file.type.split(';')[0];if(mime==='audio/x-wav')mime='audio/wav';if(mime==='audio/x-m4a')mime='audio/mp4';if(!mime){const extension=file.name.split('.').pop().toLowerCase();mime=({mp3:'audio/mpeg',wav:'audio/wav',webm:'audio/webm',ogg:'audio/ogg',m4a:'audio/mp4'})[extension]||'';}return {audio_base64:btoa(raw),mime};}
    coordinates(){return {article_id:this.articleId,article_hash:this.manifest.article.hash,paragraph_index:this.paragraph,unit_index:this.index};}
    async save(){
      if(this.saving||this.recordState!=='idle')return;const d=this.draft(),coordinates=this.coordinates(),mode=this.mode,gapCount=this.manifest.units[this.index].gap_count;this.saving=true;this.draw();
      try{if(!d.pending){const media=d.blob?await this.readFile(new File([d.blob],'recording',{type:d.blob.type})):{};d.pending={...coordinates,mode,attempt:d.attempt,answers:Array.from({length:gapCount},(_,i)=>d.answers[i]||''),request_id:crypto.randomUUID(),duration_ms:d.blob?d.duration:Math.min(3600000,Math.round(performance.now()-d.started)),voiced_ms:d.voiced,pause_count:d.pauses,analysis_available:d.analysis,...media};}this.draw();const result=await this.request('/attempts','POST',d.pending);if(this.dead)return;d.pending=null;d.result=result.attempt;d.saved=true;this.message='练习记录已保存。';}
      catch(error){if(this.dead)return;if(error.status&&error.status<500)d.pending=null;this.message=error.message;}
      finally{this.saving=false;if(!this.dead){this.draw();if(d.result&&this.tab==='practice'&&this.draft()===d)this.root.querySelector('#listen-feedback')?.scrollIntoView({block:'start',behavior:'instant'});}}
    }
    feedbackMarkup(record){const f=record.feedback;return `<div class="practice-feedback"><h3>${f.available?'原文与作答对比':'录音已保存，可回放核对'}</h3>${record.attempt?`<p class="practice-attempt-text">本次作答 / 核对文字：${esc(record.attempt)}</p>`:''}${f.available?`<p>词序匹配参考 ${f.accuracy}% · 匹配 ${f.matched_words}/${f.expected_words} 词</p><div class="practice-diff">${f.operations.map(op=>`<div class="diff-${op.kind}"><span>${esc(op.expected)||'（新增）'}</span><span>${esc(op.actual)||'（未读出 / 未写出）'}</span></div>`).join('')}</div>`:''}${record.mode==='readalong'||record.mode==='shadow'?`<p>估算发声语速：${f.speech_rate===null?'无可用数据':f.speech_rate+' 词/分钟'} · 停顿占比：${f.pause_ratio===null?'无可用数据':Math.round(f.pause_ratio*100)+'%'} · 明显停顿 ${f.pause_count} 次</p>`:''}${record.audio_size?`<audio controls preload="none" src="/api/training/attempts/${record.id}/audio"></audio>`:''}<p class="practice-source" lang="en">${esc(record.source_text)}</p>${f.notes.map(n=>`<p class="practice-hint">${esc(n)}</p>`).join('')}</div>`;}
    async showHistory(){const result=await this.request('/attempts');if(this.dead||this.tab!=='history')return;this.root.querySelector('#listen-main').innerHTML=`<p class="practice-hint">最近 100 条，累计 ${result.total} 条。录音与练习文字保存在本机。</p>${result.attempts.map(record=>`<details class="practice-panel"><summary>${esc(names[record.mode])} · ${esc(record.article_title)} · ${date(record.created_at)}</summary>${this.feedbackMarkup(record)}${btn('history-original','朗读这条原句',`data-id="${record.id}"`)}</details>`).join('')||'<div class="empty-state"><h2>还没有听说学习记录</h2><p>完成一句听写或保存一次录音，记录就会出现在这里。</p></div>'}`;this.history=result.attempts;}
    async handle(event){
      const input=event.target;
      if(event.type==='input'){
        if(input.id==='listen-attempt'){const d=this.draft();d.attempt=input.value;d.revision++;d.saved=false;}
        else if(input.dataset.listenGap!==undefined){this.draft().answers[Number(input.dataset.listenGap)]=input.value;this.draft().saved=false;}
        else if(input.id==='listen-rate'){this.rate=Number(input.value);this.root.querySelector('#listen-rate-value').textContent=this.rate.toFixed(1)+'×';}return;
      }
      try{
        if(event.type==='change'){
          if(input.id==='listen-article'){this.articleId=Number(input.value);this.paragraph=0;await this.load();}
          else if(input.id==='listen-paragraph'){this.paragraph=Number(input.value);await this.load();}
          else if(input.id==='listen-mode'){this.stopPlayback();this.mode=input.value;this.blind=this.mode==='dictation'||this.mode==='keywords';this.draw();this.updateVoices();}
          else if(input.id==='listen-unit'){this.stopPlayback();this.index=Number(input.value);this.end=Math.max(this.index,this.end);this.draw();this.updateVoices();}
          else if(input.id==='listen-end')this.end=Number(input.value);
          else if(input.id==='listen-repeats')this.repeats=Number(input.value);
          else if(input.id==='listen-gap')this.gap=Number(input.value);
          else if(input.id==='listen-voice')this.voiceIndex=input.value;
          else if(input.id==='listen-blind'){this.blind=input.checked;this.root.querySelector('#listen-source').hidden=this.blind;}
          else if(input.id==='listen-asr')this.recognitionEnabled=input.checked;
          else if(input.id==='listen-headphones')this.headphones=input.checked;
          else if(input.id==='listen-reference'){const coords=this.coordinates(),key=this.key(),version=this.manifest.units[this.index].reference?.version||0,media=await this.readFile(input.files[0]);await this.request('/reference','POST',{...coords,...media,version});if(!this.dead&&key===this.key()){this.message='当前句原音已保存。';await this.load(true);}}
          else if(input.id==='listen-record-file'){const d=this.draft(),file=input.files[0],media=await this.readFile(file);if(this.dead)return;if(d.url)URL.revokeObjectURL(d.url);d.blob=new Blob([file],{type:media.mime});d.url=URL.createObjectURL(d.blob);d.duration=0;d.voiced=0;d.pauses=0;d.analysis=false;d.saved=false;d.result=null;this.draw();}
          return;
        }
        if(event.type!=='click')return;const button=input.closest('[data-listen]');if(!button)return;event.preventDefault();event.stopPropagation();const action=button.dataset.listen;
        if(action==='play'||action==='loop')await this.play(action==='loop');
        else if(action==='stop')this.stopPlayback();
        else if(action==='record')await this.beginRecording();
        else if(action==='record-stop'||action==='record-cancel')this.stopRecording();
        else if(action==='save')await this.save();
        else if(action==='download'){const d=this.draft();if(d.url){const a=document.createElement('a');a.href=d.url;a.download='margin-recording.'+({ 'audio/webm':'webm','audio/mp4':'m4a','audio/ogg':'ogg','audio/mpeg':'mp3','audio/wav':'wav'}[d.blob.type]||'audio');a.click();}}
        else if(action==='history'||action==='practice'){this.stopPlayback();this.stopRecording();this.tab=action;this.draw();if(action==='practice')this.updateVoices();}
        else if(action==='history-original'){this.stopPlayback();const record=this.history.find(r=>String(r.id)===button.dataset.id);this.updateVoices();if(!this.voices.length)throw new Error('未检测到本机英文语音。');const utterance=this.utterance=new SpeechSynthesisUtterance(record.source_text);utterance.voice=this.voices[0];utterance.lang=this.voices[0].lang;window.speechSynthesis.speak(utterance);}
      }catch(error){if(!this.dead){this.message=error.message;const status=this.root.querySelector('#listen-message');if(status)status.textContent=this.message;}}
    }
  }
  window.MarginListening=Listening;
})();
