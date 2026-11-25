/* Real Flask and Chromium with a temporary database and a fixed Beijing day. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {before, after, test} = require('node:test');
const {chromium} = require('playwright');

const root = path.join(__dirname, '..');
let directory, server, origin, options;

before(async () => {
  directory = fs.mkdtempSync(path.join(os.tmpdir(), 'margin-plans-'));
  const local = path.join(root,'.venv',process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const python = process.env.READING_TEST_PYTHON || (fs.existsSync(local) ? local : 'python');
  server = spawn(python,['-u','-c',`
import sys
from datetime import datetime
from flask import jsonify
from werkzeug.serving import make_server
import app as application
from newsreader import study_plans, db
current=datetime(2026,11,25,20,1,tzinfo=db.CHINA_TZ)
application.today=lambda:current.date()
application.now=lambda:current.isoformat()
study_plans.today=application.today
study_plans.clock=lambda:current
app=application.create_app({'DATABASE':sys.argv[1],'TESTING':True})
@app.post('/__test/reset')
def reset():
    connection=db.get_db()
    connection.execute('DELETE FROM vocabulary')
    for table in ('study_plans','study_makeups','study_reminder_days','learning_word_events','checkins','learning_reading_events'):
        connection.execute('DELETE FROM '+table)
    connection.execute("DELETE FROM settings WHERE key='planning.goals'")
    connection.execute('UPDATE articles SET progress=0,position=0,completed_at=NULL,reading_seconds=0')
    connection.commit()
    return jsonify(ok=True)
server=make_server('127.0.0.1',0,app,threaded=True)
print('PORT:'+str(server.server_port),flush=True)
server.serve_forever()
`, path.join(directory,'test.sqlite3')], {cwd:root,stdio:['ignore','pipe','pipe']});
  let output = '', errors = '';
  server.stderr.on('data', chunk => errors = (errors + chunk).slice(-6000));
  origin = await new Promise((resolve,reject) => {
    const timer = setTimeout(()=>reject(new Error(errors)),15000);
    server.once('exit', code => {clearTimeout(timer);reject(new Error('Server '+code+': '+errors));});
    server.stdout.on('data', chunk => {
      output += chunk;
      const match = output.match(/PORT:(\d+)/);
      if (match) {clearTimeout(timer);resolve('http://127.0.0.1:'+match[1]);}
    });
  });
  options = {headless:true};
  if (process.env.READING_TEST_CHROMIUM_MODULE) {
    const {default:runtime} = await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);
    options = {...options,executablePath:await runtime.executablePath(),args:runtime.args};
  }
});

after(async () => {
  if (server && server.exitCode === null) {
    const exited = new Promise(resolve=>server.once('exit',resolve));
    server.kill();
    await exited;
  }
  if (directory) fs.rmSync(directory,{recursive:true,force:true});
});

async function fixture(t, mobile = false) {
  const browser = await chromium.launch(options);
  t.after(()=>browser.close());
  const context = await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000}});
  await context.request.post(origin+'/__test/reset',{data:{}});
  const api = async (endpoint,method='GET',data) => {
    const response = await context.request.fetch(origin+'/api'+endpoint,{method,data});
    assert.equal(response.ok(),true,await response.text());
    return response.json();
  };
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror',error=>errors.push(error.message));
  t.after(()=>assert.deepEqual(errors,[]));
  await page.goto(origin+'/#plans');
  await page.getByRole('heading',{name:'今天的目标'}).waitFor();
  return {page,api,context};
}

const act = (page, action) => page.locator(`[data-plan="${action}"]`);
const status = (page, text) => page.locator('#plan-status').getByText(text,{exact:false}).waitFor();

async function newWeek(page) {
  await act(page,'new-week').click();
  const editor = page.locator('#plan-editor');
  await editor.locator('[name=title]').fill('本周英文阅读');
  await editor.locator('[name=start_day]').fill('2026-11-25');
  await editor.locator('[name=steps]').fill('找出文章主旨\n写下三条要点');
  await editor.getByRole('button',{name:'保存计划',exact:true}).click();
  await status(page,'计划已保存');
}

test('daily goal settings use actual progress and update sidebar after reload', async t => {
  const {page,api} = await fixture(t);
  await api('/articles/1/progress','PATCH',{seconds:60,progress:20,position:20});
  await page.locator('.plan-settings > summary').click();
  await page.locator('#plan-goals-form [name=reading_minutes]').fill('7');
  await page.locator('#plan-goals-form [name=new_words]').fill('4');
  await page.getByRole('button',{name:'保存目标与提醒',exact:true}).click();
  await status(page,'目标与提醒已保存');
  assert.match(await page.locator('#daily-minutes').innerText(),/1 \/ 7 分钟/);
  assert.match(await page.locator('#daily-vocabulary').innerText(),/0 \/ 4 条/);
  await page.reload();
  await page.getByRole('heading',{name:'今天的目标'}).waitFor();
  assert.equal(await page.locator('#plan-goals-form [name=reading_minutes]').inputValue(),'7');
});

test('week plan handles non-Monday input, daily splits, steps and saved reflection', async t => {
  const {page,api} = await fixture(t);
  await newWeek(page);
  await page.locator('.plan-split summary').click();
  assert.equal(await page.locator('.plan-split tbody tr').count(),7);
  assert.match(await page.locator('.plan-detail').innerText(),/2026-11-23 至 2026-11-29/);
  await page.locator('#plan-reflection textarea').fill('找出主旨后更容易概括。');
  await page.getByRole('checkbox',{name:'找出文章主旨',exact:true}).check();
  await page.locator('.plan-steps legend').getByText('计划步骤 · 1 / 2',{exact:true}).waitFor();
  assert.equal(await page.locator('#plan-reflection textarea').inputValue(),'找出主旨后更容易概括。');
  await page.getByRole('button',{name:'保存阶段笔记',exact:true}).click();
  await status(page,'阶段笔记已保存');
  const id = (await api('/planning')).plans[0].id;
  await page.reload();
  await act(page,'open').click();
  assert.equal(await page.locator('#plan-reflection textarea').inputValue(),'找出主旨后更容易概括。');
  assert.equal((await api('/planning/plans/'+id)).plan.steps[0].done,true);
});

test('topic plan links to a filtered article library', async t => {
  const {page} = await fixture(t);
  await act(page,'new-topic').click();
  const form = page.locator('#plan-editor');
  await form.locator('[name=category]').selectOption('环境');
  await form.locator('[name=title]').fill('环境报道');
  await form.getByRole('button',{name:'保存计划',exact:true}).click();
  await status(page,'计划已保存');
  await page.getByRole('link',{name:'浏览环境文章 →',exact:true}).click();
  await page.locator('#article-grid').waitFor();
  assert.equal(await page.locator('#category-filter').inputValue(),'环境');
});

test('makeup is labeled separately in the original activity calendar', async t => {
  const {page,api} = await fixture(t);
  await api('/checkin','POST',{});
  await page.locator('#plan-makeup-form select').selectOption('2026-11-24');
  await page.locator('#plan-makeup-form [name=note]').fill('补记昨天的学习');
  await page.getByRole('button',{name:'记录补签',exact:true}).click();
  await status(page,'补签已记录');
  assert.match(await page.locator('.plan-streak-values').innerText(),/当前连续\s+2 天/);
  await page.getByRole('link',{name:'查看打卡日历',exact:true}).click();
  const day = page.getByRole('button',{name:'2026-11-24 已补签',exact:true});
  await day.click();
  assert.match(await page.locator('#calendar-detail').innerText(),/补签 · 补记昨天的学习/);
  assert.equal((await api('/stats')).today_seconds,0);
});

test('reading reminder appears after saving its schedule and is not repeated on reload', async t => {
  const {page} = await fixture(t);
  await page.locator('.plan-settings > summary').click();
  await page.locator('#plan-goals-form [name=reading_reminder]').fill('20:00');
  await page.getByRole('button',{name:'保存目标与提醒',exact:true}).click();
  await page.locator('#study-reminders').getByText('今天的阅读或词汇目标还未达成。',{exact:true}).waitFor();
  await page.getByRole('button',{name:'关闭这条提醒',exact:true}).click();
  assert.equal(await page.locator('#study-reminders').isVisible(),false);
  const claimed = page.waitForResponse(resp=>resp.url().endsWith('/api/planning/reminders/claim'));
  await page.reload();
  assert.deepEqual((await (await claimed).json()).reminders,[]);
  assert.equal(await page.locator('#study-reminders').isVisible(),false);
});

test('stale plan edit preserves typed fields and reports a conflict', async t => {
  const {page,api} = await fixture(t);
  await newWeek(page);
  const id = (await api('/planning')).plans[0].id;
  await act(page,'edit').click();
  await page.locator('#plan-editor [name=title]').fill('此页草稿');
  await api('/planning/plans/'+id,'PATCH',{version:1,title:'另一页已保存'});
  await page.locator('#plan-editor button[type=submit]').click();
  await status(page,'已在其他页面更新');
  assert.equal(await page.locator('#plan-editor [name=title]').inputValue(),'此页草稿');
  assert.equal((await api('/planning/plans/'+id)).plan.title,'另一页已保存');
});

test('mobile plans and the daily breakdown fit within the page', async t => {
  const {page} = await fixture(t,true);
  await newWeek(page);
  await page.locator('.plan-split summary').click();
  const overflow = await page.evaluate(() => {
    const main = document.querySelector('#app');
    return [document.documentElement.scrollWidth > innerWidth, main.scrollWidth > main.clientWidth + 1];
  });
  assert.deepEqual(overflow,[false,false]);
  if (process.env.READING_TEST_SCREENSHOTS) {
    fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});
    await page.locator('#app').evaluate(el=>el.scrollTo({top:0,behavior:'instant'}));
    await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,'study-plans-mobile.png')});
  }
});

test('desktop learning plan shows live daily and stage progress', async t => {
  const {page,api} = await fixture(t);
  await api('/articles/1/progress','PATCH',{seconds:60,progress:100,position:100});
  await newWeek(page);
  assert.match(await page.locator('.plan-summary').innerText(),/阅读 1 分钟，完成 1 篇文章/);
  if (process.env.READING_TEST_SCREENSHOTS) {
    fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS,{recursive:true});
    await page.locator('#app').evaluate(el=>el.scrollTo({top:0,behavior:'instant'}));
    await page.screenshot({path:path.join(process.env.READING_TEST_SCREENSHOTS,'study-plans-desktop.png')});
  }
});
