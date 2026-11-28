/* Actual browser interaction against Flask, local media and a controlled clock. */
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
const root=path.join(__dirname,'..');
let directory,server,origin,options;
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jduEAAAAASUVORK5CYII=','base64');
before(async()=>{
  directory=fs.mkdtempSync(path.join(os.tmpdir(),'margin-wordbook-'));
  const python=process.env.READING_TEST_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  server=spawn(python,['-u','-c',`
import sys
from datetime import datetime, timedelta
from flask import request, jsonify
from werkzeug.serving import make_server
from app import create_app
from newsreader.db import get_db, CHINA_TZ
import newsreader.srs as srs
current = datetime(2026,11,19,9,0,tzinfo=CHINA_TZ)
srs.clock = lambda: current
app = create_app({'DATABASE':sys.argv[1]})
@app.post('/__test/reset')
def reset():
    global current
    current = datetime(2026,11,19,9,0,tzinfo=CHINA_TZ)
    db=get_db()
    db.execute('DELETE FROM vocabulary')
    db.execute('DELETE FROM srs_events')
    db.execute('DELETE FROM word_collections')
    db.execute("DELETE FROM settings WHERE key LIKE 'srs.%'")
    db.commit()
    return jsonify(ok=True)
@app.post('/__test/advance')
def advance():
    global current
    current += timedelta(minutes=request.json['minutes'])
    return jsonify(now=current.isoformat())
server=make_server('127.0.0.1',0,app,threaded=True)
print('PORT:'+str(server.server_port),flush=True)
server.serve_forever()
`,path.join(directory,'test.sqlite3')],{cwd:root,stdio:['ignore','pipe','pipe']});
  let output='',errors='';server.stderr.on('data',chunk=>{errors=(errors+chunk).slice(-5000);});
  origin=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error(errors)),15000);server.once('exit',code=>{clearTimeout(timer);reject(new Error('Server exited '+code+': '+errors));});server.stdout.on('data',chunk=>{output+=chunk;const port=output.match(/PORT:(\d+)/);if(port){clearTimeout(timer);resolve('http://127.0.0.1:'+port[1]);}});});
  options={headless:true};if(process.env.READING_TEST_CHROMIUM_MODULE){const {default:runtime}=await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);options={...options,executablePath:await runtime.executablePath(),args:runtime.args};}
});
after(async()=>{if(server&&server.exitCode===null){const exited=new Promise(r=>server.once('exit',r));server.kill();await exited;}if(directory)fs.rmSync(directory,{recursive:true,force:true});});
async function fixture(t,route='vocabulary',viewport={width:1440,height:1000}){
  const browser=await chromium.launch(options);t.after(()=>browser.close());const context=await browser.newContext({viewport});
  const api=async(endpoint,method='GET',data)=>{const response=await context.request.fetch(origin+'/api'+endpoint,{method,data});assert.equal(response.ok(),true,await response.text());return response.json();};
  await context.request.post(origin+'/__test/reset',{data:{}});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
  await page.goto(origin+'/#'+route);await page.locator(route==='vocabulary'?'#wb-rows':'#srs-card').waitFor();return {page,api,context};
}
const wb=(page,action)=>page.locator(`[data-wb-action="${action}"]`);
const srs=(page,action)=>page.locator(`[data-srs="${action}"]`);
const field=(page,key)=>page.locator(`[data-wb-field="${key}"]`);
async function go(page,route){await page.evaluate(route=>location.hash=route,route);await page.locator(route==='vocabulary'?'#wb-rows':'#srs-card').waitFor();}
async function save(page){await page.locator('#wb-edit button[type="submit"]').click();await page.waitForFunction(()=>!document.querySelector('#wordbook-dialog').open);}
async function screenshot(page,name){if(process.env.READING_TEST_SCREENSHOTS){fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,name+'.png')});}}

test('wordbooks, multiple senses and contexts, phrase patterns and note templates persist',async t=>{
  const {page,api}=await fixture(t);
  await page.locator('.wb-collections summary').click();await page.locator('#wb-new-name').fill('经济外刊');await wb(page,'collection-add').click();await page.locator('[data-collection-name]').waitFor();
  await wb(page,'add').click();await field(page,'entry_kind').selectOption('phrase');await field(page,'word').fill('take into account');await field(page,'senses.0.meaning').fill('考虑');
  await wb(page,'add-item').filter({hasText:'添加义项'}).click();await field(page,'senses.1.meaning').fill('计入');
  await wb(page,'add-item').filter({hasText:'添加原句'}).click();await field(page,'contexts.0.text').fill('We take into account all costs.');
  await wb(page,'add-item').filter({hasText:'添加原句'}).click();await field(page,'contexts.1.text').fill('Take into account the risks.');
  await page.locator('[data-wb-membership]').check();await wb(page,'template').click();await field(page,'notes.0.body').fill('常见经济用语');await save(page);
  await page.reload();await page.locator('.wb-word').waitFor();await page.locator('.wb-word').click();
  assert.equal(await field(page,'contexts.1.text').inputValue(),'Take into account the risks.');assert.equal(await field(page,'notes.0.body').inputValue(),'常见经济用语');
  await wb(page,'close').click();await wb(page,'add').click();await field(page,'entry_kind').selectOption('pattern');await field(page,'word').fill('the more + clause, the more + clause');await field(page,'senses.0.meaning').fill('越…越…');await save(page);
  assert.equal((await api('/wordbook')).entries.length,2);await screenshot(page,'wordbooks-and-rich-entries');
});

test('merging inflections keeps the target plan and combines evidence',async t=>{
  const {page,api}=await fixture(t);
  const a=(await api('/wordbook','POST',{word:'journey',meaning:'旅程',contexts:[{text:'A journey.'}]})).entry;
  const b=(await api('/wordbook','POST',{word:'journeys',meaning:'多次旅程',contexts:[{text:'Many journeys.'}]})).entry;
  await page.reload();await page.locator('[data-wb-select]').first().waitFor();for(const checkbox of await page.locator('[data-wb-select]').all())await checkbox.check();await wb(page,'merge').click();await page.locator('#wb-merge-target').selectOption(String(a.id));await wb(page,'merge-confirm').click();await page.waitForFunction(()=>!document.querySelector('#wordbook-dialog').open);
  const entries=(await api('/wordbook')).entries;assert.equal(entries.length,1);assert.deepEqual(entries[0].aliases,['journeys']);assert.equal(entries[0].contexts.length,2);assert.deepEqual(entries[0].schedule,a.schedule);assert.notEqual(a.id,b.id);
});

test('import preview is read-only and JSON export downloads the selected data',async t=>{
  const {page,api}=await fixture(t);await wb(page,'import').click();
  const payload={entries:[{word:'journey',meaning:'旅程',aliases:['journeys'],contexts:[{text:'A journey.'}]}]};
  await page.locator('#wb-import-file').setInputFiles({name:'words.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(payload))});
  await page.waitForFunction(()=>document.querySelector('#wb-import-preview').textContent.includes('1 条'));assert.equal((await api('/wordbook')).entries.length,0);
  await wb(page,'import-confirm').click();await page.waitForFunction(()=>!document.querySelector('#wordbook-dialog').open);await page.locator('[data-wb-select]').check();await wb(page,'export').click();
  const downloaded=page.waitForEvent('download');await wb(page,'download').click();const file=await downloaded;const json=JSON.parse(fs.readFileSync(await file.path(),'utf8'));assert.equal(json.entries[0].word,'journey');assert.deepEqual(json.entries[0].aliases,['journeys']);
});

test('local image and example cards keep an unsaved note intact',async t=>{
  const {page,api}=await fixture(t);await api('/wordbook','POST',{word:'journey',meaning:'旅程'});await page.reload();await page.locator('.wb-word').click();
  await field(page,'note').fill('图片上传时仍保留的笔记');await page.locator('#wb-media').setInputFiles({name:'tiny.png',mimeType:'image/png',buffer:png});await page.locator('#wb-cards img').waitFor();
  assert.equal(await field(page,'note').inputValue(),'图片上传时仍保留的笔记');assert.equal(await page.locator('#wb-cards img').evaluate(el=>el.complete&&el.naturalWidth>0),true);
  await field(page,'cardFront').fill('A journey home.');await field(page,'cardBack').fill('归途');await wb(page,'example-add').click();await page.waitForFunction(()=>document.querySelectorAll('#wb-cards .wb-card').length===2);await save(page);
  const e=(await api('/wordbook')).entries[0];assert.equal(e.note,'图片上传时仍保留的笔记');assert.equal(e.cards.length,2);
});

test('edit conflicts retain the draft and require explicit version reconciliation',async t=>{
  const {page,api}=await fixture(t);const e=(await api('/wordbook','POST',{word:'journey',meaning:'旅程'})).entry;await page.reload();await page.locator('.wb-word').click();await field(page,'note').fill('本页草稿');
  await api('/wordbook/'+e.id,'PATCH',{version:e.version,note:'另一处的修改'});await page.locator('#wb-edit button[type="submit"]').click();await wb(page,'accept-version').waitFor();assert.equal(await field(page,'note').inputValue(),'本页草稿');assert.equal((await api('/wordbook/'+e.id)).entry.note,'另一处的修改');
  await wb(page,'accept-version').click();await save(page);assert.equal((await api('/wordbook/'+e.id)).entry.note,'本页草稿');
});

test('pause, difficult filters and resume keep the original due date',async t=>{
  const {page,api}=await fixture(t);const e=(await api('/wordbook','POST',{word:'journey',meaning:'旅程'})).entry;await page.reload();await wb(page,'difficult').click();await page.waitForFunction(()=>document.querySelector('#wb-rows').textContent.includes('困难词'));await wb(page,'pause').click();await page.waitForFunction(()=>document.querySelector('#wb-rows').textContent.includes('复习已暂停'));await page.locator('#wb-status').selectOption('paused');assert.equal(await page.locator('.wb-row').count(),1);
  await wb(page,'pause').click();await page.waitForFunction(()=>document.querySelectorAll('.wb-row').length===0);const result=(await api('/wordbook/'+e.id)).entry;assert.equal(result.schedule.due_at,e.schedule.due_at);assert.equal(result.schedule.suspended,0);assert.equal(result.schedule.leech,1);
});

test('front/back practice hides meaning until reveal and schedules tomorrow',async t=>{
  const {page,api}=await fixture(t);const e=(await api('/wordbook','POST',{word:'journey',meaning:'旅程'})).entry;await go(page,'review');
  assert.equal(await page.locator('.srs-back').count(),0);assert.equal(await srs(page,'grade').count(),0);await srs(page,'reveal').click();assert.match(await page.locator('.srs-back').innerText(),/旅程/);await page.locator('[data-grade="4"]').click();await page.waitForFunction(()=>document.querySelector('#srs-message').textContent.includes('已保存评分'));
  const result=(await api('/wordbook/'+e.id)).entry;assert.equal(result.schedule.interval_days,1);assert.equal(result.mastery,'new');await page.reload();await page.locator('#srs-card .empty-state').waitFor();
});

test('cloze removes all target forms and missing contexts offer a working fallback',async t=>{
  const {page,api}=await fixture(t);await api('/wordbook/import','POST',{entries:[{word:'journey',meaning:'旅程',aliases:['journeys'],contexts:[{text:'Journeys and a journey.'}]}]});await go(page,'review');await page.locator('#srs-mode').selectOption('cloze');assert.equal(await page.locator('.srs-front blockquote').innerText(),'____ and a ____.');await srs(page,'reveal').click();assert.match(await page.locator('.srs-back').innerText(),/Journeys and a journey/);
  await srs(page,'pause').click();await api('/wordbook','POST',{word:'city',meaning:'城市'});await srs(page,'reload').click();assert.equal(await srs(page,'reveal').isDisabled(),true);await srs(page,'fallback').click();assert.equal(await page.locator('#srs-mode').inputValue(),'front');assert.equal(await srs(page,'reveal').isEnabled(),true);
});

test('spelling accepts merged aliases and never automatically advances',async t=>{
  const {page,api}=await fixture(t);await api('/wordbook/import','POST',{entries:[{word:'journey',meaning:'旅程',aliases:['journeys']}]});await go(page,'review');await page.locator('#srs-mode').selectOption('spelling');await page.locator('#srs-attempt').fill(' JOURNEYS ');await srs(page,'reveal').click();assert.match(await page.locator('#srs-spelling-result').innerText(),/拼写正确/);assert.equal((await api('/review/queue')).stats.answers_today,0);await page.locator('[data-grade="3"]').click();await page.waitForFunction(()=>document.querySelector('#srs-message').textContent.includes('已保存评分'));assert.equal((await api('/review/queue')).stats.answers_today,1);
});

test('listening mode conceals the answer and explains missing local English voices',async t=>{
  const {page,api}=await fixture(t);await api('/wordbook','POST',{word:'journey',meaning:'旅程'});await page.addInitScript(()=>Object.defineProperty(window,'speechSynthesis',{value:{getVoices:()=>[],cancel(){}}}));await page.reload();await go(page,'review');await page.locator('#srs-mode').selectOption('listen');assert.doesNotMatch(await page.locator('.srs-front').innerText(),/journey/);await srs(page,'play').click();assert.match(await page.locator('#srs-audio-status').innerText(),/未检测到本机英文语音/);await srs(page,'reveal').click();assert.match(await page.locator('.srs-back').innerText(),/journey/);
});

test('a lost acknowledgement retries the same receipt without a second grade',async t=>{
  const {page,api}=await fixture(t);await api('/wordbook','POST',{word:'journey',meaning:'旅程'});await go(page,'review');let calls=[];
  await page.route('**/api/review/answer',async route=>{calls.push(route.request().postDataJSON());if(calls.length===1){const response=await route.fetch();assert.equal(response.ok(),true);await route.abort('failed');}else await route.continue();});
  await srs(page,'reveal').click();await page.locator('[data-grade="4"]').click();await srs(page,'retry').waitFor();await page.waitForFunction(()=>!document.querySelector('[data-srs=retry]').disabled);assert.equal(await page.locator('[data-grade="5"]').isDisabled(),true);assert.equal((await api('/review/queue')).stats.answers_today,1);await srs(page,'retry').click();await page.waitForFunction(()=>document.querySelector('#srs-message').textContent.includes('已保存评分'));assert.equal(calls.length,2);assert.deepEqual(calls[0],calls[1]);assert.equal((await api('/review/queue')).stats.answers_today,1);
});

test('forgetting reappears after ten minutes and resumes from one day',async t=>{
  const {page,api,context}=await fixture(t);const e=(await api('/wordbook','POST',{word:'journey',meaning:'旅程'})).entry;await go(page,'review');await srs(page,'reveal').click();await page.locator('[data-grade="0"]').click();await page.waitForFunction(()=>document.querySelector('#srs-message').textContent.includes('遗忘后进入重新学习'));
  assert.equal((await api('/wordbook/'+e.id)).entry.schedule.state,'relearning');await context.request.post(origin+'/__test/advance',{data:{minutes:10}});await srs(page,'reload').click();await srs(page,'reveal').click();await page.locator('[data-grade="4"]').click();await page.waitForFunction(()=>document.querySelector('#srs-message').textContent.includes('下次间隔 1 天'));assert.equal((await api('/wordbook/'+e.id)).entry.schedule.repetitions,1);
});

test('daily limits explain empty queues and persist through reload',async t=>{
  const {page,api}=await fixture(t);await api('/wordbook','POST',{word:'journey',meaning:'旅程'});await go(page,'review');await page.locator('.srs-settings summary').click();await page.locator('[name="new_limit"]').fill('0');await page.locator('#srs-settings button').click();await page.waitForFunction(()=>document.querySelector('#srs-message').textContent.includes('每日限额已保存'));assert.match(await page.locator('#srs-card').innerText(),/已达每日限额/);await page.reload();await page.locator('#srs-card .empty-state').waitFor();assert.equal(await page.locator('[name="new_limit"]').inputValue(),'0');
});

test('mobile wordbook and review cards fit and render user text safely',async t=>{
  const {page,api}=await fixture(t,'vocabulary',{width:390,height:844});await api('/wordbook','POST',{word:'take into account',entry_kind:'phrase',meaning:'<img src=x onerror="window.wordXss=true">',contexts:[{text:'We take into account every point.'}]});await page.reload();await page.locator('.wb-word').waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.equal(await page.locator('.wb-row img').count(),0);assert.equal(await page.evaluate(()=>Boolean(window.wordXss)),false);await page.locator('.wb-word').click();assert.equal(await page.locator('#wordbook-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);await wb(page,'close').click();await go(page,'review');await srs(page,'reveal').click();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.equal(await page.locator('.srs-back img').count(),0);await screenshot(page,'mobile-spaced-repetition');
});

test('a late queue response cannot repaint a different page',async t=>{
  const {page}=await fixture(t);let release;
  await page.route('**/api/review/queue',async route=>{await new Promise(r=>release=r);await route.continue().catch(()=>{});});await page.evaluate(()=>location.hash='review');await page.waitForFunction(()=>document.querySelector('#page-label').textContent==='间隔复习');await go(page,'vocabulary');release?.();await page.waitForTimeout(100);assert.equal(await page.locator('#srs-card').count(),0);assert.equal(await page.locator('#wb-rows').count(),1);
});

test('reader lookup appends a new sentence to an already saved word',async t=>{
  const {page,api}=await fixture(t);
  const entry=(await api('/wordbook','POST',{word:'journey',meaning:'旅程',contexts:[{text:'An earlier journey.'}]})).entry;
  await api('/settings','PATCH',{reading_mode:'intensive',dictionary_provider:'offline'});
  const article=await api('/articles','POST',{title:'Another reading context',content:'A journey starts with a single step. We read every day and learn something new from the world. '.repeat(5)});
  await page.evaluate(id=>location.hash='reader/'+id,article.id);await page.reload();await page.waitForFunction(()=>document.querySelector('#pending-word-count')?.textContent==='0');await page.locator('#article-body [data-word="journey"]').first().click();await page.locator('.lookup-result').waitFor();
  if(!await page.locator('.lookup-definition').evaluate(el=>el.open))await page.locator('.lookup-definition > summary').click();
  const saveButton=page.locator('[data-action="save-lookup"]');assert.equal(await saveButton.isEnabled(),true);await saveButton.click();
  await page.waitForFunction(()=>document.querySelector('.toast-region').textContent.includes('原句已追加'));
  const saved=(await api('/wordbook/'+entry.id)).entry;assert.equal(saved.contexts.length,2);assert.equal(saved.meaning,'旅程');assert.match(saved.contexts[1].text,/single step/);
});

test('a newer import selection wins over a late preview response',async t=>{
  const {page,api}=await fixture(t);await wb(page,'import').click();let release,first=true;
  await page.route('**/api/wordbook/import',async route=>{if(route.request().postDataJSON().preview&&first){first=false;await new Promise(r=>release=r);}await route.continue();});
  const file=word=>({name:word+'.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({entries:[{word,meaning:'测试'}]}))});
  await page.locator('#wb-import-file').setInputFiles(file('first'));await page.waitForTimeout(80);await page.locator('#wb-import-file').setInputFiles(file('second'));
  await page.waitForFunction(()=>document.querySelector('#wb-import-preview').textContent.includes('second'));release();await page.waitForTimeout(120);assert.match(await page.locator('#wb-import-preview').innerText(),/second/);await wb(page,'import-confirm').click();await page.waitForFunction(()=>!document.querySelector('#wordbook-dialog').open);assert.deepEqual((await api('/wordbook')).entries.map(e=>e.word),['second']);
});

test('rapid grade clicks and pending edits cannot submit a second score',async t=>{
  const {page,api}=await fixture(t);await api('/wordbook','POST',{word:'journey',meaning:'旅程'});await go(page,'review');await srs(page,'reveal').click();let release,calls=0;
  await page.route('**/api/review/answer',async route=>{calls++;await new Promise(r=>release=r);await route.continue();});
  await page.locator('[data-grade="4"]').evaluate(button=>{button.click();button.click();});await page.waitForFunction(()=>document.querySelector('[data-grade="5"]').disabled);assert.equal(await page.locator('#srs-mode').isDisabled(),true);assert.equal(await page.locator('#srs-settings button').isDisabled(),true);release();await page.waitForFunction(()=>document.querySelector('#srs-message').textContent.includes('已保存评分'));assert.equal(calls,1);assert.equal((await api('/review/queue')).stats.answers_today,1);
});

test('local WAV listening cards actually play without exposing the term on the front',async t=>{
  const {page,api}=await fixture(t);const e=(await api('/wordbook','POST',{word:'journey',meaning:'旅程'})).entry;
  const wav=Buffer.alloc(44+1600);wav.write('RIFF',0);wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(1600,40);
  await api('/wordbook/'+e.id+'/cards','POST',{version:e.version,kind:'audio',mime:'audio/wav',filename:'journey.wav',base64:wav.toString('base64')});await go(page,'review');await page.locator('#srs-mode').selectOption('listen');assert.doesNotMatch(await page.locator('.srs-front').innerText(),/journey/);await srs(page,'play').click();await page.waitForFunction(()=>document.querySelector('#srs-audio-status').textContent.includes('正在播放音频卡片'));await srs(page,'reveal').click();assert.equal(await page.locator('.srs-back audio').count(),1);
});

test('daily list allows choosing another due card without advancing the first',async t=>{
  const {page,api}=await fixture(t);const a=(await api('/wordbook','POST',{word:'journey',meaning:'旅程'})).entry;const b=(await api('/wordbook','POST',{word:'city',meaning:'城市'})).entry;await go(page,'review');await page.locator('.srs-queue summary').click();await page.locator(`[data-srs="choose"][data-id="${b.id}"]`).click();assert.equal(await page.locator('.srs-word').innerText(),'city');await srs(page,'reveal').click();await page.locator('[data-grade="4"]').click();await page.waitForFunction(()=>document.querySelector('#srs-message').textContent.includes('已保存评分'));assert.equal((await api('/wordbook/'+a.id)).entry.schedule.state,'new');assert.equal((await api('/wordbook/'+b.id)).entry.schedule.state,'review');
});
