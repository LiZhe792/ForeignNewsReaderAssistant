const {clickReaderTool}=require('./browser_helpers.cjs');
/* Local merge smoke: real Flask and Chromium; no external AI or paid calls. */
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {test,before,after}=require('node:test');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');let server,origin,temp,options;
before(async()=>{
 temp=fs.mkdtempSync(path.join(os.tmpdir(),'margin-integration-'));
 server=spawn(process.env.READING_TEST_PYTHON||'python',['-u','-c',`import sys
from app import create_app
from werkzeug.serving import make_server
s=make_server('127.0.0.1',0,create_app({'DATABASE':sys.argv[1]}),threaded=True)
print('PORT:'+str(s.server_port),flush=True)
s.serve_forever()`,path.join(temp,'test.db')],{cwd:root,stdio:['ignore','pipe','pipe']});
 let output='',errors='';server.stderr.on('data',d=>errors=(errors+d).slice(-4000));
 origin=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(errors)),15000);server.stdout.on('data',d=>{output+=d;const m=output.match(/PORT:(\d+)/);if(m){clearTimeout(timer);resolve('http://127.0.0.1:'+m[1]);}});server.once('exit',()=>{clearTimeout(timer);reject(Error(errors));});});
 options={headless:true};
 if(process.env.READING_TEST_CHROMIUM_MODULE){const {default:c}=await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);options={...options,executablePath:await c.executablePath(),args:c.args};}
});
after(async()=>{if(server?.exitCode===null){const stopped=new Promise(r=>server.once('exit',r));server.kill();await stopped;}fs.rmSync(temp,{recursive:true,force:true});});
async function pageFor(t){const b=await chromium.launch(options);t.after(()=>b.close());const page=await b.newPage({viewport:{width:1440,height:960}});page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=500)errors.push(r.status()+' '+r.url());});t.after(()=>assert.deepEqual(errors,[]));return page;}

test('phone first screen contains article text and tools remain reachable',async t=>{
 const p=await pageFor(t);await p.setViewportSize({width:390,height:844});await p.goto(origin+'/#reader/1');
 const word=p.locator('#article-body .word').first();await word.waitFor();
 const box=await word.boundingBox();assert.ok(box.y<800,JSON.stringify(box));
 assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await clickReaderTool(p,'[data-qa-action="open"]');await p.locator('#article-qa-dialog[open]').waitFor();await p.keyboard.press('Escape');
 await p.evaluate(()=>document.documentElement.style.fontSize='200%');
 assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
});

test('downloaded reading and vocabulary work without network and synchronize afterwards',async t=>{
 const p=await pageFor(t);await p.goto(origin+'/#reader/1');await p.locator('#article-body').waitFor();
 await p.evaluate(()=>navigator.serviceWorker.ready);
 await p.getByRole('button',{name:'保存到离线阅读',exact:true}).click();
 await p.getByRole('button',{name:'已保存 · 可离线阅读',exact:true}).waitFor();
 await p.goto(origin+'/offline');await p.locator('[data-open="1"]').waitFor();
 await p.context().setOffline(true);await p.reload();await p.locator('[data-open="1"]').click();
 await p.locator('#offline-word').fill('garden');await p.locator('#offline-lookup').click();
 await p.waitForFunction(()=>document.querySelector('#offline-meaning').value.length>0);
 await p.locator('#offline-word-form button').last().click();
 await p.waitForFunction(()=>document.querySelector('#offline-count').textContent.includes('1'));
 await p.context().setOffline(false);await p.locator('#offline-sync').click();
 await p.waitForFunction(()=>document.querySelector('#offline-status').textContent.includes('全部已同步'));
 const words=await p.evaluate(()=>fetch('/api/vocabulary').then(r=>r.json()));assert.ok(words.words.some(w=>w.word==='garden'));
});

test('account hub, collection and learning dimensions work together',async t=>{
 const p=await pageFor(t);await p.goto(origin+'/hub?article=2#learning');await p.waitForFunction(()=>document.querySelector('#learning-article').value==='2');
 await p.getByText('词汇、句法与背景知识难度',{exact:true}).click();await p.locator('#dimensions-load').click();await p.waitForFunction(()=>document.querySelector('#dimensions').textContent.includes('句法'));
 await p.locator('#login [name=username]').fill('browseruser');await p.locator('#login [name=password]').fill('browser-password-123');await p.locator('#login [name=register]').check();
 await Promise.all([p.waitForNavigation(),p.locator('#login button').click()]);await p.waitForFunction(()=>document.querySelector('#identity').textContent.includes('browseruser'));
 await p.locator('#collection [name=title]').fill('Browser collection');await p.locator('#collection [name=article_ids]').selectOption(['1','2']);await p.locator('#collection button').first().click();
 await p.waitForFunction(()=>document.querySelector('#collection-list').textContent.includes('Browser collection'));
 await p.goto(origin+'/#reader/1');await p.locator('#article-body').waitFor();
 const saved=await p.evaluate(()=>fetch('/api/vocabulary',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({word:'evidence',meaning:'证据'})}).then(r=>r.status));assert.equal(saved,201);
 await p.goto(origin+'/hub');await p.locator('#logout').waitFor();await p.locator('#logout').click();await p.waitForFunction(()=>document.querySelector('#identity').textContent.includes('未登录'));
 const list=await p.evaluate(()=>fetch('/api/platform/collections').then(r=>r.json()));assert.equal(list.collections.length,0);
});

test('batch import stops after the current link and retries one failure without repeating successes',async t=>{
 const p=await pageFor(t);await p.goto(origin);await p.locator('#article-grid').waitFor();
 let release,entered;const started=new Promise(r=>entered=r),gate=new Promise(r=>release=r),calls=[];let failed=false;
 await p.route('**/api/articles/batch',async route=>{
  const {urls}=route.request().postDataJSON(),url=urls[0];calls.push(url);
  if(calls.length===1){entered();await gate;}
  const ok=!url.endsWith('/two')||failed;if(!ok)failed=true;
  await route.fulfill({json:{results:[{url,ok,error:ok?undefined:'测试超时，请重试'}],imported:ok?1:0}});
 });
 await p.locator('.topbar [data-action=import]').click();await p.locator('[data-import-tab=batch]').click();
 const input='https://example.org/one\nhttps://example.org/two\nhttps://example.org/three';
 await p.locator('[name=urls]').fill(input);await p.locator('#import-submit').click();await started;
 await p.locator('[data-import-queue=stop]').click();release();
 await p.locator('[data-import-queue="1"]').waitFor();assert.equal(calls.length,1);
 await p.locator('[data-import-queue="1"]').click();await p.waitForFunction(()=>document.querySelector('#batch-results').textContent.includes('测试超时'));
 await p.locator('[data-import-queue="1"]').click();await p.waitForFunction(()=>document.querySelectorAll('#batch-results .ok').length===2);
 assert.deepEqual(calls,['https://example.org/one','https://example.org/two','https://example.org/two']);
 assert.equal(await p.locator('[name=urls]').inputValue(),input);
});

test('switching accounts does not show or submit the previous offline queue',async t=>{
 const p=await pageFor(t);await p.goto(origin);
 const register=async name=>p.evaluate(async name=>{
  const r=await fetch('/api/writing-workspace/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:name,password:'browser-password-123',register:true})});if(!r.ok)throw Error('fixture registration failed');
 },name);
 await register('offlinealice');await p.reload();await p.locator('#article-grid').waitFor();
 const alice=await p.evaluate(async()=>{await MarginPlatform.ready;await MarginOffline.save(1,MarginPlatform.scope);await MarginOffline.queue({scope:MarginPlatform.scope,kind:'word',article_id:1,word:'isolation',meaning:'隔离'});return MarginPlatform.scope;});
 await register('offlinebob');await p.goto(origin+'/offline');await p.locator('#offline-count').waitFor();
 await p.waitForFunction(()=>document.querySelector('#offline-articles')?.textContent.includes('尚未保存'));
 assert.equal(await p.locator('[data-open]').count(),0);
 const retained=await p.evaluate(()=>MarginOffline.list('operations'));assert.ok(retained.some(o=>o.scope===alice));
 const words=await p.evaluate(()=>fetch('/api/vocabulary').then(r=>r.json()));assert.ok(!words.words.some(w=>w.word==='isolation'));
});

test('selected timezone and date formats are used without changing calendar date keys',async t=>{
 const p=await pageFor(t);await p.goto(origin);await p.locator('#article-grid').waitFor();
 const dates=await p.evaluate(async()=>{await MarginPlatform.ready;MarginPlatform.preferences={timezone:'America/New_York',date_format:'iso'};const iso=MarginPlatform.date('2026-11-25T01:30:00Z');const day=MarginPlatform.date('2026-11-25');MarginPlatform.preferences.date_format='dmy';return [iso,day,MarginPlatform.date('2026-11-25T01:30:00Z')];});
 assert.deepEqual(dates,['2026-11-24','2026-11-25','24/11/2026']);
});
