/* Real browser tests against Flask and a disposable SQLite database. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {before, after, test} = require('node:test');
const {chromium} = require('playwright');

const root = path.join(__dirname, '..');
let temporary, server, origin, options, defaults;
const content = Array.from({length:8}, (_, i) => `Paragraph ${i + 1}. We take into account the needs of every reader. ` +
  'A sustainable journey can make a difference to the city and its environment. '.repeat(8)).join('\n\n');
before(async () => {
  temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'margin-dictionary-'));
  const local = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const python = process.env.READING_TEST_PYTHON || (fs.existsSync(local) ? local : 'python');
  server = spawn(python, ['-u', '-c', `
import sys, json
import newsreader.content
from app import create_app
from werkzeug.serving import make_server
from newsreader.network import FetchError
def dictionary_fetch(url, **kwargs):
    if 'api.dictionaryapi.dev' not in url:
        raise FetchError('No external services in browser tests')
    word = url.rsplit('/', 1)[-1]
    if word == 'unlistedword':
        raise FetchError('Not found')
    entry = [{'word': word, 'phonetics': [{'text': '/UK-test/', 'audio': 'https://example.org/word-uk.mp3'}, {'text': '/US-test/', 'audio': 'https://example.org/word-us.mp3'}], 'meanings': [{'partOfSpeech': 'noun', 'definitions': [{'definition': 'Online definition for ' + word}], 'synonyms': ['example'], 'antonyms': []}]}]
    return json.dumps(entry).encode(), 'application/json', url
newsreader.content.fetch_public = dictionary_fetch
server = make_server('127.0.0.1', 0, create_app({'DATABASE': sys.argv[1]}), threaded=True)
print('PORT:' + str(server.server_port), flush=True)
server.serve_forever()
`, path.join(temporary, 'test.sqlite3')], {cwd:root, stdio:['ignore','pipe','pipe']});
  let output = '', errors = '';
  server.stderr.on('data', data => { errors = (errors + data).slice(-5000); });
  origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Server startup: ' + errors)), 15000);
    server.once('exit', code => {clearTimeout(timer); reject(new Error('Server exit ' + code + ': ' + errors));});
    server.stdout.on('data', data => {output += data; const port = output.match(/PORT:(\d+)/); if(port){clearTimeout(timer);resolve('http://127.0.0.1:' + port[1]);}});
  });
  options = {headless:true};
  if(process.env.READING_TEST_CHROMIUM_MODULE){
    const {default: runtime} = await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);
    options = {...options, executablePath:await runtime.executablePath(), args:runtime.args};
  }
  defaults = await (await fetch(origin + '/api/settings')).json();
});
after(async () => {
  if(server && server.exitCode === null){const stopped = new Promise(resolve=>server.once('exit',resolve));server.kill();await stopped;}
  if(temporary) fs.rmSync(temporary,{recursive:true,force:true});
});
async function pageFor(t, settings = {}, viewport = {width:1440,height:960}){
  const browser = await chromium.launch(options);
  t.after(()=>browser.close());
  const context = await browser.newContext({viewport});
  const api = async(endpoint,method='GET',data) => {
    const response=await context.request.fetch(origin+'/api'+endpoint,{method,data});
    assert.equal(response.ok(),true,await response.text());return response.json();
  };
  for(const word of (await api('/vocabulary')).words) await api('/vocabulary/'+word.id,'DELETE',{});
  await api('/settings','PATCH',{...defaults,reading_mode:'intensive',dictionary_provider:'offline',...settings});
  const article = await api('/articles','POST',{title:'Dictionary knowledge browser test',content});
  const page=await context.newPage(), errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  t.after(()=>assert.deepEqual(errors,[]));
  await page.goto(origin+'/#reader/'+article.id);
  await page.waitForFunction(()=>document.querySelector('#pending-word-count')?.textContent==='0');
  return {page,api,id:article.id};
}
async function query(page,word){
  await page.locator('[data-reader-action="lookup"]').click();
  await page.locator('#lookup-form input').fill(word);
  await page.locator('#lookup-form').press('Enter');
  await page.locator('.lookup-result').waitFor();
}
async function unfold(page){if(!await page.locator('.lookup-definition').evaluate(el=>el.open)) await page.locator('.lookup-definition > summary').click();}
async function anchor(page){return page.locator('#article-body [data-word]').evaluateAll(words=>{
  const top=document.querySelector('.reader-scroll').getBoundingClientRect().top+20;
  const index=words.findIndex(word=>word.getBoundingClientRect().top>=top);
  return {index,y:words[index].getBoundingClientRect().top};
});}
async function unchanged(page,previous){
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const y=await page.locator('#article-body [data-word]').nth(previous.index).evaluate(el=>el.getBoundingClientRect().top);
  assert.ok(Math.abs(y-previous.y)<3,'reading text moved by '+(y-previous.y)+'px');
}
async function screenshot(page,name){
  if(!process.env.READING_TEST_SCREENSHOTS)return;
  fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});
  await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,name+'.png')});
}

test('offline knowledge shows real metadata, regional IPA and navigable word families',async t=>{
  const {page}=await pageFor(t);
  await page.locator('[data-reader-action="next"]').click();
  await page.locator('[data-reader-action="next"]').click();
  const before=await anchor(page);
  await query(page,'sustainable');
  assert.equal(await page.locator('.lookup-definition').evaluate(el=>el.open),false);
  await unfold(page);
  await page.locator('.dictionary-knowledge > summary').click();
  assert.match(await page.locator('.dictionary-knowledge').innerText(),/sustainable development/);
  assert.match(await page.locator('.dictionary-knowledge').innerText(),/BNC 词频排名：7345/);
  assert.match(await page.locator('.dictionary-pronunciations').innerText(),/英式[\s\S]*美式/);
  await unchanged(page,before);
  await screenshot(page,'desktop-dictionary-knowledge');
  await page.getByRole('button',{name:'查词 sustainability',exact:true}).click();
  await page.locator('.lookup-title h2').filter({hasText:'sustainability'}).waitFor();
  assert.match(await page.locator('.definition').innerText(),/持续|维持|支撑/);
  await unchanged(page,before);
});

test('switching dictionaries uses the chosen source and keeps the preference after refresh',async t=>{
  const {page,api}=await pageFor(t);
  await query(page,'journey');await unfold(page);
  await page.getByRole('combobox',{name:'查询词典',exact:true}).selectOption('free');
  await page.waitForFunction(()=>document.querySelector('.definition')?.textContent.includes('Online definition'));
  assert.match(await page.locator('.source-caption').innerText(),/Free Dictionary/);
  await page.waitForFunction(()=>document.querySelector('#reader-settings-status').textContent==='阅读设置已保存');
  assert.equal((await api('/settings')).dictionary_provider,'free');
  await page.reload();
  await page.getByRole('combobox',{name:'查询词典',exact:true}).selectOption('offline');
  await query(page,'journey');await unfold(page);
  assert.match(await page.locator('.definition').innerText(),/旅程/);
  assert.doesNotMatch(await page.locator('.source-caption').innerText(),/Free Dictionary/);
});

test('a delayed online result cannot replace a newer offline query',async t=>{
  const {page}=await pageFor(t);
  let release;
  await query(page,'journey');await unfold(page);
  await page.route('**/api/dictionary?word=journey&provider=free',async route=>{await new Promise(resolve=>{release=resolve;});await route.continue();});
  await page.getByRole('combobox',{name:'查询词典',exact:true}).selectOption('free');
  await page.waitForFunction(()=>Boolean(document.querySelector('.lookup-loading')));
  await page.getByRole('combobox',{name:'查询词典',exact:true}).selectOption('offline');
  await query(page,'quiet');await unfold(page);
  release();
  await page.waitForResponse(response=>response.url().includes('word=journey&provider=free'));
  assert.equal(await page.locator('.lookup-title h2').innerText(),'quiet');
  assert.match(await page.locator('.definition').innerText(),/安静/);
});

test('changing the provider while the first lookup is loading immediately retries that same term',async t=>{
  const {page}=await pageFor(t,{dictionary_provider:'free'});
  let release;
  await page.route('**/api/dictionary?word=journey&provider=free',async route=>{await new Promise(resolve=>{release=resolve;});await route.continue();});
  await page.locator('[data-reader-action="lookup"]').click();
  await page.locator('#lookup-form input').fill('journey');
  await page.locator('#lookup-form').press('Enter');
  await page.locator('.lookup-loading').waitFor();
  await page.getByRole('combobox',{name:'查询词典',exact:true}).selectOption('offline');
  try {
    await page.locator('.lookup-result').waitFor({timeout:3000});
    await unfold(page);
    assert.match(await page.locator('.definition').innerText(),/旅程/);
  } finally { release?.(); }
});

test('selecting a phrase defers it and collection preserves the exact phrase and sentence',async t=>{
  const {page,api,id}=await pageFor(t,{reading_mode:'fluent'});
  const p=page.locator('#reader-p-0');
  await p.evaluate(paragraph=>{
    const words=[...paragraph.querySelectorAll('[data-word]')];
    const first=words.findIndex(word=>word.dataset.word==='take');
    const range=document.createRange();range.setStartBefore(words[first]);range.setEndAfter(words[first+2]);
    const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
    words[first+2].dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
  });
  await page.waitForFunction(()=>document.querySelector('#pending-word-count').textContent==='1');
  await page.locator('.pending-open').click();
  await page.locator('.pending-definition > summary').click();
  await page.locator('.pending-definition .definition').waitFor();
  assert.match(await page.locator('.pending-definition .definition').innerText(),/考虑/);
  await page.locator('[data-flow-op="collect"]').click();
  await page.waitForFunction(()=>document.querySelector('#pending-word-count').textContent==='0');
  const saved=(await api('/vocabulary')).words[0];
  assert.equal(saved.word,'take into account');assert.match(saved.context,/take into account/);assert.equal(saved.article_id,id);
  await page.getByRole('button',{name:'完成，继续阅读',exact:true}).click();
  await page.reload();
  assert.equal((await api('/vocabulary')).words[0].word,'take into account');
});

test('switching pending-list dictionaries rejects a stale response and shows fresh knowledge',async t=>{
  const {page}=await pageFor(t,{reading_mode:'fluent'});
  await page.locator('#reader-p-0 [data-word="sustainable"]').first().click();
  await page.waitForFunction(()=>document.querySelector('#pending-word-count').textContent==='1');
  await page.locator('.pending-open').click();await page.locator('.pending-definition > summary').click();
  await page.locator('.pending-definition .definition').waitFor();
  let release;
  await page.route('**/api/dictionary?word=sustainable&provider=free',async route=>{await new Promise(resolve=>{release=resolve;});await route.continue();});
  await page.getByRole('combobox',{name:'待查词词典',exact:true}).selectOption('free');
  await page.waitForFunction(()=>document.querySelector('.pending-definition-content')?.textContent.includes('正在查询'));
  await page.getByRole('combobox',{name:'待查词词典',exact:true}).selectOption('offline');
  await page.locator('.pending-definition .definition').waitFor();
  release();await page.waitForResponse(response=>response.url().includes('word=sustainable&provider=free'));
  assert.match(await page.locator('.pending-definition .definition').innerText(),/可持续/);
  assert.doesNotMatch(await page.locator('.pending-definition .source-caption').innerText(),/Free Dictionary/);
});

test('manual definitions survive provider changes and retry before being saved',async t=>{
  const {page,api}=await pageFor(t);
  await query(page,'unlistedword');await unfold(page);
  await page.locator('#manual-meaning').fill('我的手动释义');
  await page.getByRole('combobox',{name:'查询词典',exact:true}).selectOption('free');
  await page.locator('#manual-meaning').waitFor();
  assert.equal(await page.locator('#manual-meaning').inputValue(),'我的手动释义');
  await page.locator('[data-action="retry-lookup"]').click();
  await page.locator('#manual-meaning').waitFor();
  assert.equal(await page.locator('#manual-meaning').inputValue(),'我的手动释义');
  await page.locator('[data-action="save-lookup"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-action="save-lookup"]')?.textContent.includes('已加入生词本'));
  const words=(await api('/vocabulary')).words;
  assert.equal(words[0].meaning,'我的手动释义');assert.equal(words[0].mastery,'new');
});

test('dictionary values are escaped rather than executed as HTML',async t=>{
  const {page}=await pageFor(t);
  await page.route('**/api/dictionary?word=quiet&provider=offline',route=>route.fulfill({json:{
    word:'quiet',lemma:'quiet',found:true,meaning:'<img src=x onerror="window.dictionaryXss=true">',source:'<script>alert(1)</script>',
    knowledge:{synonyms:['<img src=x>'],antonyms:[],collocations:['<script>bad</script>'],family:[],forms:[],roots:[],confusables:[],frequency:{},levels:[]}
  }}));
  await query(page,'quiet');await unfold(page);await page.locator('.dictionary-knowledge > summary').click();
  assert.match(await page.locator('.definition').innerText(),/<img/);
  assert.equal(await page.locator('#learning-panel img, #learning-panel script').count(),0);
  assert.equal(await page.evaluate(()=>Boolean(window.dictionaryXss)),false);
});

test('mobile phrase lookup and knowledge fit the viewport and retain reading position',async t=>{
  const {page}=await pageFor(t,{reading_mode:'fluent'},{width:390,height:844});
  await page.locator('[data-reader-action="next"]').click();
  const before=await anchor(page);
  await query(page,'took into account');await unfold(page);
  assert.match(await page.locator('.dictionary-note').first().innerText(),/take into account/);
  await screenshot(page,'mobile-phrase-dictionary');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('[data-action="return-reading"]').click();await unchanged(page,before);
  await query(page,'sustainable');await unfold(page);await page.locator('.dictionary-knowledge > summary').click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await screenshot(page,'mobile-dictionary-knowledge');
});
