/* Local Flask / SQLite / Chromium. All files and restores are disposable test data. */
const assert=require('node:assert/strict'),{spawn}=require('node:child_process');const fs=require('node:fs'),os=require('node:os'),path=require('node:path');const {pathToFileURL}=require('node:url');const {before,after,test}=require('node:test');const {chromium}=require('playwright');
const root=path.join(__dirname,'..');let server,directory,origin,options;
before(async()=>{
  directory=fs.mkdtempSync(path.join(os.tmpdir(),'margin-data-'));
  server=spawn(process.env.READING_TEST_PYTHON||path.join(root,'.venv/bin/python'),['-u','-c',`
import sys,json,shutil,uuid
from flask import jsonify
from werkzeug.serving import make_server
from app import create_app
from newsreader.db import get_db,now
from newsreader.data_management import TABLES
app=create_app({'DATABASE':sys.argv[1],'AI_API_KEY':'synthetic-test-key','AI_MODEL':'test-model'})
@app.post('/__test/reset')
def reset():
    db=get_db();db.execute('PRAGMA defer_foreign_keys=ON')
    for table in TABLES:
        if table not in ('articles','data_state'):db.execute('DELETE FROM "'+table+'"')
    db.execute('DELETE FROM articles WHERE is_demo=0');db.execute('UPDATE articles SET progress=0,position=0,reading_seconds=0,completed_at=NULL,last_read_at=NULL')
    db.execute("INSERT INTO settings VALUES('seeded','true')");db.execute("INSERT INTO settings VALUES('analytics.started_at',?)",(now(),))
    db.execute('UPDATE data_state SET generation=?,revision=0',(uuid.uuid4().hex,));db.commit()
    manager=app.extensions['data_manager']
    for item in list(manager.previews.values()):shutil.rmtree(item['directory'],ignore_errors=True)
    manager.previews.clear()
    for file in manager.backups.iterdir():file.unlink()
    return jsonify(ok=True)
@app.post('/__test/cache')
def cache():
    result={'found':True,'meaning':'A controlled cached definition.','phonetic':'','phonetics':{'uk':'','us':'','other':''},'part_of_speech':'noun','example':'','source':'Controlled cached online entry','synonyms':[],'antonyms':[]}
    db=get_db();db.execute('INSERT OR REPLACE INTO dictionary_cache VALUES(?,?,?)',('v7:free:quux',json.dumps(result),now()));db.commit();return jsonify(ok=True)
server=make_server('127.0.0.1',0,app,threaded=True);print('PORT:'+str(server.server_port),flush=True);server.serve_forever()
`,path.join(directory,'learning.sqlite3')],{cwd:root,stdio:['ignore','pipe','pipe']});
  let out='',errors='';server.stderr.on('data',b=>errors=(errors+b).slice(-7000));origin=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error(errors)),15000);server.once('exit',code=>{clearTimeout(timer);reject(new Error('Server '+code+': '+errors));});server.stdout.on('data',b=>{out+=b;const match=out.match(/PORT:(\d+)/);if(match){clearTimeout(timer);resolve('http://127.0.0.1:'+match[1]);}});});
  options={headless:true};if(process.env.READING_TEST_CHROMIUM_MODULE){const {default:runtime}=await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);options={...options,executablePath:await runtime.executablePath(),args:runtime.args};}
});
after(async()=>{if(server&&server.exitCode===null){const exited=new Promise(r=>server.once('exit',r));server.kill();await exited;}if(directory)fs.rmSync(directory,{recursive:true,force:true});});
async function fixture(t,{mobile=false}={}){
  const browser=await chromium.launch(options);t.after(()=>browser.close());const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000}});await context.request.post(origin+'/__test/reset',{data:{}});
  const api=async(endpoint,method='GET',data)=>{const headers={};if(method!=='GET'){const state=await context.request.get(origin+'/api/data/state');headers['X-Margin-Generation']=(await state.json()).generation;}const r=await context.request.fetch(origin+'/api'+endpoint,{method,data,headers});assert.equal(r.ok(),true,await r.text());return r.json();};
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));await page.goto(origin+'/#data');await page.locator('#data-storage strong').first().waitFor();return{page,api,context};
}
const act=(p,name)=>p.locator(`[data-manage="${name}"]`);
async function uploaded(page,kind,buffer){await page.locator(`#data-${kind}-form input[type=file]`).setInputFiles({name:kind==='restore'?'backup.zip':'articles.json',mimeType:kind==='restore'?'application/zip':'application/json',buffer});await page.locator(`#data-${kind}-form button[type=submit]`).click();await page.locator(`#data-${kind}-preview .data-preview`).waitFor();}
async function backup(api,context){const record=(await api('/data/backups','POST',{})).backup;const r=await context.request.get(origin+'/api/data/backups/'+record.id+'/download');assert.equal(r.ok(),true);return{record,buffer:await r.body()};}
async function confirm(page,accept=true){await page.locator('#confirm-dialog').waitFor();await page.getByRole('button',{name:accept?'确认删除':'保留记录',exact:true}).click();}
async function added(api,word='journey'){return api('/vocabulary','POST',{word,meaning:'测试释义'});}
async function message(page,text){await page.locator('#data-message').getByText(text,{exact:false}).waitFor();}
async function restore(page,api,context){const saved=await backup(api,context);await uploaded(page,'restore',saved.buffer);await page.locator('[data-restore-confirm]').check();await act(page,'apply-restore').click();await page.locator('#data-storage strong').first().waitFor();return saved;}

test('data manager describes local offline operation and initializes the data generation',async t=>{
  const {page,api}=await fixture(t);assert.match(await page.locator('.data-page').innerText(),/本机 Python 服务/);assert.equal(await page.locator('#data-offline').isChecked(),false);
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('margin.data-generation')),(await api('/data/state')).generation);assert.equal(await page.locator('#data-backups tbody tr').count(),0);
  assert.equal(JSON.stringify(await api('/data/state')).includes('synthetic-test-key'),false);
});

test('offline preference persists and the reader still supports local lookup',async t=>{
  const {page,api}=await fixture(t);await page.locator('#data-offline').check();await message(page,'设置已保存');await page.reload();await page.locator('#data-storage strong').first().waitFor();assert.equal(await page.locator('#data-offline').isChecked(),true);
  assert.match(await page.locator('#network-status-text').innerText(),/离线模式/);await page.goto(origin+'/#reader/1');await page.locator('#article-body').waitFor();await page.locator('[data-reader-action=lookup]').click();await page.locator('#lookup-form input').fill('journey');await page.locator('#lookup-form button[type=submit]').click();await page.locator('.lookup-title').waitFor();
  const result=await api('/dictionary?word=journey&provider=offline');assert.equal(result.found,true);
});

test('offline AI interface shows paused generation and hides the setup prompt',async t=>{
  const {page,api}=await fixture(t);await api('/data/preferences','PATCH',{offline:true});await page.goto(origin+'/#reader/1');await page.locator('#article-body').waitFor();await page.locator('[data-study-action=open-context]').click();await page.locator('#study-dialog').waitFor();await page.waitForFunction(()=>document.querySelector('#study-service')?.textContent.includes('离线模式'));
  assert.equal(await page.locator('#study-generate').isDisabled(),true);assert.equal(await page.locator('#study-setup').isVisible(),false);
});

test('offline online-dictionary choice reuses a stored cache without changing provider',async t=>{
  const {page,api,context}=await fixture(t);await context.request.post(origin+'/__test/cache',{data:{}});await api('/data/preferences','PATCH',{offline:true});await api('/settings','PATCH',{dictionary_provider:'free'});
  await page.goto(origin+'/#reader/1');await page.locator('#article-body').waitFor();await page.locator('[data-reader-action=lookup]').click();await page.getByRole('combobox',{name:'查询词典',exact:true}).selectOption('free');await page.locator('#lookup-form input').fill('quux');await page.locator('#lookup-form button[type=submit]').click();await page.locator('.lookup-title').waitFor();
  await page.locator('.lookup-definition > summary').click();
  assert.match(await page.locator('#learning-panel').innerText(),/Controlled cached online entry/);assert.equal(await page.getByRole('combobox',{name:'查询词典',exact:true}).inputValue(),'free');
});

test('complete backup is created, listed and downloadable as a verified archive',async t=>{
  const {page,api}=await fixture(t);await added(api);await act(page,'backup').click();await message(page,'完整备份已创建');assert.equal(await page.locator('#data-backups tbody tr').count(),1);
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#data-backups a').click()]);assert.match(download.suggestedFilename(),/^margin-backup-.*\.zip$/);const file=await download.path();assert.equal(fs.readFileSync(file).subarray(0,2).toString(),'PK');
});

test('restore preview requires confirmation and restores with a rescue backup',async t=>{
  const {page,api,context}=await fixture(t);await added(api);const saved=await backup(api,context);await added(api,'later');const old=(await api('/data/state')).generation;
  await page.evaluate(()=>{sessionStorage.setItem('margin-reading-draft-1','old draft');sessionStorage.setItem('margin.lookup-outbox.v22','[]');sessionStorage.setItem('unrelated-key','keep');});
  await uploaded(page,'restore',saved.buffer);assert.equal(await act(page,'apply-restore').isDisabled(),true);assert.match(await page.locator('#data-restore-preview').innerText(),/词条\s+2\s+1/);
  await page.locator('[data-restore-confirm]').check();await act(page,'apply-restore').click();await page.waitForFunction(old=>sessionStorage.getItem('margin.data-generation')!==old,old);await page.locator('#data-storage strong').first().waitFor();
  const info=await api('/data/state');assert.equal(info.summary.words,1);assert.notEqual(info.generation,old);assert.equal(info.backups.filter(b=>b.kind==='rescue').length,1);
  assert.deepEqual(await page.evaluate(()=>[sessionStorage.getItem('margin-reading-draft-1'),sessionStorage.getItem('unrelated-key')]),[null,'keep']);
});

test('a changed revision rejects stale restore preview while preserving file selection',async t=>{
  const {page,api,context}=await fixture(t);const saved=await backup(api,context);await uploaded(page,'restore',saved.buffer);await added(api);await page.locator('[data-restore-confirm]').check();await act(page,'apply-restore').click();await message(page,'重新预览');
  assert.equal((await api('/data/state')).summary.words,1);assert.equal(await page.locator('#data-restore-form input').evaluate(n=>n.files[0].name),'backup.zip');
  await page.locator('#data-restore-form button[type=submit]').click();await page.locator('#data-restore-preview .data-preview').waitFor();assert.equal(await act(page,'apply-restore').isDisabled(),true);
});

test('lost restore acknowledgement reuses the receipt and reloads restored data',async t=>{
  const {page,api,context}=await fixture(t);await added(api);const saved=await backup(api,context);await added(api,'later');const previous=(await api('/data/state')).generation;
  const payloads=[];let lost=false;await page.route('**/api/data/restore/apply',async route=>{payloads.push(route.request().postDataJSON());if(!lost){lost=true;await route.fetch();return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Lost restore acknowledgement'})});}return route.continue();});
  await uploaded(page,'restore',saved.buffer);await page.locator('[data-restore-confirm]').check();await act(page,'apply-restore').click();await message(page,'Lost restore acknowledgement');assert.equal((await api('/data/state')).summary.words,1);
  await act(page,'apply-restore').click();await page.waitForFunction(old=>sessionStorage.getItem('margin.data-generation')!==old,previous);await page.locator('#data-storage strong').first().waitFor();assert.deepEqual(payloads[0],payloads[1]);assert.equal((await api('/data/state')).backups.filter(b=>b.kind==='rescue').length,1);
});

test('another open tab is blocked after restore until it reloads',async t=>{
  const {page,api,context}=await fixture(t);const other=await context.newPage();await other.goto(origin+'/#data');await other.locator('#data-storage strong').first().waitFor();const original=(await api('/data/state')).generation;
  const saved=await backup(api,context);await uploaded(page,'restore',saved.buffer);await page.locator('[data-restore-confirm]').check();await act(page,'apply-restore').click();await page.waitForFunction(old=>sessionStorage.getItem('margin.data-generation')!==old,original);
  await other.locator('#data-offline').check();await other.locator('#data-changed-banner').waitFor();assert.equal((await api('/data/state')).offline,false);
  await other.locator('[data-action=reload-data]').click();await other.locator('#data-storage strong').first().waitFor();await other.locator('#data-offline').check();await message(other,'设置已保存');assert.equal((await api('/data/state')).offline,true);
});

test('article package preview escapes titles and imports only new bodies',async t=>{
  const {page,api}=await fixture(t);const title='<img src=x onerror="window.__unsafe=true">';const data={format:'margin-articles-v1',articles:[{title,category:'生活',content:'The city has a small park. '.repeat(10)}]};
  await uploaded(page,'articles',Buffer.from(JSON.stringify(data)));assert.match(await page.locator('#data-articles-preview').innerText(),/预计新增 1 篇/);assert.equal(await page.evaluate(()=>!!window.__unsafe),false);
  await act(page,'apply-articles').click();await message(page,'已新增 1 篇文章');assert.equal((await api('/data/state')).summary.articles,6);
  await uploaded(page,'articles',Buffer.from(JSON.stringify(data)));assert.match(await page.locator('#data-articles-preview').innerText(),/预计新增 0 篇/);
});

test('article export contains portable content without reading progress or IDs',async t=>{
  const {page}=await fixture(t);const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'导出全部文章 JSON ↓'}).click()]);const text=fs.readFileSync(await download.path(),'utf8'),pack=JSON.parse(text);
  assert.equal(pack.format,'margin-articles-v1');assert.equal(pack.articles.length,5);assert.equal('progress' in pack.articles[0],false);assert.equal('id' in pack.articles[0],false);
});

test('malformed uploads leave original data and show actionable errors',async t=>{
  const {page,api}=await fixture(t);await page.locator('#data-restore-form input').setInputFiles({name:'bad.zip',mimeType:'application/zip',buffer:Buffer.from('not a zip')});await page.locator('#data-restore-form button').click();await message(page,'格式无效');assert.equal((await api('/data/state')).summary.articles,5);
  await page.locator('#data-articles-form input').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from('{}')});await page.locator('#data-articles-form button').click();await message(page,'文章包无效');assert.equal((await api('/data/state')).summary.articles,5);
});

test('cache cleanup confirms the choice and preserves vocabulary',async t=>{
  const {page,api,context}=await fixture(t);await added(api);await context.request.post(origin+'/__test/cache',{data:{}});await act(page,'refresh').click();await page.waitForFunction(()=>document.querySelector('#data-storage')?.textContent.includes('1 条'));
  await act(page,'cache').click();await confirm(page);await message(page,'已清理 1 条词典缓存');assert.equal((await api('/data/state')).summary.words,1);
});

test('backup deletion can be cancelled and deletes only after confirmation',async t=>{
  const {page,api}=await fixture(t);await act(page,'backup').click();await message(page,'完整备份已创建');await act(page,'delete-backup').click();await confirm(page,false);assert.equal((await api('/data/state')).backups.length,1);
  await act(page,'delete-backup').click();await confirm(page);await message(page,'选中的备份文件已删除');assert.equal((await api('/data/state')).backups.length,0);assert.equal((await api('/data/state')).summary.articles,5);
});

test('preview cancellation removes the candidate and keeps current learning data',async t=>{
  const {page,api,context}=await fixture(t);const saved=await backup(api,context);await uploaded(page,'restore',saved.buffer);await act(page,'cancel-preview').click();await page.waitForFunction(()=>!document.querySelector('#data-restore-preview .data-preview'));assert.equal((await api('/data/state')).summary.articles,5);
});

test('same-generation reload keeps reading drafts',async t=>{
  const {page}=await fixture(t);await page.evaluate(()=>sessionStorage.setItem('margin-reading-draft-123','existing draft'));await page.reload();await page.locator('#data-storage strong').first().waitFor();assert.equal(await page.evaluate(()=>sessionStorage.getItem('margin-reading-draft-123')),'existing draft');
});

test('leaving while a preview returns cannot replace the new route',async t=>{
  const {page,api,context}=await fixture(t);const saved=await backup(api,context);let release,accepted;const blocked=new Promise(r=>release=r),received=new Promise(r=>accepted=r);
  await page.route('**/api/data/restore/preview',async route=>{const response=await route.fetch();accepted();await blocked;await route.fulfill({response}).catch(()=>{});});
  await page.locator('#data-restore-form input').setInputFiles({name:'backup.zip',mimeType:'application/zip',buffer:saved.buffer});await page.locator('#data-restore-form button').click();await received;await page.getByRole('link',{name:'我的生词',exact:true}).click();await page.locator('#wb-search').waitFor();release();await page.waitForLoadState('networkidle');assert.equal(await page.locator('.data-preview').count(),0);
});

test('offline articles can be imported and auto-backup preference persists',async t=>{
  const {page,api}=await fixture(t);await page.locator('#data-offline').check();await message(page,'设置已保存');await page.locator('#data-auto').check();await message(page,'设置已保存');
  const pack={format:'margin-articles-v1',articles:[{title:'Offline import',category:'生活',source_url:'https://example.com/article',content:'The city has a small park. '.repeat(10)}]};await uploaded(page,'articles',Buffer.from(JSON.stringify(pack)));await act(page,'apply-articles').click();await message(page,'已新增 1 篇文章');
  await page.reload();await page.locator('#data-storage strong').first().waitFor();assert.equal(await page.locator('#data-auto').isChecked(),true);assert.equal((await api('/data/state')).summary.articles,6);
});

test('desktop data manager screenshot uses disposable test records',async t=>{
  const {page,api}=await fixture(t);await added(api);await act(page,'backup').click();await message(page,'完整备份已创建');if(process.env.READING_TEST_SCREENSHOTS){fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});await page.locator('#app').evaluate(n=>n.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,'data-management-desktop.png'),fullPage:true});}
});

test('mobile data manager and restore preview stay inside the viewport',async t=>{
  const {page,api,context}=await fixture(t,{mobile:true});const saved=await backup(api,context);await uploaded(page,'restore',saved.buffer);const bounds=await page.evaluate(()=>({document:document.documentElement.scrollWidth<=innerWidth+1,app:document.querySelector('#app').scrollWidth<=document.querySelector('#app').clientWidth+1}));assert.deepEqual(bounds,{document:true,app:true});
  if(process.env.READING_TEST_SCREENSHOTS){fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});await page.locator('#app').evaluate(n=>n.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,'data-management-mobile.png'),fullPage:true});}
});


test('offline mode suppresses browser speech recognition while retaining local recording',async t=>{
  const {page,api}=await fixture(t);
  await page.addInitScript(()=>{
    window.__asrStarted=0;class Recognition{start(){window.__asrStarted++;}stop(){}}
    Object.defineProperty(window,'SpeechRecognition',{value:Recognition,configurable:true});
    Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{const audio=new AudioContext();await audio.resume();const oscillator=audio.createOscillator(),destination=audio.createMediaStreamDestination();oscillator.connect(destination);oscillator.start();const stream=destination.stream;for(const track of stream.getTracks()){const stop=track.stop.bind(track);track.stop=()=>{stop();audio.close().catch(()=>{});};}return stream;}}});
  });
  await page.goto(origin+'/#listening/1');await page.reload();await page.locator('#listen-main').waitFor();assert.equal(await page.evaluate(()=>typeof window.__asrStarted),'number');await page.locator('#listen-mode').selectOption('readalong');await page.locator('#listen-asr').check();
  await api('/data/preferences','PATCH',{offline:true});await page.locator('[data-listen=record]').click();await page.waitForFunction(()=>document.querySelector('#listen-record-status')?.textContent.includes('正在录音'));
  assert.equal(await page.evaluate(()=>window.__asrStarted),0);assert.equal(await page.locator('#listen-asr').isDisabled(),true);await page.locator('[data-listen=record-stop]').click();await page.locator('#listen-attempt').fill('Manual comparison remains available.');
});
