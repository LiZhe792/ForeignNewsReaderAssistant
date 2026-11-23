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
  directory = fs.mkdtempSync(path.join(os.tmpdir(), 'margin-writing-'));
  const local = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const python = process.env.READING_TEST_PYTHON || (fs.existsSync(local) ? local : 'python');
  server = spawn(python, ['-u', '-c', `
import sys
from werkzeug.serving import make_server
from app import create_app
app = create_app({"DATABASE": sys.argv[1], "TESTING": True})
server = make_server("127.0.0.1", 0, app, threaded=True)
print(server.server_port, flush=True)
server.serve_forever()
`, path.join(directory, 'test.sqlite3')], {cwd: root, stdio: ['ignore','pipe','pipe']});
  origin = await new Promise((resolve, reject) => {
    let errors = '';
    server.stderr.on('data', chunk => { errors += chunk; });
    const timer = setTimeout(() => reject(new Error(errors || 'Server startup timed out')), 10000);
    server.once('exit', code => {clearTimeout(timer); reject(new Error(`Server ${code}: ${errors}`));});
    server.stdout.once('data', chunk => {
      clearTimeout(timer);
      resolve('http://127.0.0.1:' + chunk.toString().trim());
    });
  });

  options = {headless: true};
  if (process.env.READING_TEST_CHROMIUM_MODULE) {
    const {default: runtime} = await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);
    options = {...options, executablePath: await runtime.executablePath(), args: runtime.args};
  }
});

after(async () => {
  if (server && server.exitCode === null) {
    const exited = new Promise(resolve => server.once('exit', resolve));
    server.kill();
    await exited;
  }
  if (directory) fs.rmSync(directory, {recursive: true, force: true});
});

async function newPage(t, mobile = false) {
  const browser = await chromium.launch(options);
  t.after(() => browser.close());
  const context = await browser.newContext({viewport: mobile ? {width: 360, height: 720} : {width: 1280, height: 920}});
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  return page;
}

async function signIn(page, username, register = false) {
  await page.goto(origin + '/writing');
  if (register) await page.getByRole('button', {name: '创建账户', exact: true}).click();
  await page.getByLabel('用户名', {exact: true}).fill(username);
  await page.getByLabel('密码', {exact: true}).fill('sample passphrase');
  await page.getByRole('button', {name: register ? '创建账户' : '登录', exact: true}).click();
  await page.getByRole('heading', {name: '编辑草稿', exact: true}).waitFor();
}

const editor = page => page.getByLabel('正文', {exact: true});
const draftButton = page => page.locator('#writing-list button');
async function openFirst(page) {
  await draftButton(page).first().click();
  await page.getByRole('status').getByText('已打开草稿。', {exact: true}).waitFor();
}
async function save(page) {
  await page.getByRole('button', {name: '保存草稿', exact: true}).click();
  await page.getByRole('status').getByText('草稿已保存并同步。', {exact: true}).waitFor();
}

test('two sessions continue a draft and preserve both versions after simultaneous editing', async t => {
  const a = await newPage(t);
  const b = await newPage(t, true);
  await signIn(a, 'devices-writer', true);
  await a.getByRole('button', {name: '新建草稿', exact: true}).click();
  await a.getByLabel('标题', {exact: true}).fill('Reading journal 练习');
  await editor(a).fill('Device A: opening paragraph');
  await save(a);
  await signIn(b, 'devices-writer');
  await openFirst(b);
  assert.equal(await editor(b).inputValue(), 'Device A: opening paragraph');
  await editor(b).fill('Device B: revised paragraph');
  await save(b);
  await editor(a).fill('Device A: competing paragraph');
  await a.getByRole('button', {name: '保存草稿', exact: true}).click();
  await a.getByRole('heading', {name: '草稿已在另一设备上修改'}).waitFor();
  await a.getByText('查看服务器版本', {exact: true}).click();
  assert.equal(await a.locator('#writing-remote-content').textContent(), 'Device B: revised paragraph');
  assert.equal(await editor(a).inputValue(), 'Device A: competing paragraph');

  await a.getByRole('button', {name: '将本机版本另存为草稿'}).click();
  await a.getByRole('status').getByText('草稿已保存并同步。', {exact: true}).waitFor();
  await b.getByRole('button', {name: '同步', exact: true}).click();
  await b.getByRole('status').getByText('已同步服务器草稿。', {exact: true}).waitFor();
  assert.equal(await draftButton(b).count(), 2);
  assert.equal(await editor(b).inputValue(), 'Device B: revised paragraph');
  await openFirst(b);
  assert.equal(await editor(b).inputValue(), 'Device A: competing paragraph');
});

test('unsent edits survive a reload and sync after the connection returns', async t => {
  const page = await newPage(t);
  await signIn(page, 'recovery-writer', true);
  await page.getByRole('button', {name: '新建草稿', exact: true}).click();
  await page.getByLabel('标题', {exact: true}).fill('Recovery draft');
  await editor(page).fill('Original');
  await save(page);
  const pattern = '**/api/writing-workspace/drafts/*';
  await page.route(pattern, async route => {
    if (route.request().method() === 'PUT') await route.abort();
    else await route.continue();
  });
  await editor(page).fill('Unsynced Chinese 中文 📝');
  await page.getByRole('button', {name: '保存草稿', exact: true}).click();
  await page.getByRole('status').getByText(/未同步修改仍在此标签页/).waitFor();
  await page.reload();
  await page.getByRole('status').getByText('已恢复本标签页的未同步修改。', {exact: true}).waitFor();
  assert.equal(await editor(page).inputValue(), 'Unsynced Chinese 中文 📝');
  await page.unroute(pattern);
  await save(page);
  await page.reload();
  await openFirst(page);
  assert.equal(await editor(page).inputValue(), 'Unsynced Chinese 中文 📝');
});

test('keyboard skips navigation, routes announce changes and accessibility preferences persist', async t => {
  const page = await newPage(t, true);
  await page.goto(origin + '/');
  const link = page.getByRole('link', {name: '跳到主要内容'});
  await page.keyboard.press('Tab');
  assert.equal(await link.evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#app').evaluate(el => el === document.activeElement), true);

  await page.getByRole('button', {name: '语言与显示', exact: true}).click();
  const dialog = page.getByRole('dialog', {name: '语言与显示'});
  await dialog.getByLabel('界面语言', {exact: true}).selectOption('en');
  await page.getByLabel('Larger interface text', {exact: true}).check();
  await page.getByLabel('High contrast', {exact: true}).check();
  await page.getByLabel('Reduce motion', {exact: true}).check();
  await page.getByRole('button', {name: 'Done', exact: true}).click();
  const state = () => page.locator('html').evaluate(el => ({
    language: el.lang, large: el.hasAttribute('data-large-text'),
    contrast: el.hasAttribute('data-high-contrast'), motion: el.hasAttribute('data-reduced-motion'),
  }));
  assert.deepEqual(await state(), {language: 'en', large: true, contrast: true, motion: true});
  assert.equal(await page.getByRole('link', {name: 'Article library', exact: true}).getAttribute('aria-current'), 'page');
  await page.getByRole('link', {name: 'Learning activity', exact: true}).click();
  await page.waitForFunction(() => document.getElementById('route-status').textContent === 'Learning activity');
  assert.equal(await page.getByRole('link', {name: 'Learning activity', exact: true}).getAttribute('aria-current'), 'page');
  await page.reload();
  await page.getByRole('button', {name: 'Language and display', exact: true}).waitFor();
  assert.deepEqual(await state(), {language: 'en', large: true, contrast: true, motion: true});
  await page.getByRole('link', {name: 'Writing workspace'}).click();
  await page.getByRole('heading', {name: 'Writing workspace', exact: true}).waitFor();
  await page.getByLabel('Username', {exact: true}).waitFor();
  assert.deepEqual(await state(), {language: 'en', large: true, contrast: true, motion: true});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test('English core controls preserve form data and the original article text', async t => {
  const page = await newPage(t);
  await page.goto(origin + '/');
  await page.getByRole('button', {name: '语言与显示', exact: true}).click();
  await page.getByLabel('界面语言', {exact: true}).selectOption('en');
  await page.getByRole('button', {name: 'Done', exact: true}).click();
  await page.getByRole('button', {name: 'Import article', exact: true}).first().click();
  const dialog = page.getByRole('dialog', {name: 'Import an article'});
  assert.equal(await dialog.getByLabel('Category', {exact: true}).inputValue(), '其他');
  await dialog.getByLabel('Category', {exact: true}).selectOption({label: 'Science'});
  assert.equal(await dialog.getByLabel('Category', {exact: true}).inputValue(), '科学');
  await page.keyboard.press('Escape');

  await page.locator('.card-title').first().click();
  await page.getByRole('region', {name: 'Article reading area', exact: true}).waitFor();
  const original = await page.locator('#article-body .paragraph').first().getAttribute('data-plain');
  assert.ok(original?.length > 40);
  assert.equal(await page.locator('#article-body').getAttribute('lang'), 'en');
  await page.getByRole('button', {name: 'Look up', exact: true}).focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.getByRole('textbox', {name: 'Look up an English word or phrase'}).evaluate(el => el === document.activeElement), true);
  await page.getByRole('link', {name: 'Write about this article', exact: true}).click();
  await page.getByRole('heading', {name: 'Writing workspace', exact: true}).waitFor();
  assert.ok(await page.locator('body').getAttribute('data-article-title'));
});
