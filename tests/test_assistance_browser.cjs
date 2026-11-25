const {clickReaderTool}=require('./browser_helpers.cjs');
/* Real Flask/SQLite/browser checks. The model service is explicitly synthetic. */
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
const root=path.join(__dirname,'..');
let temporary,server,origin,options,defaults;
const content=Array.from({length:4},(_,i)=>`😀 Paragraph ${i+1}. The bank of the river was quiet. It gave readers a place to rest and think. `+
  'The community turned a new page, and the readers chose this bank because the path followed the water. '.repeat(3)).join('\n\n');
before(async()=>{
  temporary=fs.mkdtempSync(path.join(os.tmpdir(),'margin-study-'));
  const local=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  server=spawn(process.env.READING_TEST_PYTHON||(fs.existsSync(local)?local:'python'),['-u','-c',`
import sys,json
sys.path.insert(0,'tests')
from ai_fixtures import context_result,translation_result
from app import create_app
from newsreader import ai
from werkzeug.serving import make_server
def synthetic_model(payload,config):
    material=json.loads(payload['input'])
    result=context_result(material['selected_text']) if material['task']=='context' else translation_result(material['selected_text'])
    if material['task']=='context': result['learning_note'] += ' Level: '+material['learning_level']
    return result
ai.request_json=synthetic_model
app=create_app({'DATABASE':sys.argv[1],'AI_API_KEY':'synthetic-test-key','AI_MODEL':'test-model'})
server=make_server('127.0.0.1',0,app,threaded=True)
print('PORT:'+str(server.server_port),flush=True)
server.serve_forever()
`,path.join(temporary,'study.sqlite3')],{cwd:root,stdio:['ignore','pipe','pipe']});
  let output='',errors='';server.stderr.on('data',data=>{errors=(errors+data).slice(-5000);});
  origin=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Server startup: '+errors)),15000);
    server.once('exit',code=>{clearTimeout(timer);reject(new Error('Server exit '+code+': '+errors));});
    server.stdout.on('data',data=>{output+=data;const m=output.match(/PORT:(\d+)/);if(m){clearTimeout(timer);resolve('http://127.0.0.1:'+m[1]);}});
  });
  options={headless:true};
  if(process.env.READING_TEST_CHROMIUM_MODULE){const {default:runtime}=await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);options={...options,executablePath:await runtime.executablePath(),args:runtime.args};}
  defaults=await(await fetch(origin+'/api/settings')).json();
});
after(async()=>{if(server&&server.exitCode===null){const stopped=new Promise(resolve=>server.once('exit',resolve));server.kill();await stopped;}if(temporary)fs.rmSync(temporary,{recursive:true,force:true});});
async function pageFor(t,settings={},viewport={width:1440,height:960}){
  const browser=await chromium.launch(options);t.after(()=>browser.close());
  const context=await browser.newContext({viewport});
  const api=async(endpoint,method='GET',data)=>{const response=await context.request.fetch(origin+'/api'+endpoint,{method,data});assert.equal(response.ok(),true,await response.text());return response.json();};
  await api('/settings','PATCH',{...defaults,dictionary_provider:'offline',reading_mode:'intensive',...settings});
  const article=await api('/articles','POST',{title:'AI reading browser test '+t.name,content});
  const page=await context.newPage(),errors=[],posts=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/assistance'))posts.push(request.postDataJSON());});
  t.after(()=>assert.deepEqual(errors,[]));
  await page.goto(origin+'/#reader/'+article.id);
  await page.waitForFunction(()=>document.querySelector('#pending-word-count')?.textContent==='0');
  return {page,api,id:article.id,posts};
}
const dialog=page=>page.locator('#study-dialog');
async function open(page,kind='translation'){
  await clickReaderTool(page,`[data-study-action="open-${kind==='context'?'context':'translation'}"]`);
  await page.locator('#study-paragraph option').first().waitFor({state:'attached'});
  await page.waitForFunction(()=>!document.querySelector('#study-generate').disabled);
}
async function generate(page){await page.locator('#study-generate').click();await page.waitForFunction(()=>document.querySelector('#study-message').textContent.startsWith('已完成'));}
const close=page=>page.getByRole('button',{name:'关闭 AI 学习面板',exact:true}).click();
const row=page=>page.locator('.study-result').first();
async function reveal(page){const details=page.locator('.study-translation').first();if(!await details.evaluate(el=>el.open))await details.locator(':scope > summary').click();}
async function edit(page){await reveal(page);await page.locator('[data-study-action="edit"]').first().click();}
async function captureAnchor(page){return page.locator('#article-body [data-word]').evaluateAll(words=>{const top=document.querySelector('.reader-scroll').getBoundingClientRect().top+20,index=words.findIndex(word=>word.getBoundingClientRect().top>=top);return {index,y:words[index].getBoundingClientRect().top};});}
async function unchanged(page,anchor){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));const y=await page.locator('#article-body [data-word]').nth(anchor.index).evaluate(el=>el.getBoundingClientRect().top);assert.ok(Math.abs(y-anchor.y)<3,'reading line moved by '+(y-anchor.y)+'px');}
async function screenshot(page,name){if(!process.env.READING_TEST_SCREENSHOTS)return;fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,name+'.png')});}

test('context explanation uses the selected occurrence, all learning categories and the chosen level',async t=>{
  const {page,api,id,posts}=await pageFor(t);
  await page.locator('#reader-p-0 [data-word="bank"]').first().click();
  await page.locator('.lookup-result').waitFor();
  await clickReaderTool(page,'[data-study-action="lookup-context"]');
  await page.waitForFunction(()=>!document.querySelector('#study-generate').disabled);
  assert.equal(await page.locator('#study-source-preview').innerText(),'bank');
  await page.locator('#study-level').selectOption('advanced');await generate(page);
  const text=await row(page).innerText();
  for(const label of ['本句中的含义','一词多义辨析','短语整体含义','隐喻与习语','语域与语气','上下文指代','新例句'])assert.match(text,new RegExp(label));
  assert.match(text,/Level: advanced/);assert.equal(posts[0].level,'advanced');
  assert.equal((await api(`/articles/${id}/assistance`)).results[0].source_text,'bank');
  await screenshot(page,'ai-context-explanation');
  await close(page);await page.reload();
  await open(page,'context');assert.equal(await page.locator('#study-level').inputValue(),'advanced');
});

test('a selected expression after an emoji uses code-point offsets and keeps the reading position',async t=>{
  const {page,api,id}=await pageFor(t,{reading_mode:'fluent'});
  await page.locator('[data-reader-action="next"]').click();
  const anchor=await captureAnchor(page);
  await page.locator('#reader-p-1').evaluate(paragraph=>{
    const words=[...paragraph.querySelectorAll('[data-word]')],word=words.filter(word=>word.dataset.word==='bank').at(-1);
    const range=document.createRange();range.selectNodeContents(word);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
    word.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
  });
  await clickReaderTool(page,'[data-study-action="explain-selection"]');
  await page.waitForFunction(()=>!document.querySelector('#study-generate').disabled);await generate(page);
  const record=(await api(`/articles/${id}/assistance`)).results[0];
  assert.equal(record.source_text,'bank');assert.equal(record.paragraph_index,1);assert.ok(record.start>100);
  await close(page);await unchanged(page,anchor);
});

test('word, sentence and paragraph translation each retain the exact original scope',async t=>{
  const {page,api,id}=await pageFor(t);
  await page.locator('#reader-p-0 [data-word="bank"]').first().click();await page.locator('.lookup-result').waitFor();
  await clickReaderTool(page,'[data-study-action="lookup-translation"]');
  await page.waitForFunction(()=>!document.querySelector('#study-generate').disabled);await generate(page);
  assert.equal(await page.locator('.study-original-source').innerText(),'bank');
  await page.locator('#study-scope').selectOption('sentence');await generate(page);
  await page.locator('#study-scope').selectOption('paragraph');await generate(page);
  const records=(await api(`/articles/${id}/assistance`)).results;
  assert.deepEqual(records.map(record=>record.scope),['word','sentence','paragraph']);
  assert.match(await row(page).innerText(),/直译[\s\S]*意译[\s\S]*重点表达[\s\S]*文化与背景/);
});

test('full translation retries only missing units and refresh restores folded aligned results',async t=>{
  const {page,api,id,posts}=await pageFor(t);await open(page);await page.locator('#study-scope').selectOption('full');
  let calls=0;
  await page.route('**/api/articles/*/assistance',async route=>{
    if(route.request().method()==='POST'&&++calls===2)return route.fulfill({status:502,json:{error:'temporary model failure'}});
    return route.continue();
  });
  await page.locator('#study-generate').click();
  await page.waitForFunction(()=>document.querySelector('#study-message').textContent.includes('temporary model failure'));
  assert.equal((await api(`/articles/${id}/assistance`)).results.length,1);
  await generate(page);assert.equal((await api(`/articles/${id}/assistance`)).results.length,4);
  assert.equal(posts.filter(post=>post.paragraph_index===0).length,1);
  await close(page);await page.reload();await open(page);await page.locator('#study-scope').selectOption('full');
  assert.equal(await page.locator('.study-result').count(),4);
  assert.equal(await page.locator('.study-translation[open]').count(),0);
  const before=posts.length;await generate(page);assert.equal(posts.length,before);
  await screenshot(page,'aligned-article-translation');
});

test('stopping a batch preserves completed work and allows a later continuation',async t=>{
  const {page,api,id}=await pageFor(t);await open(page);await page.locator('#study-scope').selectOption('full');
  let calls=0,release;
  await page.route('**/api/articles/*/assistance',async route=>{
    if(route.request().method()==='POST'&&++calls===2){await new Promise(resolve=>{release=resolve;});await route.continue().catch(()=>{});}
    else await route.continue();
  });
  await page.locator('#study-generate').click();
  await page.waitForFunction(()=>document.querySelector('#study-message').textContent.includes('2/4'));
  await page.locator('#study-stop').click();release?.();
  assert.match(await page.locator('#study-message').innerText(),/已停止/);
  assert.ok((await api(`/articles/${id}/assistance`)).results.length>=1);
  await close(page);await open(page);await page.locator('#study-scope').selectOption('full');await generate(page);
  assert.equal((await api(`/articles/${id}/assistance`)).results.length,4);
});

test('translation favorites, revisions and the original draft persist after refresh',async t=>{
  const {page,api,id}=await pageFor(t);await open(page);await generate(page);
  await clickReaderTool(page,'[data-study-action="favorite"]');
  await page.waitForFunction(()=>document.querySelector('[data-study-action="favorite"]').getAttribute('aria-pressed')==='true');
  await edit(page);
  await page.locator('[data-study-edit="literal"]').fill('我核对后的直译');
  await page.locator('[data-study-edit="natural"]').fill('我核对后的意译');
  await page.locator('[data-study-edit="note"]').fill('保留否定与语气');
  await clickReaderTool(page,'[data-study-action="original"]');
  assert.equal(await page.locator('[data-study-edit="note"]').inputValue(),'保留否定与语气');
  await page.locator('[data-study-edit="literal"]').fill('我核对后的直译');
  await page.locator('[data-study-edit="natural"]').fill('我核对后的意译');
  await clickReaderTool(page,'[data-study-action="save"]');
  await page.waitForFunction(()=>!document.querySelector('.study-draft-note')?.textContent.includes('尚未保存'));
  const saved=(await api(`/articles/${id}/assistance`)).results[0];
  assert.equal(saved.payload.natural,'我核对后的意译');assert.notEqual(saved.original.natural,saved.payload.natural);
  await close(page);await page.reload();await open(page);await reveal(page);
  assert.match(await row(page).innerText(),/我核对后的直译[\s\S]*我核对后的意译/);
  await page.locator('#study-favorites').check();assert.equal(await page.locator('.study-result').count(),1);
});

test('typing during a pending save preserves the newer input and its caret',async t=>{
  const {page,api,id}=await pageFor(t);await open(page);await generate(page);await edit(page);
  let release;
  await page.route('**/api/articles/*/assistance/*',async route=>{if(route.request().method()==='PATCH'){await new Promise(resolve=>{release=resolve;});await route.continue();}else await route.continue();});
  const field=page.locator('[data-study-edit="natural"]');await field.fill('第一版');
  await clickReaderTool(page,'[data-study-action="save"]');
  await field.fill('第二版，仍在输入');await field.evaluate(el=>el.setSelectionRange(2,5));
  release();await page.waitForFunction(()=>document.querySelector('#study-message').textContent.includes('之后的输入仍待保存'));
  assert.equal(await field.inputValue(),'第二版，仍在输入');assert.equal(await field.evaluate(el=>el.selectionStart),2);
  assert.equal((await api(`/articles/${id}/assistance`)).results[0].payload.natural,'第一版');
  await page.unroute('**/api/articles/*/assistance/*');await clickReaderTool(page,'[data-study-action="save"]');
  await page.waitForFunction(()=>document.querySelector('#study-message').textContent==='译文记录已保存。');
  assert.equal((await api(`/articles/${id}/assistance`)).results[0].payload.natural,'第二版，仍在输入');
});

test('a revision conflict preserves the local draft and requires an explicit second save',async t=>{
  const {page,api,id}=await pageFor(t);await open(page);await generate(page);await edit(page);
  await page.locator('[data-study-edit="natural"]').fill('保留本页输入');
  const record=(await api(`/articles/${id}/assistance`)).results[0];
  await api(`/articles/${id}/assistance/${record.id}`,'PATCH',{version:record.version,natural:'另一页已保存'});
  await clickReaderTool(page,'[data-study-action="save"]');await page.locator('.study-result [role="alert"]').waitFor();
  assert.equal(await page.locator('[data-study-edit="natural"]').inputValue(),'保留本页输入');
  assert.equal((await api(`/articles/${id}/assistance`)).results[0].payload.natural,'另一页已保存');
  await clickReaderTool(page,'[data-study-action="save"]');await page.waitForFunction(()=>!document.querySelector('.study-result [role="alert"]'));
  assert.equal((await api(`/articles/${id}/assistance`)).results[0].payload.natural,'保留本页输入');
});

test('newly arriving batch results preserve a draft, focus and selection in an earlier translation',async t=>{
  const {page}=await pageFor(t);await open(page);await page.locator('#study-scope').selectOption('full');
  let calls=0,release;
  await page.route('**/api/articles/*/assistance',async route=>{if(route.request().method()==='POST'&&++calls===2)await new Promise(resolve=>{release=resolve;});await route.continue();});
  await page.locator('#study-generate').click();await page.locator('.study-result').first().waitFor();await edit(page);
  const field=page.locator('[data-study-edit="natural"]').first(),draft=Array.from({length:30},(_,i)=>`尚未保存的草稿第 ${i+1} 行`).join('\n');
  await field.fill(draft);await field.evaluate(el=>{el.setSelectionRange(3,6);el.scrollTop=120;});
  const scroll=await field.evaluate(el=>el.scrollTop);
  release();await page.waitForFunction(()=>document.querySelector('#study-message').textContent.startsWith('已完成'));
  assert.equal(await field.inputValue(),draft);
  assert.equal(await field.evaluate(el=>document.activeElement===el),true);assert.equal(await field.evaluate(el=>el.selectionStart),3);
  assert.equal(await field.evaluate(el=>el.scrollTop),scroll);
});

test('failed generation can be retried without changing the selected source',async t=>{
  const {page}=await pageFor(t);await open(page,'context');const source=await page.locator('#study-source-preview').innerText();
  await page.route('**/api/articles/*/assistance',async route=>route.request().method()==='POST'?route.fulfill({status:502,json:{error:'invalid model output'}}):route.continue(),{times:1});
  await page.locator('#study-generate').click();await page.waitForFunction(()=>document.querySelector('#study-message').textContent.includes('invalid model output'));
  assert.equal(await page.locator('#study-source-preview').innerText(),source);await generate(page);assert.equal(await page.locator('.study-result').count(),1);
});

test('unconfigured service leaves saved translations visible and editable without a model call',async t=>{
  const {page,posts}=await pageFor(t);await open(page);await generate(page);await close(page);
  await page.route('**/api/assistance/status',route=>route.fulfill({json:{configured:false,model:'',message:'尚未配置 AI 服务。已有结果可查看。'}}));
  await clickReaderTool(page,'[data-study-action="open-translation"]');
  await page.waitForFunction(()=>document.querySelector('#study-service').textContent.includes('尚未配置'));
  assert.equal(await page.locator('#study-generate').isDisabled(),true);assert.equal(await page.locator('.study-result').count(),1);
  await edit(page);const before=posts.length;await page.locator('[data-study-edit="natural"]').fill('离线修订');await clickReaderTool(page,'[data-study-action="save"]');
  await page.waitForFunction(()=>document.querySelector('#study-message').textContent==='译文记录已保存。');assert.equal(posts.length,before);
});

test('navigation cancels the old panel and a late response never opens it in another article',async t=>{
  const {page,api}=await pageFor(t);await open(page,'context');let release;
  await page.route('**/api/articles/*/assistance',async route=>{if(route.request().method()==='POST')await new Promise(resolve=>{release=resolve;});await route.continue().catch(()=>{});});
  await page.locator('#study-generate').click();await page.waitForFunction(()=>document.querySelector('#study-stop').hidden===false);
  const other=await api('/articles','POST',{title:'Another article',content});
  await page.evaluate(id=>{location.hash='reader/'+id;},other.id);
  await page.waitForFunction(id=>state.article?.id===id,other.id);
  await page.getByRole('heading',{name:'Another article',exact:true}).waitFor();release?.();
  assert.equal(await dialog(page).evaluate(el=>el.open),false);
});

test('all generated text is rendered as text and never executed as HTML',async t=>{
  const {page}=await pageFor(t);await open(page);
  await page.route('**/api/articles/*/assistance',async route=>{
    if(route.request().method()!=='POST')return route.continue();
    const response=await route.fetch(),body=await response.json();body.result.payload.natural='<img src=x onerror="window.aiXss=true">';
    await route.fulfill({response,json:body});
  });
  await generate(page);assert.match(await row(page).innerText(),/<img/);
  assert.equal(await dialog(page).locator('img,script').count(),0);assert.equal(await page.evaluate(()=>Boolean(window.aiXss)),false);
});

test('mobile dark-mode translation fits the viewport and closing restores the reading line',async t=>{
  const {page}=await pageFor(t,{theme:'dark',reading_mode:'fluent'},{width:390,height:844});
  await page.locator('[data-reader-action="next"]').click();const anchor=await captureAnchor(page);
  await open(page);await generate(page);await edit(page);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(await dialog(page).evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);
  await screenshot(page,'mobile-translation-assistance');await close(page);await unchanged(page,anchor);
});
