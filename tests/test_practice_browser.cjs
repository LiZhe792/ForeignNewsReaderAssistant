/* Real Flask/SQLite/Chromium. Microphone, recognition and AI use explicit test inputs. */
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
const root=path.join(__dirname,'..');let directory,server,origin,options;
function wav(){const out=Buffer.alloc(1644);out.write('RIFF');out.writeUInt32LE(out.length-8,4);out.write('WAVEfmt ',8);out.writeUInt32LE(16,16);out.writeUInt16LE(1,20);out.writeUInt16LE(1,22);out.writeUInt32LE(8000,24);out.writeUInt32LE(16000,28);out.writeUInt16LE(2,32);out.writeUInt16LE(16,34);out.write('data',36);out.writeUInt32LE(1600,40);return out;}
before(async()=>{
  directory=fs.mkdtempSync(path.join(os.tmpdir(),'margin-practice-'));const local=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  server=spawn(process.env.READING_TEST_PYTHON||(fs.existsSync(local)?local:'python'),['-u','-c',`
import sys,json
from datetime import datetime,timedelta
from flask import request,jsonify
from werkzeug.serving import make_server
from app import create_app
from newsreader import ai,exercises
from newsreader.db import get_db,CHINA_TZ
shift=0
exercises.clock=lambda: datetime.now(CHINA_TZ).replace(microsecond=0)+timedelta(seconds=shift)
def synthetic_questions(payload,config):
    material=json.loads(payload['input'])
    content='\\n\\n'.join(material['paragraphs'])
    bank=next(item for item in exercises.BANK['articles'] if item['article_hash']==exercises.digest(content))
    return {'questions':bank['questions']}
ai.request_json=synthetic_questions
app=create_app({'DATABASE':sys.argv[1],'AI_API_KEY':'synthetic-test-only-key','AI_MODEL':'test-model'})
@app.post('/__test/reset')
def reset():
    global shift
    shift=0
    db=get_db()
    for table in ['reading_sessions','reading_mistakes','reading_sets','listening_attempts','listening_audio']:
        db.execute('DELETE FROM '+table)
    db.execute('DELETE FROM articles WHERE is_demo=0')
    db.commit()
    return jsonify(ok=True)
@app.post('/__test/advance')
def advance():
    global shift
    shift+=request.json['seconds']
    return jsonify(ok=True)
@app.get('/__test/expected/<int:identity>')
def expected(identity):
    row=get_db().execute('SELECT snapshot_json FROM reading_sessions WHERE id=?',(identity,)).fetchone()
    return jsonify(json.loads(row[0]))
server=make_server('127.0.0.1',0,app,threaded=True)
print('PORT:'+str(server.server_port),flush=True)
server.serve_forever()
`,path.join(directory,'practice.sqlite3')],{cwd:root,stdio:['ignore','pipe','pipe']});
  let output='',errors='';server.stderr.on('data',b=>errors=(errors+b).slice(-6000));
  origin=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error(errors)),15000);server.once('exit',c=>{clearTimeout(timer);reject(new Error('Server '+c+': '+errors));});server.stdout.on('data',b=>{output+=b;const match=output.match(/PORT:(\d+)/);if(match){clearTimeout(timer);resolve('http://127.0.0.1:'+match[1]);}});});
  options={headless:true};if(process.env.READING_TEST_CHROMIUM_MODULE){const {default:runtime}=await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);options={...options,executablePath:await runtime.executablePath(),args:runtime.args};}
});
after(async()=>{if(server&&server.exitCode===null){const exit=new Promise(r=>server.once('exit',r));server.kill();await exit;}if(directory)fs.rmSync(directory,{recursive:true,force:true});});
async function speechFixture(page,{noVoices=false,deny=false,noRecorder=false}={}){
  await page.addInitScript(({noVoices,deny,noRecorder})=>{
    window.__spoken=[];window.__cancelled=0;window.__gumCalls=0;window.__stopped=0;window.__streams=[];let timer;
    class Utterance{constructor(text){this.text=text;}}
    Object.defineProperty(window,'SpeechSynthesisUtterance',{value:Utterance,configurable:true});
    Object.defineProperty(window,'speechSynthesis',{configurable:true,value:{getVoices:()=>noVoices?[]:[{name:'Controlled test English',lang:'en-US'}],addEventListener(){},removeEventListener(){},cancel(){window.__cancelled++;clearTimeout(timer);},speak(u){window.__spoken.push(u.text);timer=setTimeout(()=>u.onend?.({}),45);}}});
    class Recognition{start(){const target=window.__recognitionText||'A controlled English recording.';setTimeout(()=>this.onresult?.({results:[Object.assign([{transcript:target}],{isFinal:true})]}),30);}stop(){this.onend?.({});}}
    Object.defineProperty(window,'SpeechRecognition',{value:Recognition,configurable:true});
    window.__makeStream=async()=>{const context=new AudioContext();await context.resume();const oscillator=context.createOscillator(),gain=context.createGain(),destination=context.createMediaStreamDestination();gain.gain.value=.1;oscillator.connect(gain).connect(destination);oscillator.start();const stream=destination.stream;window.__streams.push(stream);for(const track of stream.getTracks()){const stop=track.stop.bind(track);track.stop=()=>{window.__stopped++;stop();context.close().catch(()=>{});};}return stream;};
    Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{window.__gumCalls++;if(deny)throw new DOMException('Denied by test','NotAllowedError');return window.__makeStream();}}});
    if(noRecorder)Object.defineProperty(window,'MediaRecorder',{value:undefined,configurable:true});
  },{noVoices,deny,noRecorder});
}
async function fixture(t,route='listening/1',config={}){
  const browser=await chromium.launch(options);t.after(()=>browser.close());const context=await browser.newContext({viewport:config.viewport||{width:1440,height:1000}});
  const api=async(endpoint,method='GET',data)=>{const response=await context.request.fetch(origin+'/api'+endpoint,{method,data});assert.equal(response.ok(),true,await response.text());return response.json();};
  await context.request.post(origin+'/__test/reset',{data:{}});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));await speechFixture(page,config);await page.goto(origin+'/#'+route);await page.locator(route.startsWith('listening')?'#listen-main':'[data-reading="start-source"]').waitFor();return{page,context,api};
}
const listen=(p,a)=>p.locator(`[data-listen="${a}"]`);const reading=(p,a)=>p.locator(`[data-reading="${a}"]`);
async function start(page,kind='source'){await reading(page,'start-'+kind).click();await page.locator('#reading-work').waitFor();}
async function expected(context,page){const match=page.url().match(/session\/(\d+)/);assert.ok(match,'session route absent');const response=await context.request.get(origin+'/__test/expected/'+match[1]);return response.json();}
async function fillCorrect(page,context){const snapshot=await expected(context,page);for(const q of snapshot.questions){if(q.type==='cloze'){for(let i=0;i<q.answer.length;i++)await page.locator(`[data-reading-gap="${q.id}"][data-index="${i}"]`).fill(q.answer[i]);}else if(q.type==='order'){const block=page.locator(`[data-question-id="${q.id}"]`);for(let position=0;position<q.answer.length;position++){let texts=await block.locator('.reading-order li > span').allTextContents(),from=texts.indexOf(q.options[Number(q.answer[position])]);while(from>position){await block.locator(`[data-reading="order-up"][data-index="${from}"]`).click();from--;}}await block.locator('[data-reading="order-confirm"]').click();}else await page.locator(`[data-reading-choice="${q.id}"][value="${q.answer[0]}"]`).check();}return snapshot;}
async function saved(page){await page.waitForFunction(()=>document.querySelector('#reading-save-status')?.textContent==='作答已保存');}
async function screenshot(page,name){if(process.env.READING_TEST_SCREENSHOTS){fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,name+'.png')});}}

test('blind dictation toggles source, shows word differences and persists history',async t=>{
  const {page,api}=await fixture(t);assert.equal(await page.locator('#listen-source').isVisible(),false);await page.locator('#listen-attempt').fill('At a station after sunset.');await page.locator('#listen-blind').uncheck();assert.match(await page.locator('#listen-source').innerText(),/At a station/);assert.equal(await page.locator('#listen-attempt').inputValue(),'At a station after sunset.');await listen(page,'save').click();await page.locator('.practice-feedback').waitFor();assert.match(await page.locator('#listen-feedback').innerText(),/词序匹配参考/);assert.equal((await api('/training/attempts')).attempts.length,1);await page.reload();await listen(page,'history').click();await page.locator('#listen-main details').first().click();assert.match(await page.locator('#listen-main').innerText(),/At a station after sunset/);
});

test('keyword fields submit the source-derived blanks and reveal feedback after save',async t=>{
  const {page,api}=await fixture(t);await page.locator('#listen-mode').selectOption('keywords');const manifest=await api('/training/articles/1'),unit=manifest.units[0];const targets=[];let offset=0;for(let i=0;i<unit.gap_count;i++){offset+=unit.keyword_parts[i].length;const rest=unit.text.slice(offset),next=unit.keyword_parts[i+1];const end=next?rest.indexOf(next):rest.length;targets.push(rest.slice(0,end));offset+=end;}
  for(let i=0;i<targets.length;i++)await page.locator(`[data-listen-gap="${i}"]`).fill(targets[i]);await listen(page,'save').click();await page.locator('.practice-feedback').waitFor();assert.match(await page.locator('#listen-feedback').innerText(),/100%/);assert.equal((await api('/training/attempts')).attempts[0].mode,'keywords');
});

test('finite A-B loops finish and stopping cancels later speech callbacks',async t=>{
  const {page}=await fixture(t);await page.locator('#listen-end').selectOption('1');await page.locator('#listen-repeats').fill('2');await page.locator('#listen-gap').fill('0');await listen(page,'loop').click();await page.waitForFunction(()=>document.querySelector('#listen-playing').textContent==='本次播放已完成。');assert.equal(await page.evaluate(()=>window.__spoken.length),4);await listen(page,'loop').click();await listen(page,'stop').click();const count=await page.evaluate(()=>window.__spoken.length);await page.waitForTimeout(150);assert.equal(await page.evaluate(()=>window.__spoken.length),count);
});

test('missing English voices offers reference upload and the selected sentence stays selected',async t=>{
  const {page,api}=await fixture(t,'listening/1',{noVoices:true});await page.locator('#listen-unit').selectOption('1');await listen(page,'play').click();assert.match(await page.locator('#listen-playing').innerText(),/没有可用的本机英文语音/);await page.locator('.practice-reference summary').click();await page.locator('#listen-reference').setInputFiles({name:'reference.wav',mimeType:'audio/wav',buffer:wav()});await page.waitForFunction(()=>document.querySelector('#listen-message').textContent.includes('原音已保存'));assert.equal(await page.locator('#listen-unit').inputValue(),'1');assert.ok((await api('/training/articles/1')).units[1].reference);await listen(page,'play').click();await page.waitForFunction(()=>document.querySelector('#listen-playing').textContent==='本次播放已完成。');
});

test('actual MediaRecorder records a synthetic microphone and releases tracks on stop',async t=>{
  const {page,api}=await fixture(t);assert.equal(await page.evaluate(()=>window.__gumCalls),0);await page.locator('#listen-mode').selectOption('readalong');await page.locator('#listen-asr').check();const source=await page.locator('#listen-source').innerText();await page.evaluate(text=>window.__recognitionText=text,source);await listen(page,'record').click();await page.waitForFunction(()=>document.querySelector('#listen-record-status').textContent.includes('正在录音'));await page.waitForTimeout(350);await listen(page,'record-stop').click();await page.locator('#listen-record-preview audio').waitFor();assert.equal(await page.evaluate(()=>window.__streams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),true);await listen(page,'save').click();await page.locator('.practice-feedback').waitFor();const attempt=(await api('/training/attempts')).attempts[0];assert.ok(attempt.audio_size>32);assert.equal(attempt.mode,'readalong');assert.match(await page.locator('#listen-feedback').innerText(),/不是声学发音评分/);await screenshot(page,'listening-recording-feedback');
});

test('shadow recording requires headphones and plays the reference while recording',async t=>{
  const {page,api}=await fixture(t);await page.locator('#listen-mode').selectOption('shadow');await listen(page,'record').click();assert.equal(await page.evaluate(()=>window.__gumCalls),0);assert.match(await page.locator('#listen-message').innerText(),/戴耳机/);await page.locator('#listen-headphones').check();await listen(page,'record').click();await page.waitForFunction(()=>window.__spoken.length===1);await page.waitForFunction(()=>practiceUI?.recordSession?.size>0);await listen(page,'record-stop').click();await page.locator('#listen-record-preview audio').waitFor();await listen(page,'save').click();await page.locator('.practice-feedback').waitFor();assert.equal((await api('/training/attempts')).attempts[0].mode,'shadow');
});

test('denied microphones keep local audio upload usable',async t=>{
  const {page,api}=await fixture(t,'listening/1',{deny:true});await page.locator('#listen-mode').selectOption('readalong');await listen(page,'record').click();await page.waitForFunction(()=>document.querySelector('#listen-message').textContent.includes('麦克风许可未开启'));await page.locator('#listen-record-file').setInputFiles({name:'local.wav',mimeType:'audio/wav',buffer:wav()});await page.locator('#listen-record-preview audio').waitFor();await listen(page,'save').click();await page.locator('.practice-feedback').waitFor();assert.equal((await api('/training/attempts')).attempts[0].feedback.accuracy,null);
});

test('navigating away before microphone permission returns stops the late stream',async t=>{
  const {page}=await fixture(t);await page.evaluate(()=>navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>window.__releaseMic=resolve));await page.locator('#listen-mode').selectOption('readalong');await listen(page,'record').click();await page.waitForFunction(()=>Boolean(window.__releaseMic));await page.locator('[data-nav="library"]').click();await page.locator('#article-grid').waitFor();await page.evaluate(async()=>window.__releaseMic(await window.__makeStream()));await page.waitForFunction(()=>window.__stopped>0);assert.equal(await page.locator('#listen-main').count(),0);
});

test('a lost listening save acknowledgement reuses the same receipt',async t=>{
  const {page,api}=await fixture(t);let posts=[];await page.route('**/api/training/attempts',async route=>{if(route.request().method()==='POST'){posts.push(route.request().postDataJSON());if(posts.length===1){await route.fetch();return route.abort('failed');}}return route.continue();});await page.locator('#listen-attempt').fill('A different sentence.');await listen(page,'save').click();await page.waitForFunction(()=>document.querySelector('[data-listen="save"]').textContent.includes('重试'));assert.equal(await page.locator('#listen-mode').isDisabled(),true);await listen(page,'save').click();await page.locator('.practice-feedback').waitFor();assert.deepEqual(posts[0],posts[1]);assert.equal((await api('/training/attempts')).attempts.length,1);
});

test('all eight reading question types support answering, ordering and authoritative scoring',async t=>{
  const {page,context}=await fixture(t,'exercises/1');await start(page);assert.equal(await page.locator('.reading-question').count(),8);assert.equal(await page.locator('.reading-question').filter({hasText:'参考答案'}).count(),0);await fillCorrect(page,context);await reading(page,'submit').click();await page.locator('.reading-result').waitFor();assert.match(await page.locator('.reading-result').innerText(),/100 分/);assert.equal(await page.locator('.reading-correct').count(),8);await screenshot(page,'reading-exercise-results');
});

test('automatic draft saves survive a reload without exposing the answer key',async t=>{
  const {page,context,api}=await fixture(t,'exercises/1');await start(page);const snapshot=await expected(context,page),q=snapshot.questions.find(q=>q.type==='cloze');await page.locator(`[data-reading-gap="${q.id}"]`).fill('unsaved draft');await saved(page);const id=page.url().match(/session\/(\d+)/)[1];const response=await api('/reading/sessions/'+id);assert.equal(response.session.questions.some(q=>'answer'in q),false);await page.reload();await page.locator(`[data-reading-gap="${q.id}"]`).waitFor();assert.equal(await page.locator(`[data-reading-gap="${q.id}"]`).inputValue(),'unsaved draft');
});

test('typing during an in-flight draft save preserves the newer text and caret',async t=>{
  const {page,context}=await fixture(t,'exercises/1');await start(page);const q=(await expected(context,page)).questions.find(q=>q.type==='cloze');let release,first=true;await page.route('**/api/reading/sessions/*/answers',async route=>{if(route.request().method()==='PATCH'&&first){first=false;await new Promise(r=>release=r);}await route.continue();});const input=page.locator(`[data-reading-gap="${q.id}"]`);await input.fill('first');await page.waitForTimeout(650);await input.fill('second draft');await input.evaluate(el=>el.setSelectionRange(2,5));release();await saved(page);assert.equal(await input.inputValue(),'second draft');assert.equal(await input.evaluate(el=>el.selectionStart),2);assert.equal(await input.evaluate(el=>el.selectionEnd),5);
});

test('conflicting reading drafts stay local until the user reconciles versions',async t=>{
  const {page,context,api}=await fixture(t,'exercises/1');await start(page);const snapshot=await expected(context,page),q=snapshot.questions.find(q=>q.type==='cloze'),other=snapshot.questions.find(q=>q.type==='choice'),id=page.url().match(/session\/(\d+)/)[1];await api('/reading/sessions/'+id+'/answers','PATCH',{version:1,answers:{[other.id]:other.answer}});await page.locator(`[data-reading-gap="${q.id}"]`).fill('keep my draft');await reading(page,'conflict-local').waitFor();assert.equal(await page.locator(`[data-reading-gap="${q.id}"]`).inputValue(),'keep my draft');assert.ok((await api('/reading/sessions/'+id)).session.answers[other.id]);await reading(page,'conflict-local').click();await saved(page);assert.equal((await api('/reading/sessions/'+id)).session.answers[q.id][0],'keep my draft');
});

test('deadline expiry restores only previously saved answers after reload',async t=>{
  const {page,context,api}=await fixture(t,'exercises/1');await page.locator('#reading-time').fill('1');await start(page);const snapshot=await expected(context,page),q=snapshot.questions.find(q=>q.type==='choice');await page.locator(`[data-reading-choice="${q.id}"][value="${q.answer[0]}"]`).check();await saved(page);await context.request.post(origin+'/__test/advance',{data:{seconds:61}});await page.reload();await page.locator('.reading-result').waitFor();assert.match(await page.locator('.reading-result').innerText(),/限时结束/);const id=page.url().match(/session\/(\d+)/)[1];assert.equal((await api('/reading/sessions/'+id)).session.result.correct,1);
});

test('a live countdown automatically ends a short timed session',async t=>{
  const {page,api}=await fixture(t,'exercises/1');const article=(await api('/reading/catalog')).articles[0];const set=(await api('/reading/sets','POST',{article_id:article.id,article_hash:article.hash,source:'source'})).set;const session=(await api('/reading/sessions','POST',{set_ids:[set.id],request_id:'short_countdown',time_limit_seconds:2})).session;await page.goto(origin+'/#exercises/session/'+session.id);await page.reload();await page.locator('.reading-result').waitFor({timeout:6000});assert.match(await page.locator('.reading-result').innerText(),/限时结束/);
});

test('a lost submission acknowledgement retries once without duplicating mistakes',async t=>{
  const {page,api}=await fixture(t,'exercises/1');await start(page);let posts=[];await page.route('**/api/reading/sessions/*/answers',async route=>{if(route.request().method()==='POST'){posts.push(route.request().postDataJSON());if(posts.length===1){await route.fetch();return route.abort('failed');}}return route.continue();});await reading(page,'submit').click();await page.waitForFunction(()=>document.querySelector('[data-reading="submit"]').textContent.includes('重试'));assert.equal(await page.locator('#reading-work input').first().isDisabled(),true);await reading(page,'submit').click();await page.locator('.reading-result').waitFor();assert.deepEqual(posts[0],posts[1]);assert.equal((await api('/reading/mistakes')).mistakes.length,8);assert.equal((await api('/reading/history')).sessions.length,1);
});

test('mistake notes survive filtering and can be classified then practiced again',async t=>{
  const {page,context,api}=await fixture(t,'exercises/1');await start(page);await reading(page,'submit').click();await page.locator('.reading-result').waitFor();await reading(page,'mistakes').first().click();await page.locator('[data-mistake-id]').first().waitFor();const id=await page.locator('[data-mistake-id]').first().getAttribute('data-mistake-id');await page.locator(`[data-mistake-note="${id}"]`).fill('先查词性，再看语境');await page.locator('#reading-reason').selectOption('grammar');await page.locator('#reading-reason').selectOption('');assert.equal(await page.locator(`[data-mistake-note="${id}"]`).inputValue(),'先查词性，再看语境');await page.locator(`[data-mistake-reason="${id}"]`).selectOption('grammar');await page.locator(`[data-reading="save-mistake"][data-id="${id}"]`).click();await page.waitForFunction(()=>document.querySelector('#reading-message').textContent.includes('错因与笔记已保存'));assert.equal((await api('/reading/mistakes')).mistakes.find(m=>String(m.id)===id).note,'先查词性，再看语境');await page.locator(`[data-reading-mistake="${id}"]`).check();await reading(page,'practice-mistakes').click();await page.locator('#reading-work').waitFor();await fillCorrect(page,context);await reading(page,'submit').click();await page.locator('.reading-result').waitFor();assert.match(await page.locator('.reading-result').innerText(),/100 分/);assert.equal((await api('/reading/mistakes')).mistakes.find(m=>String(m.id)===id).resolved,1);
});

test('stage assessment combines two articles and records type-specific results',async t=>{
  const {page,context,api}=await fixture(t,'exercises/1');for(const box of (await page.locator('[data-reading-stage]').all()).slice(0,2))await box.check();await reading(page,'assessment').click();await page.locator('#reading-work').waitFor();assert.equal(await page.locator('.reading-question').count(),16);await fillCorrect(page,context);await reading(page,'submit').click();await page.locator('.reading-result').waitFor();assert.match(await page.locator('.reading-result').innerText(),/阶段测评：100 分/);assert.equal((await api('/reading/history')).sessions[0].kind,'assessment');
});

test('manual question authoring validates source evidence and renders text safely',async t=>{
  const {page,api}=await fixture(t,'exercises/1');await reading(page,'author').click();const form=page.locator('#reading-author');await form.locator('[name="prompt"]').fill('<img src=x onerror="window.practiceXss=true"> Which statement fits?');await form.locator('[name="options"]').fill('A journey is part of the holiday.\nThe article is about cooking.');await form.locator('[name="answer"]').fill('1');await form.locator('[name="explanation"]').fill('核对原文中关于旅程的描述。');await form.locator('[name="evidence"]').fill('It is part of the holiday.');await form.locator('[type="submit"]').click();await reading(page,'author-start').click();await page.locator('#reading-work').waitFor();assert.equal(await page.locator('.reading-question img').count(),0);assert.equal(await page.evaluate(()=>Boolean(window.practiceXss)),false);assert.match(await page.locator('.reading-question h3').innerText(),/<img/);assert.equal((await api('/reading/history')).sessions[0].total,1);
});

test('a lost session-start acknowledgement has a visible retry using the same request',async t=>{
  const {page,api}=await fixture(t,'exercises/1');let posts=[];await page.route('**/api/reading/sessions',async route=>{if(route.request().method()==='POST'){posts.push(route.request().postDataJSON());if(posts.length===1){await route.fetch();return route.abort('failed');}}return route.continue();});await reading(page,'start-source').click();await reading(page,'retry-start').waitFor();await reading(page,'retry-start').click();await page.locator('#reading-work').waitFor();assert.deepEqual(posts[0],posts[1]);assert.equal((await api('/reading/history')).sessions.length,1);
});

test('AI generation is explicit and a late result cannot reopen a page after navigation',async t=>{
  const {page}=await fixture(t,'exercises/1');let release;await page.route('**/api/reading/sets',async route=>{if(route.request().postDataJSON().source==='ai')await new Promise(r=>release=r);await route.continue().catch(()=>{});});await reading(page,'start-ai').click();await page.waitForFunction(()=>document.querySelector('#reading-message').textContent.includes('正在根据'));await page.locator('[data-nav="library"]').click();await page.locator('#article-grid').waitFor();release?.();await page.waitForTimeout(150);assert.equal(await page.locator('#reading-work').count(),0);
});

test('mobile listening and reading controls fit the viewport',async t=>{
  const {page}=await fixture(t,'listening/1',{viewport:{width:390,height:844}});await page.locator('#listen-mode').selectOption('readalong');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await screenshot(page,'mobile-listening-practice');await page.locator('[data-nav="exercises"]').click();await reading(page,'start-source').waitFor();await start(page);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.equal(await page.locator('#reading-work .reading-question').count(),8);await screenshot(page,'mobile-reading-exercises');
});

test('unsupported recording APIs explain the fallback without requesting a microphone',async t=>{
  const {page,api}=await fixture(t,'listening/1',{noRecorder:true});await page.locator('#listen-mode').selectOption('readalong');await listen(page,'record').click();assert.match(await page.locator('#listen-message').innerText(),/不支持录音/);assert.equal(await page.evaluate(()=>window.__gumCalls),0);await page.locator('#listen-record-file').setInputFiles({name:'local.wav',mimeType:'audio/wav',buffer:wav()});await page.locator('#listen-record-preview audio').waitFor();await listen(page,'save').click();await page.locator('.practice-feedback').waitFor();assert.ok((await api('/training/attempts')).attempts[0].audio_size>0);
});

test('leaving an active recording stops the stream and cancels playback',async t=>{
  const {page}=await fixture(t);await page.locator('#listen-mode').selectOption('readalong');await listen(page,'record').click();await page.waitForFunction(()=>document.querySelector('#listen-record-status').textContent.includes('正在录音'));await page.waitForTimeout(150);await page.locator('[data-nav="library"]').click();await page.locator('#article-grid').waitFor();assert.equal(await page.evaluate(()=>window.__streams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),true);assert.equal(await page.locator('#listen-record-preview').count(),0);
});

test('configured AI questions cover eight types through the normal practice workflow',async t=>{
  const {page,context,api}=await fixture(t,'exercises/1');let generationCalls=0;page.on('request',r=>{if(r.url().endsWith('/reading/sets')&&r.method()==='POST'&&r.postDataJSON().source==='ai')generationCalls++;});assert.equal(generationCalls,0);await start(page,'ai');assert.equal(generationCalls,1);const snapshot=await expected(context,page);assert.equal(snapshot.materials[0].source,'ai');assert.equal(new Set(snapshot.questions.map(q=>q.type)).size,8);await fillCorrect(page,context);await reading(page,'submit').click();await page.locator('.reading-result').waitFor();assert.equal((await api('/reading/history')).sessions[0].score,100);
});
