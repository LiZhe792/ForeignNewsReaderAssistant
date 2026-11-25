/* Real Flask / SQLite / Chromium, with a controlled China clock and local-only inputs. */
const assert=require('node:assert/strict');const {spawn}=require('node:child_process');const fs=require('node:fs'),os=require('node:os'),path=require('node:path');const {pathToFileURL}=require('node:url');const {before,after,test}=require('node:test');const {chromium}=require('playwright');
const root=path.join(__dirname,'..');let server,directory,origin,options;
before(async()=>{
  directory=fs.mkdtempSync(path.join(os.tmpdir(),'margin-analytics-'));
  server=spawn(process.env.READING_TEST_PYTHON||path.join(root,'.venv/bin/python'),['-u','-c',`
import sys
from datetime import datetime,timedelta
from flask import jsonify,request
from werkzeug.serving import make_server
import app as application
from newsreader import analytics,srs,db,wordbook,reading_flow
current=datetime(2026,11,22,10,tzinfo=db.CHINA_TZ)
analytics.clock=lambda:current
srs.clock=lambda:current
for module in (application,analytics,db,wordbook,reading_flow):module.now=lambda:current.isoformat()
application.today=lambda:current.date()
app=application.create_app({'DATABASE':sys.argv[1]})
@app.post('/__test/reset')
def reset():
    global current
    current=datetime(2026,11,22,10,tzinfo=db.CHINA_TZ)
    conn=db.get_db()
    conn.execute('DELETE FROM vocabulary');conn.execute('DELETE FROM articles WHERE is_demo=0')
    for table in ['srs_events','pending_words','checkins','learning_reading_events','learning_word_events','learning_lookup_events']:conn.execute('DELETE FROM '+table)
    conn.execute('UPDATE articles SET progress=0,position=0,reading_seconds=0,completed_at=NULL,last_read_at=NULL');conn.commit()
    return jsonify(ok=True)
@app.post('/__test/advance')
def advance():
    global current
    current+=timedelta(seconds=request.json['seconds']);return jsonify(now=current.isoformat())
server=make_server('127.0.0.1',0,app,threaded=True);print('PORT:'+str(server.server_port),flush=True);server.serve_forever()
`,path.join(directory,'analytics.sqlite3')],{cwd:root,stdio:['ignore','pipe','pipe']});
  let out='',errors='';server.stderr.on('data',b=>errors=(errors+b).slice(-6000));origin=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error(errors)),15000);server.once('exit',c=>{clearTimeout(timer);reject(new Error('Server '+c+': '+errors));});server.stdout.on('data',b=>{out+=b;const m=out.match(/PORT:(\d+)/);if(m){clearTimeout(timer);resolve('http://127.0.0.1:'+m[1]);}});});
  options={headless:true};if(process.env.READING_TEST_CHROMIUM_MODULE){const {default:runtime}=await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);options={...options,executablePath:await runtime.executablePath(),args:runtime.args};}
});
after(async()=>{if(server&&server.exitCode===null){const exit=new Promise(r=>server.once('exit',r));server.kill();await exit;}if(directory)fs.rmSync(directory,{recursive:true,force:true});});
async function seed(api,t){
  const article=await api('/articles','POST',{title:'A park for every season '+t.name,category:'生活',content:('The city has a small park. '.repeat(50)+'\n\n').repeat(2)});
  for(const [progress,seconds] of [[20,30],[40,60],[100,0]])await api('/articles/'+article.id+'/progress','PATCH',{progress,position:progress,seconds,request_id:'browser_reading_'+progress});
  const entry=(await api('/wordbook','POST',{word:'city',meaning:'城市'})).entry;const park=await api('/vocabulary','POST',{word:'park',meaning:'公园'});await api('/vocabulary/'+park.id,'PATCH',{mastery:'mastered'});
  await api('/review/answer','POST',{word_id:entry.id,version:entry.schedule.version,content_version:entry.version,grade:4,mode:'spelling',attempt:'wrong',request_id:'analytics_spelling_seed'});
  const detail=await api('/articles/'+article.id);
  for(const paragraph of [0,1])await api('/analytics/lookup','POST',{request_id:'analytics_lookup_seed_'+paragraph,word:'city',article_id:article.id,article_hash:detail.content_hash,paragraph_index:paragraph});
  return article.id;
}
async function fixture(t,{seeded=false,mobile=false}={}){
  const browser=await chromium.launch(options);t.after(()=>browser.close());const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000}});await context.request.post(origin+'/__test/reset',{data:{}});
  const api=async(endpoint,method='GET',data)=>{const r=await context.request.fetch(origin+'/api'+endpoint,{method,data});assert.equal(r.ok(),true,await r.text());return r.json();};
  const id=seeded?await seed(api,t):null;const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
  await page.addInitScript(()=>{const NativeDate=Date;window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:['2026-11-22T10:00:00+08:00']));}static now(){return new NativeDate('2026-11-22T10:00:00+08:00').getTime();}};});
  await page.goto(origin+'/#analytics');await page.locator('.analytics-summary h2').waitFor();return{page,api,context,id};
}
const act=(p,id)=>p.locator(`[data-analytics="${id}"]`);
async function updated(p){await p.waitForFunction(()=>document.querySelector('#analytics-status')?.textContent.startsWith('已更新'));}

test('empty report distinguishes zero counts from missing speed and review samples',async t=>{
  const {page}=await fixture(t);assert.match(await page.locator('.analytics-summary').innerText(),/还没有可统计/);
  await page.locator('#analytics-read-metric').selectOption('speed_wpm');await page.locator('#analytics-reading-chart summary').click();assert.match(await page.locator('#analytics-reading-chart table').innerText(),/暂无样本/);
  assert.match(await page.locator('.analytics-review-grid').innerText(),/暂无样本/);assert.equal(await page.locator('#analytics-reading-chart [data-analytics-point]').count(),7);
});

test('week month and year show their exact dates and aggregate values',async t=>{
  const {page}=await fixture(t,{seeded:true});assert.match(await page.locator('.analytics-summary').innerText(),/活跃阅读 1.5 分钟，完成 1 篇文章，新增 2 条词条/);
  await act(page,'period').filter({hasText:'月报'}).click();await page.getByRole('heading',{name:'学习月报',exact:true}).waitFor();assert.match(await page.locator('#analytics-status').innerText(),/2026-11-01 — 2026-11-30/);assert.equal(await page.locator('#analytics-reading-chart [data-analytics-point]').count(),30);
  await act(page,'period').filter({hasText:'年度回顾'}).click();await page.getByRole('heading',{name:'年度学习回顾',exact:true}).waitFor();assert.equal(await page.locator('#analytics-reading-chart [data-analytics-point]').count(),12);
  await page.locator('#analytics-read-metric').selectOption('speed_wpm');await page.locator('#analytics-reading-chart summary').click();assert.match(await page.locator('#analytics-reading-chart table').innerText(),/160 词\/分钟/);
});

test('charts support keyboard selection and exact data tables',async t=>{
  const {page}=await fixture(t,{seeded:true});const point=page.getByRole('button',{name:'2026-11-22，阅读时长：1.5 分钟',exact:true});await point.press('Enter');
  assert.match(await page.locator('#analytics-point-detail').innerText(),/完成 1 篇.*新增 2 条.*查词 2 次/);
  await page.locator('#analytics-word-chart summary').click();assert.match(await page.locator('#analytics-word-chart table').innerText(),/2026-11-22\s+2 条/);
});

test('current mastery, article rate, encounters and review measures stay distinct',async t=>{
  const {page,id}=await fixture(t,{seeded:true});assert.match(await page.locator('.analytics-distribution').innerText(),/已掌握\s+1 条/);
  assert.match(await page.locator('#analytics-articles').innerText(),/16.7%/);assert.match(await page.locator('.analytics-review-grid').innerText(),/自评记住率\s+100%/);assert.match(await page.locator('.analytics-review-grid').innerText(),/拼写核对正确率\s+0%/);
  assert.match(await page.locator('.analytics-word-list').allTextContents().then(x=>x.join(' ')),/city.*2 次 · 1 篇正文 · 1 天/);
  await page.locator(`#analytics-articles a[href="#reader/${id}"]`).click();await page.locator('#article-body').waitFor();
});

test('previous period uses selected controls and month boundaries',async t=>{
  const {page}=await fixture(t);await act(page,'period').filter({hasText:'月报'}).click();await page.getByRole('heading',{name:'学习月报',exact:true}).waitFor();
  await act(page,'previous').click();await page.waitForFunction(()=>document.querySelector('#analytics-status')?.textContent.includes('2026-10-01 — 2026-10-31'));
  await page.locator('#analytics-controls [name=date]').fill('2024-02-29');await page.locator('#analytics-controls button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#analytics-status')?.textContent.includes('2024-02-01 — 2024-02-29'));
  await act(page,'next').click();await page.waitForFunction(()=>document.querySelector('#analytics-status')?.textContent.includes('2024-03-01 — 2024-03-31'));
});

test('CSV download contains exact daily rows and method notes',async t=>{
  const {page}=await fixture(t,{seeded:true});const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'导出每日数据 CSV ↓'}).click()]);assert.equal(download.suggestedFilename(),'margin-week-2026-11-16.csv');
  const file=await download.path();const text=fs.readFileSync(file,'utf8');assert.ok(text.startsWith('\ufeff'));assert.match(text,/2026-11-22,90,1,600,2,0,2,2,2,1,1,0,160/);assert.match(text,/口径说明/);
});

test('failed refresh retains the dated previous report and provides retry',async t=>{
  const {page}=await fixture(t,{seeded:true});let failing=true;await page.route('**/api/analytics/report?*',route=>failing?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Controlled report failure'})}):route.continue());
  await act(page,'period').filter({hasText:'月报'}).click();await page.locator('#analytics-status').getByText(/Controlled report failure/).waitFor();assert.match(await page.locator('#analytics-status').innerText(),/保留上一份报告/);assert.match(await page.locator('.analytics-summary').innerText(),/2026-11-16 — 2026-11-22/);
  failing=false;await act(page,'retry').click();await page.getByRole('heading',{name:'学习月报',exact:true}).waitFor();
});

test('late old report cannot replace a newer period',async t=>{
  const {page}=await fixture(t,{seeded:true});let release;const blocked=new Promise(r=>release=r);
  await page.route('**/api/analytics/report?*',async route=>{if(new URL(route.request().url()).searchParams.get('period')==='month')await blocked;await route.continue().catch(()=>{});});
  await act(page,'period').filter({hasText:'月报'}).click();await act(page,'period').filter({hasText:'年度回顾'}).click();await page.getByRole('heading',{name:'年度学习回顾',exact:true}).waitFor();release();await page.waitForLoadState('networkidle');assert.equal(await page.getByRole('heading',{name:'年度学习回顾',exact:true}).count(),1);
});

test('leaving during a report request preserves the new route',async t=>{
  const {page}=await fixture(t);let release;const blocked=new Promise(r=>release=r);await page.route('**/api/analytics/report?*',async route=>{await blocked;await route.continue().catch(()=>{});});
  await act(page,'period').filter({hasText:'月报'}).click();await page.getByRole('link',{name:'我的生词',exact:true}).click();await page.locator('#wb-search').waitFor();release();await page.waitForLoadState('networkidle');assert.equal(await page.locator('.analytics-summary').count(),0);
});

test('dictionary UI logs actual lookup without adding a vocabulary entry',async t=>{
  const {page,api}=await fixture(t);await page.getByRole('link',{name:'发现文章',exact:true}).click();await page.locator('#article-grid').waitFor();await page.locator('[data-open="1"]').first().click();await page.locator('#article-body').waitFor();
  await page.locator('[data-reader-action=lookup]').click();await page.locator('#lookup-form input').fill('city');await page.locator('#lookup-form button[type=submit]').click();await page.locator('.lookup-title').waitFor();
  await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('margin.lookup-outbox.v22')||'[]').length===0);
  const r=await api('/analytics/report?period=week&date=2026-11-22');assert.equal(r.totals.lookups,1);assert.equal(r.totals.vocabulary_added,0);
});

test('deferred lookup logs an encounter first and a query only on expansion',async t=>{
  const {page,api}=await fixture(t);const article=await api('/articles','POST',{title:'Deferred city',category:'生活',content:'The city has a small park. '.repeat(10)});
  await page.goto(origin+'/#reader/'+article.id);await page.locator('#article-body').waitFor();await page.locator('.paragraph [data-word="city"]').first().click();await page.waitForFunction(()=>document.querySelector('#pending-word-count')?.textContent==='1');
  let r=await api('/analytics/report?period=week&date=2026-11-22');assert.equal(r.totals.encounters,1);assert.equal(r.totals.lookups,0);
  await page.locator('[data-flow-action=queue]').first().click();await page.locator('#pending-words-dialog details[data-pending-id] summary').first().click();await page.locator('#pending-words-dialog .definition').waitFor();
  await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('margin.lookup-outbox.v22')||'[]').length===0);
  r=await api('/analytics/report?period=week&date=2026-11-22');assert.equal(r.totals.lookups,1);assert.equal(r.totals.encounters,1);
});

test('lost lookup acknowledgement retries the same stored observation after refresh',async t=>{
  const {page,api}=await fixture(t);let lost=false;await page.route('**/api/analytics/lookup',async route=>{if(!lost){lost=true;await route.fetch();return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Lost acknowledgement'})});}return route.continue();});
  await page.goto(origin+'/#reader/1');await page.locator('#article-body').waitFor();await page.locator('[data-reader-action=lookup]').click();await page.locator('#lookup-form input').fill('city');const acknowledgement=page.waitForResponse(r=>r.url().endsWith('/api/analytics/lookup')&&r.status()===503);await page.locator('#lookup-form button[type=submit]').click();await page.locator('.lookup-title').waitFor();await acknowledgement;
  await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('margin.lookup-outbox.v22')||'[]').length===1);assert.equal((await api('/analytics/report?period=week&date=2026-11-22')).totals.lookups,1);
  await page.reload();await page.locator('#article-body').waitFor();await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('margin.lookup-outbox.v22')||'[]').length===0);
  assert.equal((await api('/analytics/report?period=week&date=2026-11-22')).totals.lookups,1);
});

test('deleted article snapshots and malicious-looking titles render safely',async t=>{
  const {page,api,id}=await fixture(t,{seeded:true});await api('/articles/'+id,'DELETE',{});
  const title='<img src=x onerror="window.__unsafe=true">';await api('/articles','POST',{title,category:'科学',content:'The city has a small park. '.repeat(10)});
  await act(page,'period').filter({hasText:'月报'}).click();await page.getByRole('heading',{name:'学习月报',exact:true}).waitFor();
  assert.match(await page.locator('#analytics-articles').innerText(),/正文已改动或删除/);await page.getByRole('link',{name:title,exact:true}).waitFor();assert.equal(await page.evaluate(()=>!!window.__unsafe),false);
});

test('desktop report screenshot contains the real seeded totals',async t=>{
  const {page}=await fixture(t,{seeded:true});if(process.env.READING_TEST_SCREENSHOTS){fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});await page.locator('#app').evaluate(n=>n.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,'analytics-desktop.png'),fullPage:true});}
});

test('mobile report fits viewport, charts scroll and tables remain accessible',async t=>{
  const {page}=await fixture(t,{seeded:true,mobile:true});await act(page,'period').filter({hasText:'年度回顾'}).click();await page.getByRole('heading',{name:'年度学习回顾',exact:true}).waitFor();await page.locator('#analytics-reading-chart summary').click();
  const bounds=await page.evaluate(()=>({document:document.documentElement.scrollWidth<=innerWidth+1,app:document.querySelector('#app').scrollWidth<=document.querySelector('#app').clientWidth+1,scroll:document.querySelector('.analytics-chart').scrollWidth>document.querySelector('.analytics-chart').clientWidth}));assert.deepEqual(bounds,{document:true,app:true,scroll:true});
  if(process.env.READING_TEST_SCREENSHOTS){fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});await page.locator('#app').evaluate(n=>n.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,'analytics-mobile.png'),fullPage:true});}
});


test('historical missing lookup data is not displayed as zero',async t=>{
  const {page}=await fixture(t);await page.locator('#analytics-controls [name=date]').fill('2020-01-01');await page.locator('#analytics-controls button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#analytics-status')?.textContent.includes('2019-12-30 — 2020-01-05'));
  assert.match(await page.locator('.analytics-tiles').innerText(),/主动查词（已采集）\s+未采集/);
  await page.locator('#analytics-read-metric').selectOption('lookups');await page.locator('#analytics-reading-chart summary').click();assert.match(await page.locator('#analytics-reading-chart table').innerText(),/暂无样本/);
});
