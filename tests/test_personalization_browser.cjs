/* Real Flask / SQLite / Chromium. No external dictionary, AI or recommendation calls. */
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
const root=path.join(__dirname,'..');let directory,server,origin,options;
before(async()=>{
  directory=fs.mkdtempSync(path.join(os.tmpdir(),'margin-personal-'));
  server=spawn(process.env.READING_TEST_PYTHON||path.join(root,'.venv/bin/python'),['-u','-c',`
import sys
from flask import jsonify
from werkzeug.serving import make_server
from app import create_app
from newsreader.db import get_db
app=create_app({'DATABASE':sys.argv[1]})
@app.post('/__test/reset')
def reset():
    db=get_db()
    db.execute('DELETE FROM learner_profile');db.execute('DELETE FROM article_feedback')
    db.execute('DELETE FROM articles WHERE is_demo=0');db.execute('UPDATE articles SET progress=0,position=0,reading_seconds=0')
    db.commit();return jsonify(ok=True)
server=make_server('127.0.0.1',0,app,threaded=True)
print('PORT:'+str(server.server_port),flush=True)
server.serve_forever()
`,path.join(directory,'personal.sqlite3')],{cwd:root,stdio:['ignore','pipe','pipe']});
  let output='',errors='';server.stderr.on('data',b=>errors=(errors+b).slice(-6000));
  origin=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error(errors)),15000);server.once('exit',code=>{clearTimeout(timer);reject(new Error('Server '+code+': '+errors));});server.stdout.on('data',b=>{output+=b;const match=output.match(/PORT:(\d+)/);if(match){clearTimeout(timer);resolve('http://127.0.0.1:'+match[1]);}});});
  options={headless:true};if(process.env.READING_TEST_CHROMIUM_MODULE){const {default:runtime}=await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);options={...options,executablePath:await runtime.executablePath(),args:runtime.args};}
});
after(async()=>{if(server&&server.exitCode===null){const exited=new Promise(r=>server.once('exit',r));server.kill();await exited;}if(directory)fs.rmSync(directory,{recursive:true,force:true});});
async function fixture(t,{route='personalization',mobile=false}={}){
  const browser=await chromium.launch(options);t.after(()=>browser.close());const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000}});
  const api=async(endpoint,method='GET',data)=>{const response=await context.request.fetch(origin+'/api'+endpoint,{method,data});assert.equal(response.ok(),true,await response.text());return response.json();};
  await context.request.post(origin+'/__test/reset',{data:{}});
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));t.after(()=>assert.deepEqual(errors,[]));
  await page.goto(origin+'/#'+route);await page.locator(route==='library'?'#personal-library .personal-mini':'#personal-results .personal-card').first().waitFor();
  return{page,api,context};
}
const action=(p,name)=>p.locator(`[data-personal="${name}"]`);
const card=(p,id)=>p.locator(`[data-personal-card="${id}"]`);
async function updated(p){await p.waitForFunction(()=>document.querySelector('#personal-message')?.textContent.startsWith('已更新'));}
async function save(p){await p.locator('#personal-profile button[type=submit]').click();await p.locator('#personal-profile-status').getByText('已保存',{exact:true}).waitFor();await updated(p);}

test('cold start, preference save, persistence and explained metrics',async t=>{
  const {page,api}=await fixture(t);
  assert.match(await page.locator('#personal-evidence').innerText(),/记录待积累/);
  await page.locator('#personal-profile [name=level]').selectOption('B2');await page.locator('#personal-profile [name=minutes]').fill('8');await page.locator('#personal-profile [name=speed]').fill('120');await page.locator('[name=interests][value=科学]').check();
  await save(page);const stored=await api('/personalization/profile');assert.equal(stored.preferences.level,'B2');assert.deepEqual(stored.preferences.interests,['科学']);
  const c=page.locator('.personal-card').first();await c.locator('.personal-analysis summary').click();assert.match(await c.innerText(),/手动掌握覆盖\s*0%/);assert.match(await c.innerText(),/词库未覆盖不等于生词/);
  if(process.env.READING_TEST_SCREENSHOTS){fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});await page.locator('#app').evaluate(node=>node.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,'personalization-desktop.png'),fullPage:true});}
  await page.reload();await page.locator('#personal-results .personal-card').first().waitFor();assert.equal(await page.locator('#personal-profile [name=level]').inputValue(),'B2');
});

test('filter selections survive saves and combine with include read',async t=>{
  const {page,api}=await fixture(t);await api('/articles/1/progress','PATCH',{progress:100,position:0,seconds:0});
  await page.locator('#personal-filters [name=include_read]').check();await updated(page);assert.equal(await card(page,1).count(),1);
  await page.locator('#personal-filters [name=category]').selectOption('科学');await updated(page);for(const text of await page.locator('.personal-card-meta').allTextContents())assert.match(text,/科学/);
  await page.locator('#personal-profile [name=exclude_read]').uncheck();await save(page);
  assert.equal(await page.locator('#personal-filters [name=category]').inputValue(),'科学');assert.equal(await page.locator('#personal-filters [name=include_read]').isChecked(),true);
  for(const text of await page.locator('.personal-card-meta').allTextContents())assert.match(text,/科学/);
});

test('feedback votes persist, hidden articles can be restored from history',async t=>{
  const {page,api}=await fixture(t);
  await card(page,1).locator('[data-personal=difficulty][data-value=hard]').click();await updated(page);
  assert.equal(await card(page,1).locator('[data-value=hard]').getAttribute('aria-pressed'),'true');
  await card(page,1).locator('[data-personal=interest][data-value=less]').click();await page.waitForFunction(()=>!document.querySelector('[data-personal-card="1"]'));await updated(page);
  await action(page,'feedback').click();await card(page,1).waitFor();
  assert.equal(await card(page,1).locator('[data-value=less]').getAttribute('aria-pressed'),'true');
  await card(page,1).locator('[data-personal=clear]').click();await page.waitForFunction(()=>!document.querySelector('[data-personal-card="1"]'));
  await action(page,'recommendations').click();await card(page,1).waitFor();
  assert.equal((await api('/personalization/articles/1')).feedback.interest,'neutral');
});

test('stale profile asks to reload without overwriting another page',async t=>{
  const {page,api}=await fixture(t);await api('/personalization/profile','PATCH',{version:0,level:'C1'});
  await page.locator('#personal-profile [name=level]').selectOption('A2');await page.locator('#personal-profile button[type=submit]').click();
  await action(page,'reload').waitFor();assert.match(await page.locator('#personal-message').innerText(),/其他页面/);
  assert.equal((await api('/personalization/profile')).preferences.level,'C1');assert.equal(await page.locator('#personal-profile [name=level]').inputValue(),'A2');
  await action(page,'reload').click();await page.waitForFunction(()=>document.querySelector('#personal-profile [name=level]')?.value==='C1');
});

test('stale feedback does not silently overwrite another vote',async t=>{
  const {page,api}=await fixture(t);const data=await api('/personalization/articles/1');await api('/personalization/articles/1/feedback','PUT',{version:0,article_hash:data.analysis.hash,difficulty:'hard'});
  await card(page,1).locator('[data-value=easy]').click();await action(page,'reload').waitFor();assert.equal((await api('/personalization/articles/1')).feedback.difficulty,'hard');
});

test('default reset preserves feedback and reading records',async t=>{
  const {page,api}=await fixture(t);await page.locator('#personal-profile [name=level]').selectOption('C2');await save(page);
  await card(page,1).locator('[data-value=hard]').click();await updated(page);
  await action(page,'reset').click();await page.waitForFunction(()=>document.querySelector('#personal-profile [name=level]')?.value==='B1');await updated(page);
  assert.equal((await api('/personalization/articles/1')).feedback.difficulty,'hard');assert.equal((await api('/articles')).articles.length,5);
});

test('failed refresh can be retried and keeps existing articles',async t=>{
  const {page}=await fixture(t);let fail=true;
  await page.route('**/api/personalization/recommendations?*',route=>fail?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Controlled temporary failure'})}):route.continue());
  await action(page,'refresh').click();await page.locator('#personal-message').getByText('Controlled temporary failure',{exact:false}).waitFor();assert.equal(await page.locator('.personal-card').count(),5);
  fail=false;await page.locator('#personal-message [data-personal=refresh]').click();await updated(page);
});

test('out of order filter responses do not paint stale results',async t=>{
  const {page}=await fixture(t);let release;const blocked=new Promise(r=>release=r);
  await page.route('**/api/personalization/recommendations?*',async route=>{if(new URL(route.request().url()).searchParams.get('category')==='科技')await blocked;await route.continue().catch(()=>{});});
  await page.locator('#personal-filters [name=category]').selectOption('科技');await page.locator('#personal-filters [name=category]').selectOption('文化');await updated(page);
  release();await page.waitForLoadState('networkidle');for(const text of await page.locator('.personal-card-meta').allTextContents())assert.match(text,/文化/);
});

test('leaving while recommendations load preserves the next route',async t=>{
  const {page}=await fixture(t);let release;const blocked=new Promise(r=>release=r);
  await page.route('**/api/personalization/recommendations?*',async route=>{await blocked;await route.continue().catch(()=>{});});
  await action(page,'refresh').click();await page.getByRole('link',{name:'我的生词',exact:true}).click();await page.locator('#wb-search').waitFor().catch(async()=>{await page.getByRole('heading',{name:'每个生词，都是新的可能。'}).waitFor();});
  release();await page.waitForLoadState('networkidle');assert.equal(await page.locator('#personal-results').count(),0);
});

test('library and reader expose personalization without changing reader position',async t=>{
  const {page,api}=await fixture(t,{route:'library'});assert.equal(await page.locator('#personal-library .personal-mini').count(),3);
  await page.locator('#personal-library .personal-mini').first().click();await page.locator('#article-body').waitFor();const id=Number(page.url().match(/reader\/(\d+)/)[1]);
  await page.locator('.reader-article-details > summary').click();
  await page.getByRole('link',{name:'个人难度与读后反馈'}).click();await card(page,id).waitFor();
  await card(page,id).locator('[data-value=fit]').click();await page.waitForFunction(()=>document.querySelector('[data-personal=difficulty][data-value=fit]')?.getAttribute('aria-pressed')==='true');
  await page.getByRole('link',{name:'← 继续阅读',exact:true}).click();await page.locator('#article-body').waitFor();assert.equal((await api('/articles/'+id)).progress<100,true);
});

test('mobile layout stays within viewport and supports profile and feedback',async t=>{
  const {page}=await fixture(t,{mobile:true});
  await page.locator('#personal-profile [name=minutes]').fill('5');await save(page);
  await page.locator('.personal-card').first().locator('.personal-analysis summary').click();
  const overflow=await page.evaluate(()=>{const root=document.querySelector('#app');return {document:document.documentElement.scrollWidth>innerWidth+1,root:root.scrollWidth>root.clientWidth+1};});assert.deepEqual(overflow,{document:false,root:false});const check=page.locator('#personal-filters .personal-check');const box=await check.boundingBox();assert.ok(box.width>=110 && box.height<50,'checkbox label should stay on one line');
  if(process.env.READING_TEST_SCREENSHOTS){fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});await page.locator('#app').evaluate(node=>node.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,'personalization-mobile.png'),fullPage:true});}
});

test('article titles are rendered as text, including malicious-looking imports',async t=>{
  const {page,api}=await fixture(t);const title='<img src=x onerror="window.__unsafe=true">';await api('/articles','POST',{title,category:'生活',content:'The city has a small park. '.repeat(10)});
  await action(page,'refresh').click();await updated(page);await page.getByRole('link',{name:title,exact:true}).waitFor();assert.equal(await page.evaluate(()=>!!window.__unsafe),false);
});


test('late profile save cannot paint a newly opened route',async t=>{
  const {page,api}=await fixture(t);let release,committed;const blocked=new Promise(r=>release=r),accepted=new Promise(r=>committed=r);
  await page.route('**/api/personalization/profile',async route=>{if(route.request().method()!=='PATCH')return route.continue();const response=await route.fetch();committed();await blocked;await route.fulfill({response}).catch(()=>{});});
  await page.locator('#personal-profile [name=level]').selectOption('C1');await page.locator('#personal-profile button[type=submit]').click();await accepted;
  await page.getByRole('link',{name:'发现文章',exact:true}).click();await page.locator('#article-grid').waitFor();release();await page.waitForLoadState('networkidle');
  assert.equal(await page.locator('#personal-profile').count(),0);assert.equal((await api('/personalization/profile')).preferences.level,'C1');
  await page.getByRole('link',{name:'难度与推荐',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#personal-profile [name=level]')?.value==='C1');
});
