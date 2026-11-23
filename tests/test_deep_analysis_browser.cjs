/* Browser tests use a temporary database and mock the model response, not a paid AI service. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {before, after, test} = require('node:test');
const {chromium} = require('playwright');

const root=path.join(__dirname,'..');
const python=process.env.READING_TEST_PYTHON || 'python';
const executablePath=process.env.READING_TEST_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
let server, origin, temporary, browser;
before(async()=>{
  temporary=fs.mkdtempSync(path.join(os.tmpdir(),'margin-deep-'));
  server=spawn(python,['-u','-c',`import sys
from app import create_app
from werkzeug.serving import make_server
s=make_server('127.0.0.1',0,create_app({'DATABASE':sys.argv[1]}),threaded=True)
print('PORT:'+str(s.server_port),flush=True)
s.serve_forever()`,path.join(temporary,'test.sqlite3')],{cwd:root,stdio:['ignore','pipe','pipe']});
  origin=await new Promise((resolve,reject)=>{
    let output='',errors='';const timer=setTimeout(()=>reject(new Error('Server failed: '+errors)),20000);
    server.stderr.on('data',data=>errors+=data);server.once('exit',code=>reject(new Error('Server exited: '+code+' '+errors)));
    server.stdout.on('data',data=>{output+=data;const m=output.match(/PORT:(\d+)/);if(m){clearTimeout(timer);resolve('http://127.0.0.1:'+m[1]);}});
  });
  browser=await chromium.launch({headless:true,executablePath});
});
after(async()=>{if(browser)await browser.close();if(server&&server.exitCode===null){const stopped=new Promise(resolve=>server.once('exit',resolve));server.kill();await stopped;}if(temporary)fs.rmSync(temporary,{recursive:true,force:true});});

async function reader(t, viewport={width:1300,height:900}){
  const page=await browser.newPage({viewport});t.after(()=>page.close());
  await page.goto(origin+'/#reader/1');await page.locator('#article-body .paragraph').first().waitFor();return page;
}

test('unconfigured service explains setup without sending material',async t=>{
  const page=await reader(t);let requests=0;
  page.on('request',req=>{if(req.url().endsWith('/deep-analysis')&&req.method()==='POST')requests++;});
  await page.locator('[data-analysis-open="sentence"]').click();
  await page.locator('#deep-status').getByText('尚未配置 AI 服务',{exact:false}).waitFor();
  assert.equal(await page.locator('#deep-generate').isDisabled(),true);assert.equal(requests,0);
});

test('selected sentence and comparison render safe model output',async t=>{
  const page=await reader(t);let captured=[];
  await page.route('**/api/deep-analysis/status',route=>route.fulfill({json:{configured:true,model:'fake-model'}}));
  await page.route('**/api/articles/*/deep-analysis',async route=>{
    const body=route.request().postDataJSON();captured.push(body);
    const result=body.mode==='sentence'?{meaning:'解析 <img src=x>',main_clause:'主干',components:'',clauses:'',nonfinite:'',special_structures:'',references:'',connectors:'',breakdown:'第一层',syntax_tree:'S → NP VP',simplification:'A simple sentence.',uncertainty:'',evidence:[]}:
      {summary:'摘要',structure:'结构',arguments:'论点',paragraph_logic:'段落关系',background:'',culture:'',rhetoric:'',stance_tone:'立场',fact_opinion:'区分',comparison:'对比结果',uncertainty:'',evidence:[]};
    await route.fulfill({json:{mode:body.mode,result,materials:[{article_id:1,title:'test'}]}});
  });
  const selected=await page.locator('#article-body .paragraph').first().evaluate(p=>{
    const w=document.createTreeWalker(p,NodeFilter.SHOW_TEXT);let node,start,end,offset;while(node=w.nextNode()){if(!start)start=node;const at=node.data.indexOf('.');if(at>=0){end=node;offset=at+1;break;}}
    const range=document.createRange();range.setStart(start,0);range.setEnd(end,offset);const s=window.getSelection();s.removeAllRanges();s.addRange(range);p.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));return s.toString().trim();
  });
  await page.locator('[data-analysis-open="sentence"]').click();
  await page.locator('#deep-generate:not([disabled])').waitFor();
  assert.equal((await page.locator('#deep-source').textContent()).trim(),selected);
  await page.locator('#deep-generate').click();await page.getByText('第一层',{exact:true}).waitFor();
  assert.equal(captured[0].selected_text,selected);assert.equal(await page.locator('#deep-results img').count(),0);
  await page.locator('[data-analysis-action="close"]').first().click();
  await page.locator('[data-analysis-open="article"]').click();await page.locator('#deep-generate:not([disabled])').waitFor();
  await page.locator('#deep-compare').selectOption(['2','3']);await page.locator('#deep-generate').click();
  await page.getByText('对比结果',{exact:true}).waitFor();assert.deepEqual(captured[1].compare_ids,[2,3]);
});

test('narrow reader exposes paragraph analysis without horizontal overflow',async t=>{
  const page=await reader(t,{width:390,height:844});
  await page.route('**/api/deep-analysis/status',route=>route.fulfill({json:{configured:true,model:'fake-model'}}));
  await page.locator('[data-analysis-open="article"]').click();await page.locator('#deep-generate:not([disabled])').waitFor();
  await page.locator('#deep-scope').selectOption('paragraph');
  assert.equal(await page.locator('#deep-compare-label').isHidden(),true);
  const dimensions=await page.locator('#deep-analysis-dialog').evaluate(el=>({scroll:el.scrollWidth,client:el.clientWidth}));
  assert.ok(dimensions.scroll<=dimensions.client+1,JSON.stringify(dimensions));
});
