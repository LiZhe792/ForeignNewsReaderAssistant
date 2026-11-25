const {clickReaderTool}=require('./browser_helpers.cjs');
/* Optional: node --test tests/test_knowledge_browser.cjs (Playwright required). */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {before, after, test} = require('node:test');
const {chromium} = require('playwright');
const root=path.resolve(__dirname,'..');
let temporary,server,browser,origin,articleIndex=0;
const errors=[];
const parts=Array.from({length:7},(_,i)=>`Section ${i+1}. A 🌱 quiet journey gives readers time to think. A quiet journey gives readers time to think. The city values public gardens and shared space. Readers can learn to pay attention to the world around them.`);
before(async()=>{
  temporary=fs.mkdtempSync(path.join(os.tmpdir(),'margin-knowledge-'));
  const local=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  server=spawn(process.env.KNOWLEDGE_TEST_PYTHON||process.env.READING_TEST_PYTHON||(fs.existsSync(local)?local:'python'),['-B','-u','-c',`
import logging, sys
from app import create_app
from werkzeug.serving import make_server
application=create_app({'DATABASE':sys.argv[1]})
logging.getLogger('werkzeug').setLevel(logging.ERROR)
server=make_server('127.0.0.1',0,application,threaded=True)
print('PORT:'+str(server.server_port),flush=True)
server.serve_forever()
`,path.join(temporary,'knowledge.sqlite3')],{cwd:root,stdio:['ignore','pipe','pipe']});
  let output='',stderr='';server.stderr.on('data',data=>stderr=(stderr+data).slice(-5000));
  origin=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Server startup: '+stderr)),15000);
    server.once('exit',code=>{clearTimeout(timer);reject(new Error('Server exit '+code+' '+stderr));});
    server.stdout.on('data',data=>{output+=data;const match=output.match(/PORT:(\d+)/);if(match){clearTimeout(timer);resolve('http://127.0.0.1:'+match[1]);}});
  });
  browser=await chromium.launch({headless:true,...(process.env.READING_TEST_CHROME?{executablePath:process.env.READING_TEST_CHROME}:{}),...(process.env.KNOWLEDGE_TEST_BROWSER?{channel:process.env.KNOWLEDGE_TEST_BROWSER}:{})});
});
after(async()=>{
  await browser?.close();
  if(server&&server.exitCode===null){const stopped=new Promise(resolve=>server.once('exit',resolve));server.kill();await stopped;}
  if(temporary){const target=path.resolve(temporary);assert.equal(path.dirname(target),path.resolve(os.tmpdir()));assert.ok(path.basename(target).startsWith('margin-knowledge-'));fs.rmSync(target,{recursive:true,force:true});}
  assert.deepEqual(errors,[],'Browser script errors');
});
async function api(context,url,method='GET',data){const r=await context.request.fetch(origin+'/api'+url,{method,data});assert.equal(r.ok(),true,await r.text());return r.json();}
async function setup(t,options={}){
  const context=await browser.newContext({viewport:{width:1440,height:1050},...options});t.after(()=>context.close());
  context.setDefaultTimeout(15000);
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const {id}=await api(context,'/articles','POST',{title:'The City We Share · '+(++articleIndex),content:parts.join('\n\n')});
  await go(page,id);return {page,context,id};
}
async function go(page,id){
  if(page.url()===origin+'/#reader/'+id)await page.reload();else await page.goto(origin+'/#reader/'+id);
  await page.waitForFunction(id=>typeof knowledgeOrganizer!=='undefined'&&knowledgeOrganizer?.article?.id===id&&document.querySelector('.k-bookmark:not(:disabled)'),id);
}
async function select(page,paragraph,quote,occurrence=0){
  return page.evaluate(({paragraph,quote,occurrence})=>{
    const text=knowledgeOrganizer.reader.paragraphs[paragraph].dataset.plain;
    let start=-1;for(let i=0;i<=occurrence;i++)start=text.indexOf(quote,start+1);
    if(start<0)throw new Error('Missing quote');
    const anchor={paragraph,start,end:start+quote.length,quote};
    const range=knowledgeOrganizer.rangeFor(anchor),selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);knowledgeOrganizer.captureSelection();
    return anchor;
  },{paragraph,quote,occurrence});
}
async function savedCount(page,count){await page.waitForFunction(count=>knowledgeOrganizer.items.length===count,count);}
async function library(page){await page.locator('a[data-nav="knowledge"]').click();await page.waitForSelector('#k-filter-form');await page.waitForFunction(()=>document.querySelector('#k-list-status').textContent.startsWith('已显示'));}
async function snapshot(page,name){if(process.env.KNOWLEDGE_SCREENSHOTS){fs.mkdirSync(process.env.KNOWLEDGE_SCREENSHOTS,{recursive:true});await page.waitForFunction(()=>!document.querySelector('#toast-region .toast'));await page.screenshot({path:path.join(process.env.KNOWLEDGE_SCREENSHOTS,name+'.png'),fullPage:true});}}

test('four highlight colors, repeated sentence anchors, bookmarks and reload preserve word and TTS markup',async t=>{
  const {page,context,id}=await setup(t);
  const words=await page.locator('#article-body .word').count(),sentences=await page.locator('.tts-sentence').count();
  const selections=['quiet journey','public gardens','shared space','🌱'];
  for(const [i,color] of ['yellow','green','blue','pink'].entries()){
    await select(page,0,selections[i]);await page.locator(`[data-k-action="highlight"][data-color="${color}"]`).click();await savedCount(page,i+1);
    assert.equal(await page.evaluate(color=>CSS.highlights.get('knowledge-'+color).size,color),1);
  }
  const anchor=await select(page,1,'quiet journey',1);
  await clickReaderTool(page,'[data-k-action="sentence"]');await savedCount(page,5);
  const sentence=(await api(context,`/articles/${id}/knowledge`)).items.find(i=>i.kind==='sentence');assert.deepEqual(sentence.anchors,[anchor]);
  await page.locator('.k-bookmark[data-paragraph="0"]').click();await savedCount(page,6);
  await page.locator('.k-bookmark[data-paragraph="0"]').click();await savedCount(page,5);
  await page.locator('.k-bookmark[data-paragraph="0"]').click();await savedCount(page,6);
  await go(page,id);await savedCount(page,6);
  assert.equal(await page.locator('.k-bookmark[data-paragraph="0"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#article-body .word').count(),words);assert.equal(await page.locator('.tts-sentence').count(),sentences);
  await page.locator('#reader-p-0 .word').filter({hasText:/^journey$/}).first().click();
  await page.waitForFunction(()=>Number(document.querySelector('#pending-word-count').textContent)>0);
  const pending=await api(context,`/articles/${id}/pending-words`);assert.ok(pending.words?.length||pending.items?.length||pending.pending_words?.length,JSON.stringify(pending));
  await snapshot(page,'reader-desktop');
});

test('multi-paragraph selection ignores controls; annotations, cards, tags and source round trips',async t=>{
  const {page,context,id}=await setup(t);
  await page.evaluate(()=>{
    const p=knowledgeOrganizer.reader.paragraphs;
    const first={paragraph:0,start:0,end:p[0].dataset.plain.length,quote:p[0].dataset.plain};
    const second={paragraph:1,start:0,end:10,quote:p[1].dataset.plain.slice(0,10)};
    const a=knowledgeOrganizer.rangeFor(first),b=knowledgeOrganizer.rangeFor(second);a.setEnd(b.endContainer,b.endOffset);
    const sel=getSelection();sel.removeAllRanges();sel.addRange(a);knowledgeOrganizer.captureSelection();
  });
  await clickReaderTool(page,'[data-k-action="annotate"]');await page.locator('#k-edit-form [name="title"]').fill('城市空间的启发');
  await page.locator('#k-edit-form [name="content"]').fill('将公共花园与社区参与联系起来。<img src=x onerror=window.badKnowledge=1>');
  await page.locator('#k-edit-form [name="tags"]').fill('环境, Writing, writing');
  await page.locator('#k-save').click();await savedCount(page,1);
  const item=(await api(context,`/articles/${id}/knowledge`)).items[0];assert.equal(item.anchors.length,2);assert.deepEqual(item.tags,['环境','writing']);
  assert.ok(!item.quote.includes('☆'));assert.ok(!item.quote.includes('读完此段'));
  await page.locator('.k-paragraph-notes[data-paragraph="0"]').click();
  assert.match(await page.locator('#k-margin-items').textContent(),/城市空间/);
  assert.equal(await page.locator('#k-margin-items img').count(),0);
  await page.locator(`#k-margin [data-k-action="make-card"][data-id="${item.id}"]`).click();
  await page.locator('#k-edit-form [name="title"]').fill('公共空间如何影响社区？');
  await page.locator('#k-save').click();await savedCount(page,2);
  await library(page);
  await page.locator('#k-filter-form [name="tag"]').fill('环境');await page.locator('#k-filter-form button[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('#k-list .k-card').length===2);
  await page.locator(`#k-list [data-k-action="source"][data-id="${item.id}"]`).click();
  await page.waitForFunction(id=>knowledgeOrganizer.article?.id===id&&document.querySelector('.k-source-focus'),id);
  assert.equal(await page.evaluate(()=>getSelection().toString()),item.anchors[0].quote);
  await page.locator('#k-margin [data-k-action="close-margin"]').click();await library(page);
  await clickReaderTool(page,'[data-k-action="reset-filter"]');await page.locator('#k-filter-form [name="kind"]').selectOption('card');await page.locator('#k-filter-form button[type="submit"]').click();
  await page.waitForSelector('.k-answer');await page.locator('.k-answer summary').first().click();assert.ok(await page.locator('.k-answer[open]').count());
  await snapshot(page,'knowledge-library');
});

test('free notes, grammar, expressions, flashcards and safe edits; failures and conflicting saves keep input',async t=>{
  const {page,context}=await setup(t);await library(page);
  for(const kind of ['grammar','note','expression','card']) {
    await page.locator('#k-new-kind').selectOption(kind);await clickReaderTool(page,'[data-k-action="new-item"]');
    await page.locator('#k-edit-form [name="title"]').fill(kind+' 素材');await page.locator('#k-edit-form [name="content"]').fill('A useful explanation.');
    if(kind==='expression')await page.locator('#k-edit-form [name="example"]').fill('A garden brings people together.');
    await page.locator('#k-save').click();await page.waitForFunction(()=>!document.querySelector('#k-editor').open);
  }
  await page.locator('#k-filter-form [name="kind"]').selectOption('expression');await page.locator('#k-filter-form button[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('#k-list .k-card').length===1);await page.locator('#k-list [data-k-action="edit-item"]').click();
  await page.waitForSelector('#k-editor[open]');
  const itemId=await page.evaluate(()=>knowledgeOrganizer.edit.id);
  await page.locator('#k-edit-form [name="content"]').fill('Local changes retained.');
  await api(context,'/knowledge/items/'+itemId,'PATCH',{version:0,content:'Changed in another window.'});
  await page.locator('#k-save').click();await page.waitForSelector('#k-edit-conflict:not([hidden])');
  assert.equal(await page.locator('#k-edit-form [name="content"]').inputValue(),'Local changes retained.');
  assert.match(await page.locator('#k-edit-conflict pre').textContent(),/another window/);
  await clickReaderTool(page,'[data-k-action="keep-item"]');await page.waitForFunction(()=>!document.querySelector('#k-editor').open);
  assert.equal((await api(context,'/knowledge/items/'+itemId)).item.content,'Local changes retained.');
  await page.locator('#k-list [data-k-action="edit-item"]').click();await page.locator('#k-edit-form [name="content"]').fill('Offline draft.');
  await page.route('**/api/knowledge/items/'+itemId,route=>route.request().method()==='PATCH'?route.abort():route.continue());
  await page.locator('#k-save').click();await page.waitForFunction(()=>document.querySelector('#k-edit-status').textContent.includes('无法连接'));
  assert.equal(await page.locator('#k-edit-form [name="content"]').inputValue(),'Offline draft.');
  await page.unroute('**/api/knowledge/items/'+itemId);await page.locator('#k-save').click();await page.waitForFunction(()=>!document.querySelector('#k-editor').open);
  await page.locator('#k-list [data-k-action="edit-item"]').click();await clickReaderTool(page,'[data-k-action="delete-item"]');await page.waitForFunction(()=>!document.querySelector('#k-editor').open);
  assert.equal((await context.request.get(origin+'/api/knowledge/items/'+itemId)).status(),404);
});

test('mind maps edit, reparent, link, collapse, persist and protect against stale versions',async t=>{
  const {page,context}=await setup(t);
  const {item}=await api(context,'/knowledge/items','POST',{request_id:crypto.randomUUID(),kind:'note',title:'Community gardens',content:'Shared space builds community.',tags:['environment']});
  await library(page);await clickReaderTool(page,'[data-k-action="new-map"]');
  await page.locator('#k-map-title').fill('城市与社区');await page.locator('#k-node-text').fill('城市与自然');
  await clickReaderTool(page,'[data-k-action="add-node"]');await page.locator('#k-node-text').fill('公共空间');
  const branch=await page.evaluate(()=>knowledgeOrganizer.nodeId);
  await page.locator('#k-link-search').fill('Community gardens');await clickReaderTool(page,'[data-k-action="search-link"]');
  await page.locator(`[data-k-action="link-node"][data-id="${item.id}"]`).click();
  await clickReaderTool(page,'[data-k-action="add-node"]');await page.locator('#k-node-text').fill('归属感');
  const child=await page.evaluate(()=>knowledgeOrganizer.nodeId);
  await page.locator('#k-node-parent').selectOption('root');assert.equal(await page.evaluate(()=>knowledgeOrganizer.selectedNode().parent),'root');
  await page.locator('#k-node-parent').selectOption(branch);
  await page.locator(`[data-k-action="fold-node"][data-node="${branch}"]`).click();assert.equal(await page.locator(`[data-k-action="select-node"][data-node="${child}"]`).count(),0);
  await page.locator(`[data-k-action="fold-node"][data-node="${branch}"]`).click();
  await page.locator('#k-map-save').click();await page.waitForFunction(()=>knowledgeOrganizer.map?.id&&!knowledgeOrganizer.mapDirty);
  const id=await page.evaluate(()=>knowledgeOrganizer.map.id);
  await snapshot(page,'knowledge-mindmap');
  await api(context,'/knowledge/maps/'+id,'PATCH',{version:0,title:'Other title',nodes:(await api(context,'/knowledge/maps/'+id)).map.nodes});
  await page.locator('#k-map-title').fill('我的城市导图');await page.locator('#k-map-save').click();await page.waitForSelector('#k-map-conflict:not([hidden])');
  assert.equal(await page.locator('#k-map-title').inputValue(),'我的城市导图');await clickReaderTool(page,'[data-k-action="keep-map"]');await page.waitForFunction(()=>!knowledgeOrganizer.mapDirty);
  await clickReaderTool(page,'[data-k-action="close-map"]');await clickReaderTool(page,'[data-k-action="maps-tab"]');await page.locator(`[data-k-action="open-map"][data-id="${id}"]`).click();
  await page.waitForSelector('#k-map[open]');
  assert.equal(await page.locator('#k-map-title').inputValue(),'我的城市导图');assert.equal(await page.locator('.k-node').count(),3);
  await page.locator(`[data-k-action="select-node"][data-node="${branch}"]`).click();await clickReaderTool(page,'[data-k-action="view-linked"]');
  await page.waitForSelector('#k-editor[open]');
  assert.equal(await page.locator('#k-edit-form [name="title"]').inputValue(),'Community gardens');await page.locator('[data-k-action="close-editor"]').first().click();
  await clickReaderTool(page,'[data-k-action="delete-node"]');assert.equal(await page.locator('.k-node').count(),1);
  await page.locator('#k-map-save').click();await page.waitForFunction(()=>!knowledgeOrganizer.mapDirty);await clickReaderTool(page,'[data-k-action="delete-map"]');await page.waitForFunction(()=>!document.querySelector('#k-map').open);
  assert.equal((await api(context,'/knowledge/items/'+item.id)).item.id,item.id);
});

test('mobile dark reader and fallback highlights keep text intact and source deletion remains explicit',async t=>{
  const {page,context,id}=await setup(t,{viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await api(context,'/settings','PATCH',{theme:'dark'});
  await go(page,id);
  assert.equal(await page.locator('body').getAttribute('data-reader-theme'),'dark');
  await page.evaluate(()=>{window.Highlight=undefined;});
  await select(page,0,'quiet journey');await clickReaderTool(page,'[data-k-action="highlight"][data-color="blue"]');await savedCount(page,1);
  assert.ok(await page.locator('mark[data-k-mark="blue"]').count());
  await select(page,0,'journey gives readers');await clickReaderTool(page,'[data-k-action="highlight"][data-color="pink"]');await savedCount(page,2);
  assert.equal(await page.evaluate(()=>{const p=knowledgeOrganizer.reader.paragraphs[0];return knowledgeOrganizer.textNodes(p).map(n=>n.node.textContent).join('')===p.dataset.plain;}),true);
  await page.locator('.k-paragraph-notes[data-paragraph="0"]').click();
  await page.locator('#k-margin [data-k-action="paragraph-note"]').click();await page.locator('#k-edit-form [name="content"]').fill('手机端页边批注');await page.locator('#k-save').click();await savedCount(page,3);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await snapshot(page,'knowledge-mobile-dark');
  await api(context,'/articles/'+id,'DELETE',{});
  await library(page);await page.locator('#k-filter-form [name="q"]').fill('手机端页边批注');await page.locator('#k-filter-form button[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('#k-list .k-card').length===1);
  assert.match(await page.locator('#k-list').textContent(),/原文已删除/);
  assert.equal(await page.locator('#k-list [data-k-action="source"]').count(),0);
  await page.locator('#k-list [data-k-action="edit-item"]').click();await page.locator('#k-source summary').click();assert.match(await page.locator('#k-source-content').textContent(),/摘录和笔记仍保留/);
});

test('back navigation can keep unsaved notes and saving cannot discard edits made in flight',async t=>{
  const {page,context}=await setup(t);await library(page);
  await page.locator('#k-new-kind').selectOption('note');await clickReaderTool(page,'[data-k-action="new-item"]');
  await page.locator('#k-edit-form [name="title"]').fill('An unfinished note');await page.locator('#k-edit-form [name="content"]').fill('Keep this draft.');
  page.removeAllListeners('dialog');page.once('dialog',d=>d.dismiss());
  await page.evaluate(()=>{location.hash='activity';});
  await page.waitForFunction(()=>location.hash==='#knowledge');
  assert.equal(await page.locator('#k-edit-form [name="content"]').inputValue(),'Keep this draft.');
  page.on('dialog',d=>d.accept());
  await page.route('**/api/knowledge/items',async route=>{
    if(route.request().method()==='POST')await new Promise(resolve=>setTimeout(resolve,300));
    await route.continue();
  });
  await page.locator('#k-save').click();
  assert.equal(await page.locator('#k-edit-form [name="content"]').isDisabled(),true);
  await page.evaluate(()=>{location.hash='activity';});
  await page.waitForFunction(()=>location.hash==='#knowledge');
  await page.waitForFunction(()=>!document.querySelector('#k-editor').open);
  assert.equal((await api(context,'/knowledge/items?q=An%20unfinished%20note')).items[0].content,'Keep this draft.');
});

test('a source deep in a long paragraph is visible after the initial reading position restores',async t=>{
  const {page,context}=await setup(t);
  const prefix='The community learns by sharing public spaces. '.repeat(110),quote='A memorable conclusion about belonging.',content=prefix+quote;
  const {id}=await api(context,'/articles','POST',{title:'A long paragraph',content});
  const {item}=await api(context,'/knowledge/items','POST',{request_id:crypto.randomUUID(),kind:'sentence',title:'The conclusion',article_id:id,anchors:[{paragraph:0,start:prefix.length,end:content.length,quote}]});
  await page.goto(origin+`/#reader/${id}/knowledge/${item.id}`);
  await page.waitForFunction(quote=>getSelection()?.toString()===quote,quote);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.evaluate(()=>{const rect=getSelection().getRangeAt(0).getClientRects()[0],box=knowledgeOrganizer.reader.scroller.getBoundingClientRect();return rect.top>=box.top&&rect.bottom<=box.bottom;}),true);
});
