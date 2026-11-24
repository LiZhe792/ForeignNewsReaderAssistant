const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {test,before,after}=require('node:test');
const {chromium}=require('playwright');
let server,origin,temp,browser;
before(async()=>{
 temp=fs.mkdtempSync(path.join(os.tmpdir(),'margin-lab-'));
 server=spawn(process.env.READING_TEST_PYTHON||'python',['-u','-c',`import sys
from app import create_app
from werkzeug.serving import make_server
s=make_server('127.0.0.1',0,create_app({'DATABASE':sys.argv[1]}),threaded=True)
print('PORT:'+str(s.server_port),flush=True)
s.serve_forever()`,path.join(temp,'test.db')],{cwd:path.resolve(__dirname,'..'),stdio:['ignore','pipe','pipe']});
 origin=await new Promise((resolve,reject)=>{let out='',errors='';const timer=setTimeout(()=>reject(Error(errors)),20000);server.stderr.on('data',d=>errors+=d);server.on('exit',()=>reject(Error(errors)));server.stdout.on('data',d=>{out+=d;const m=out.match(/PORT:(\d+)/);if(m){clearTimeout(timer);resolve('http://127.0.0.1:'+m[1]);}});});
 browser=await chromium.launch({headless:true,executablePath:process.env.READING_TEST_CHROME||'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'});
});
after(async()=>{await browser?.close();if(server?.exitCode===null){const done=new Promise(r=>server.once('exit',r));server.kill();await done;}fs.rmSync(temp,{recursive:true,force:true});});

test('reader to recall, source navigation, scaffold and saved task pack',async t=>{
 const page=await browser.newPage({viewport:{width:1280,height:900}});t.after(()=>page.close());const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin+'/#reader/1');await page.locator('#article-body .word').first().waitFor();
 await page.locator('.reader-end a[href="#lab/1"]').click();await page.locator('[data-card=recall] summary').click();
 await page.locator('[data-field=recall-answer]').fill('A train journey can be part of a holiday.');await page.locator('[data-lab=recall-reveal]').click();
 for(let i=0;i<3;i++)await page.locator(`[data-field=point-${i}]`).selectOption(String(i));
 await page.locator('[data-field=remembered-0]').check();await page.locator('[data-lab=recall-save]').click();
 await page.getByText('遗漏的要点：').waitFor();await page.locator('[data-result=recall] a').first().click();await page.locator('#article-body .paragraph').first().waitFor();
 await page.goto(origin+'/#lab/1');await page.locator('[data-card=scaffold] summary').click();await page.locator('[data-field=sentence]').selectOption('0');
 await page.locator('[data-field=before]').fill('My first understanding');await page.locator('[data-field=structure]').fill('Subject and verb');
 await page.locator('[data-lab=stage-one]').click();await page.locator('[data-lab=stage-two]').click();await page.locator('[data-lab=fade]').click();await page.locator('[data-field=after]').fill('My revised understanding');await page.locator('[data-lab=scaffold-save]').click();
 await page.getByText('前后理解已保存',{exact:false}).first().waitFor();
 await page.getByText('主题连续阅读任务包',{exact:true}).click();await page.locator('[data-field=pack-title]').fill('My topic');
 for(let i=0;i<3;i++){await page.locator(`[data-field=pack-article-${i}]`).selectOption(String(i+1));await page.locator(`[data-field=pack-question-${i}]`).fill('Question '+i);}
 await page.locator('[data-field=pack-words]').fill('train, journey');await page.locator('[data-lab=pack-create]').click();await page.getByRole('heading',{name:'My topic'}).waitFor();
 await page.locator('[data-pack-answer="0"]').fill('A response');await page.locator('[data-pack-done="0"]').check();await page.locator('[data-lab=pack-save]').click();
 await page.reload();await page.getByText('主题连续阅读任务包',{exact:true}).click();await page.getByRole('heading',{name:'My topic'}).waitFor();
 assert.equal(await page.locator('[data-pack-answer="0"]').inputValue(),'A response');assert.equal(await page.locator('[data-pack-done="0"]').isChecked(),true);assert.deepEqual(errors,[]);
});

test('reading budget offers an optional reversible switch',async t=>{
 const page=await browser.newPage({viewport:{width:1280,height:900}});t.after(()=>page.close());
 const saved=await page.request.patch(origin+'/api/settings',{data:{reading_mode:'intensive'}});assert.equal(saved.status(),200);
 await page.goto(origin+'/#reader/1');await page.locator('#article-body .word').first().waitFor();
 await page.locator('.reader-scroll').evaluate(el=>{el.scrollTop=el.scrollHeight;});await page.waitForTimeout(250);
 await page.locator('.reader-scroll').evaluate(el=>{el.scrollTop=0;});await page.waitForTimeout(250);
 await page.locator('[data-reader-action=lookup]').evaluate(el=>{for(let i=0;i<30;i++)el.click();});
 await page.locator('[data-budget=accept]').waitFor();await page.locator('[data-budget=accept]').click();
 assert.equal(await page.locator('body').getAttribute('data-reading-mode'),'fluent');await page.locator('[data-budget=undo]').click();
 assert.equal(await page.locator('body').getAttribute('data-reading-mode'),'intensive');
});

test('expression practice records a later review',async t=>{
 const page=await browser.newPage();t.after(()=>page.close());
 const article=await (await page.request.get(origin+'/api/articles/1')).json();
 const context=article.content.split('\n')[0].trim();
 await page.request.patch(origin+'/api/articles/1/progress',{data:{seconds:2,progress:20,position:20,request_id:'lab_browser_progress'}});
 for(const word of ['station','crowd','sunset']){
  const response=await page.request.post(origin+'/api/vocabulary',{data:{word,meaning:'释义',context,article_id:1}});assert.equal(response.status(),201,await response.text());
 }
 await page.goto(origin+'/#lab/1');await page.getByText('个人表达迁移训练',{exact:true}).click();
 const checkboxes=page.locator('[data-expression]');await checkboxes.nth(0).check();await checkboxes.nth(1).check();await checkboxes.nth(2).check();
 await page.locator('[data-field=topic]').fill('A journey');await page.locator('[data-field=expression-answer]').fill('A crowd gathered at the station at sunset.');
 await page.locator('[data-lab=expression-check]').click();for(const select of await page.locator('[data-rating]').all())await select.selectOption('uncertain');
 await page.locator('[data-lab=expression-save]').click();await page.getByText('表达迁移已保存。').waitFor();
 await page.getByText('练习记录',{exact:true}).click();await page.locator('[data-lab=review-show]').first().click();
 for(const select of await page.locator('[data-review-rating]').all())await select.selectOption('appropriate');
 await page.locator('[data-lab=review-save]').first().click();await page.getByText('复习已保存，下次建议在七天后。').waitFor();
});
