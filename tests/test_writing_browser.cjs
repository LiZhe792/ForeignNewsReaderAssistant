const {clickReaderTool}=require('./browser_helpers.cjs');
/* Optional browser regressions: node --test tests/test_writing_browser.cjs */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {before, after, test} = require('node:test');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '..');
let server, browser, temporary, origin;
const errors = [];
const articleText = Array.from({length:9},(_,i)=>`Section ${i+1}. A quiet journey gives readers time to think. The city values public gardens and shared space. Readers can learn to pay attention to the world around them.`).join('\n\n');

before(async()=>{
  temporary=fs.mkdtempSync(path.join(os.tmpdir(),'margin-writing-'));
  const localPython=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  const python=process.env.WRITING_TEST_PYTHON||process.env.READING_TEST_PYTHON||(fs.existsSync(localPython)?localPython:'python');
  server=spawn(python,['-B','-u','-c',`
import json, logging, sys, time
from flask import jsonify, request
from werkzeug.serving import make_server
from app import create_app
from newsreader import ai_client
application = create_app({'DATABASE':sys.argv[1], 'DEEPSEEK_API_KEY':'sk-writing-browser-fixture'})
metrics = {'calls':0, 'fail':False}
def generate(messages, config):
    metrics['calls'] += 1
    data = json.loads(messages[-1]['content'])
    answer = data['student_answer']
    if 'slow' in answer:
        time.sleep(1.2)
    if metrics['fail']:
        raise ai_client.AIError('模拟服务暂不可用')
    revised = answer.replace('She go', 'She goes')
    corrections = [{'kind':'grammar','before':'She go','after':'She goes','reason':'第三人称单数。'}] if 'She go' in answer else []
    return {'summary':'观点明确。<img src=x onerror=window.badWriting=1>', 'strengths':['结构清楚。'],
            'corrections':corrections, 'suggestions':['补充一个具体例子。'], 'revised':revised}
ai_client.generate = generate
@application.route('/__writing_test',methods=['GET','POST'])
def control():
    if request.method=='POST':
        metrics['fail']=bool(request.json.get('fail',False))
    return jsonify(metrics)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
server=make_server('127.0.0.1',0,application,threaded=True)
print('PORT:'+str(server.server_port),flush=True)
server.serve_forever()
`,path.join(temporary,'test.sqlite3')],{cwd:root,stdio:['ignore','pipe','pipe']});
  let output='',stderr='';
  server.stderr.on('data',data=>stderr=(stderr+data).slice(-6000));
  origin=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Server did not start: '+stderr)),15000);
    server.once('exit',code=>{clearTimeout(timer);reject(new Error('Server exited '+code+' '+stderr));});
    server.stdout.on('data',data=>{output+=data;const match=output.match(/PORT:(\d+)/);if(match){clearTimeout(timer);resolve('http://127.0.0.1:'+match[1]);}});
  });
  browser=await chromium.launch({headless:true,...(process.env.READING_TEST_CHROME?{executablePath:process.env.READING_TEST_CHROME}:{}),...(process.env.WRITING_TEST_BROWSER?{channel:process.env.WRITING_TEST_BROWSER}:{})});
});

after(async()=>{
  await browser?.close();
  if(server&&server.exitCode===null){const done=new Promise(resolve=>server.once('exit',resolve));server.kill();await done;}
  if(temporary){
    const resolved=path.resolve(temporary);
    assert.equal(path.dirname(resolved),path.resolve(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith('margin-writing-'));
    fs.rmSync(resolved,{recursive:true,force:true});
  }
  assert.deepEqual(errors,[],'Unexpected browser script errors');
});

async function api(context,url,method='GET',data){
  const response=await context.request.fetch(origin+url,{method,data});
  assert.equal(response.ok(),true,await response.text());
  return response.json();
}
async function go(page,id){
  await page.goto(origin+'/#reader/'+id);
  await page.waitForFunction(id=>typeof writingTrainer!=='undefined'&&writingTrainer?.article.id===id,id);
}
async function setup(t,options={}){
  const context=await browser.newContext({viewport:{width:1440,height:1050},...options});
  t.after(()=>context.close());
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  const article=await api(context,'/api/articles','POST',{title:'Writing '+t.name,content:articleText});
  await go(page,article.id);
  await page.waitForSelector('#article-body');
  await clickReaderTool(page,'[data-writing-action="open"]');
  await page.waitForFunction(()=>document.querySelector('#writing-mode').options.length===12);
  return {context,page,id:article.id};
}
async function create(page,mode='grammar'){
  await clickReaderTool(page,'[data-writing-action="new"]');
  await page.waitForSelector('#writing-setup',{state:'visible'});
  await page.locator('#writing-mode').selectOption(mode);
  if(mode==='vocabulary')await page.locator('#writing-targets').fill('journey, pay attention to');
  if(mode==='zh_en')await page.locator('#writing-source').fill('安静的旅行让读者有时间思考。');
  await page.locator('#writing-create').click();
  await page.waitForSelector('#writing-editor',{state:'visible'});
}
async function save(page,text){
  await page.locator('#writing-draft').fill(text);
  await clickReaderTool(page,'[data-writing-action="save"]');
  await page.waitForFunction(()=>document.querySelector('#writing-save-status').textContent==='草稿已保存');
}
async function feedback(page){
  await page.locator('#writing-submit').click();
  await page.waitForSelector('.writing-comparison');
  await page.waitForFunction(()=>!document.querySelector('#writing-draft').disabled);
}
async function reopen(page){
  await page.reload();await page.waitForSelector('#article-body');
  await clickReaderTool(page,'[data-writing-action="open"]');
  await page.waitForSelector('#writing-editor',{state:'visible'});
}
async function screenshot(page,name){
  if(process.env.WRITING_TEST_OUTPUT){fs.mkdirSync(process.env.WRITING_TEST_OUTPUT,{recursive:true});await page.screenshot({path:path.join(process.env.WRITING_TEST_OUTPUT,name+'.png')});}
}

test('all twelve exercise modes can save and receive grounded feedback',async t=>{
  const {page}=await setup(t);
  const modes=await page.locator('#writing-mode option').evaluateAll(items=>items.map(item=>item.value));
  for(const mode of modes){
    await create(page,mode);
    await save(page,mode==='en_zh'?'安静的旅行让读者有时间思考。':'She go on a journey and pay attention to the city.');
    await feedback(page);
    assert.equal(await page.locator('.writing-comparison').count(),1);
  }
  assert.equal(await page.locator('.writing-history-item').count(),12);
  await screenshot(page,'desktop-feedback');
});

test('selected source excludes paragraph controls',async t=>{
  const {page}=await setup(t);
  await clickReaderTool(page,'[data-writing-action="close"]');
  await page.evaluate(()=>{
    const paragraph=document.querySelector('.paragraph');
    const range=document.createRange();range.selectNodeContents(paragraph);
    const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
  });
  await clickReaderTool(page,'[data-writing-action="selection"]');
  await page.waitForFunction(()=>document.querySelector('#writing-source-choice').value==='selection');
  const source=await page.locator('#writing-source').inputValue();
  assert.ok(source.includes('A quiet journey'));
  assert.ok(!source.includes('处理待查词'));
  await page.locator('#writing-create').click();
  await page.waitForSelector('#writing-editor',{state:'visible'});
});

test('writing configuration is available directly and never echoes a saved key',async t=>{
  const {page,context}=await setup(t);
  await page.waitForFunction(()=>document.querySelector('#writing-config-state').textContent==='已配置');
  const before=(await api(context,'/__writing_test')).calls;
  await page.locator('#writing-configuration summary').click();
  assert.equal(await page.locator('#writing-api-key').inputValue(),'');
  assert.equal(await page.locator('#writing-api-key').isDisabled(),true);
  await page.locator('#writing-model').selectOption('deepseek-flash');
  await page.locator('#writing-config-save').click();
  await page.waitForFunction(()=>document.querySelector('#writing-config-status').textContent==='AI 配置已保存。');
  assert.equal((await api(context,'/__writing_test')).calls,before);
  assert.equal(await page.locator('#writing-api-key').inputValue(),'');
});

test('custom source survives switching exercise types before creation',async t=>{
  const {page}=await setup(t);
  await page.locator('#writing-source-choice').selectOption('custom');
  await page.locator('#writing-source').fill('My own source sentence should remain available.');
  await page.locator('#writing-mode').selectOption('expand');
  assert.equal(await page.locator('#writing-source').inputValue(),'My own source sentence should remain available.');
  await page.locator('#writing-source-choice').selectOption('1');
  await page.locator('#writing-source-choice').selectOption('custom');
  assert.equal(await page.locator('#writing-source').inputValue(),'My own source sentence should remain available.');
});

test('drafts survive reload, offline edits and switching between articles',async t=>{
  const {page,context,id}=await setup(t);
  await create(page);
  await save(page,'A saved draft.');
  await page.route('**/api/writing/exercises/*',route=>route.request().method()==='PATCH'?route.abort():route.continue());
  await page.locator('#writing-draft').fill('An offline draft survives.');
  await clickReaderTool(page,'[data-writing-action="save"]');
  await page.waitForFunction(()=>document.querySelector('#writing-save-status').textContent==='尚未保存，请重试');
  await reopen(page);
  assert.equal(await page.locator('#writing-draft').inputValue(),'An offline draft survives.');
  await page.unroute('**/api/writing/exercises/*');
  await save(page,'An offline draft survives.');
  const other=await api(context,'/api/articles','POST',{title:'Another article',content:articleText});
  await go(page,other.id);
  await clickReaderTool(page,'[data-writing-action="open"]');
  await page.waitForFunction(()=>document.querySelector('#writing-mode').options.length===12);
  assert.equal(await page.locator('#writing-editor').isVisible(),false);
  await go(page,id);
  await clickReaderTool(page,'[data-writing-action="open"]');await page.waitForSelector('#writing-editor',{state:'visible'});
  assert.equal(await page.locator('#writing-draft').inputValue(),'An offline draft survives.');
});

test('concurrent draft edits require explicit resolution without losing local text',async t=>{
  const {page,context}=await setup(t);
  await create(page);await save(page,'First tab draft.');
  const id=await page.evaluate(()=>writingTrainer.exercise.id);
  const current=(await api(context,'/api/writing/exercises/'+id)).exercise;
  await api(context,'/api/writing/exercises/'+id,'PATCH',{draft:'Changed in another tab.',version:current.version});
  await page.locator('#writing-draft').fill('My local version remains.');
  await clickReaderTool(page,'[data-writing-action="save"]');
  await page.waitForSelector('#writing-conflict',{state:'visible'});
  assert.equal(await page.locator('#writing-draft').inputValue(),'My local version remains.');
  assert.equal(await page.locator('#writing-server-draft').textContent(),'Changed in another tab.');
  await clickReaderTool(page,'[data-writing-action="keep-local"]');
  await page.waitForSelector('#writing-conflict',{state:'hidden'});
  await page.waitForFunction(()=>document.querySelector('#writing-save-status').textContent==='草稿已保存');
  assert.equal((await api(context,'/api/writing/exercises/'+id)).exercise.draft,'My local version remains.');
});

test('feedback snapshots and diff markers remain separate from later drafts',async t=>{
  const {page}=await setup(t);
  await create(page);await save(page,'She go on a journey.');await feedback(page);
  assert.ok(await page.locator('.writing-comparison del').count()>0);
  assert.ok(await page.locator('.writing-comparison ins').count()>0);
  await save(page,'A different draft.');
  assert.equal(await page.locator('.writing-comparison .writing-text').first().textContent(),'She go on a journey.');
  await feedback(page);
  await page.waitForFunction(()=>document.querySelector('#writing-attempt').options.length===2);
  const first=await page.locator('#writing-attempt option').last().getAttribute('value');
  await page.locator('#writing-attempt').selectOption(first);
  assert.equal(await page.locator('.writing-comparison .writing-text').first().textContent(),'She go on a journey.');
  assert.equal(await page.locator('#writing-feedback img').count(),0);
  assert.equal(await page.evaluate(()=>window.badWriting),undefined);
});

test('lost feedback response reuses the same request without another generation',async t=>{
  const {page,context}=await setup(t);
  await create(page);await save(page,'She go on a slow journey.');
  const before=(await api(context,'/__writing_test')).calls;
  await page.route('**/api/writing/exercises/*/attempts',async route=>{await route.fetch();await route.abort();},{times:1});
  await page.locator('#writing-submit').click();
  await page.waitForFunction(()=>document.querySelector('#writing-submit').textContent==='重试这次提交');
  assert.equal((await api(context,'/__writing_test')).calls,before+1);
  await page.locator('#writing-submit').click();
  await page.waitForFunction(()=>document.querySelector('#writing-submit').textContent==='获取反馈');
  assert.equal((await api(context,'/__writing_test')).calls,before+1);
  assert.equal(await page.locator('#writing-attempt option').count(),1);
});

test('failed generation is visible, keeps the draft and supports an explicit new attempt',async t=>{
  const {page,context}=await setup(t);
  await create(page);await save(page,'She go on a journey.');
  await api(context,'/__writing_test','POST',{fail:true});
  try {
    await page.locator('#writing-submit').click();
    await page.waitForFunction(()=>document.querySelector('#writing-feedback').textContent.includes('模拟服务暂不可用'));
  } finally {await api(context,'/__writing_test','POST',{fail:false});}
  assert.equal(await page.locator('#writing-draft').inputValue(),'She go on a journey.');
  await feedback(page);
  assert.equal(await page.locator('#writing-attempt option').count(),2);
});

test('closing and navigating during feedback cannot update the next article',async t=>{
  const {page,context,id}=await setup(t);
  await create(page);await save(page,'She go on a slow journey.');
  const exercise=await page.evaluate(()=>writingTrainer.exercise.id);
  await page.locator('#writing-submit').click();
  await page.waitForFunction(()=>document.querySelector('#writing-draft').disabled);
  await clickReaderTool(page,'[data-writing-action="close"]');
  const other=await api(context,'/api/articles','POST',{title:'Navigation target',content:articleText});
  await go(page,other.id);
  await clickReaderTool(page,'[data-writing-action="open"]');
  await page.waitForTimeout(1600);
  assert.equal(await page.locator('#writing-feedback').textContent(),'');
  assert.equal(await page.locator('#writing-editor').isVisible(),false);
  const stored=await api(context,'/api/writing/exercises/'+exercise);
  assert.equal(stored.exercise.article_id,id);
  assert.equal(stored.attempts[0].status,'complete');
});

test('deleting an exercise removes its drafts and history after confirmation',async t=>{
  const {page}=await setup(t);
  await create(page);await save(page,'She go on a journey.');await feedback(page);
  await clickReaderTool(page,'[data-writing-action="delete"]');
  await clickReaderTool(page,'[data-writing-action="cancel-delete"]');
  assert.equal(await page.locator('#writing-draft').inputValue(),'She go on a journey.');
  await clickReaderTool(page,'[data-writing-action="delete"]');
  await clickReaderTool(page,'[data-writing-action="confirm-delete"]');
  await page.waitForSelector('#writing-setup',{state:'visible'});
  await page.waitForFunction(()=>document.querySelectorAll('.writing-history-item').length===0);
});

test('mobile dark theme keeps the editor and comparison within the viewport',async t=>{
  const {page,context}=await setup(t,{viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await api(context,'/api/settings','PATCH',{theme:'dark'});
  await page.reload();await page.waitForSelector('#article-body');await clickReaderTool(page,'[data-writing-action="open"]');
  await create(page);await save(page,'She go on a journey.');await feedback(page);
  assert.equal(await page.evaluate(()=>document.body.dataset.readerTheme),'dark');
  assert.ok(await page.locator('#writing-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth+1));
  assert.equal(await page.locator('.writing-comparison').evaluate(node=>getComputedStyle(node).gridTemplateColumns.split(' ').length),1);
  await page.locator('.writing-comparison').scrollIntoViewIfNeeded();
  await screenshot(page,'mobile-feedback');
});
