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

test('all sidebar routes render their own module and writing workspace opens',async t=>{
 const p=await pageFor(t);
 for(const route of ['library','vocabulary','review','listening','exercises','personalization','analytics','data','plans','activity','knowledge','exports','feeds']){
  await p.goto(origin+'/#'+route);
  await p.waitForFunction(route=>typeof state!=='undefined'&&state.view===route&&!document.querySelector('#app .loading-screen'),route);
  assert.ok((await p.locator('#app').innerText()).length>30,route);
  assert.equal(await p.getByText('这页暂时没有翻开',{exact:true}).count(),0,route);
 }
 await p.goto(origin+'/writing');await p.locator('main').waitFor();
});

test('reader mounts QA, writing, analysis, translation, annotations and speech together',async t=>{
 const p=await pageFor(t);await p.goto(origin+'/#reader/1');await p.locator('#article-body .word').first().waitFor();
 for(const [button,dialog] of [['[data-qa-action="open"]','#article-qa-dialog'],['[data-writing-action="open"]','#writing-dialog'],['[data-analysis-open="sentence"]','#deep-analysis-dialog'],['[data-study-action="open-context"]','#study-dialog']]){
  await p.locator(button).click();await p.locator(dialog+'[open]').waitFor();await p.keyboard.press('Escape');await p.locator(dialog+'[open]').waitFor({state:'hidden'});
 }
 await p.locator('[data-k-action="margin"]').click();await p.locator('#k-margin').waitFor();
 await p.locator('[data-tts-action="toggle"]').click();await p.locator('#speech-player').waitFor();
 assert.ok(await p.locator('.tts-sentence').count());
});

test('file preview, correction and import open the reader',async t=>{
 const p=await pageFor(t);await p.goto(origin);await p.locator('#article-grid .article-card').first().waitFor();
 await p.locator('.topbar [data-action="import"]').click();await p.locator('[data-import-tab="file"]').click();
 const content='A careful reader takes time to understand the article. '.repeat(10);
 await p.locator('#import-file').setInputFiles({name:'integration.txt',mimeType:'text/plain',buffer:Buffer.from(content)});
 await p.waitForFunction(()=>document.querySelector('#file-status').textContent.includes('已提取'));
 await p.locator('#import-form [name="title"]').fill('Integrated file preview');await p.locator('#import-submit').click();
 await p.locator('.reader-title').waitFor();assert.equal(await p.locator('.reader-title').innerText(),'Integrated file preview');
});
