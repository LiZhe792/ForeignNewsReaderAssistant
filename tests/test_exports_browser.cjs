/* Optional: node --test tests/test_exports_browser.cjs (Playwright required). */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {before, after, test} = require('node:test');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
let temporary, server, browser, origin, downloadNumber = 0;
const errors = [];
before(async () => {
  temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'margin-exports-browser-'));
  const local = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  server = spawn(process.env.EXPORT_TEST_PYTHON || (fs.existsSync(local) ? local : 'python'), ['-B', '-u', '-c', `
import json, logging, sys
from app import create_app
from newsreader.db import get_db
from werkzeug.serving import make_server
sys.path.insert(0, 'tests')
from test_exports import create_note_fixture_schema
application = create_app({'DATABASE': sys.argv[1]})
with application.app_context():
    db = get_db()
    create_note_fixture_schema(db)
    db.execute("UPDATE articles SET source_url='https://example.com/reading', progress=40, reading_seconds=98765 WHERE id=1")
    db.executemany('INSERT INTO vocabulary(word,meaning,context,note,article_id,mastery,created_at) VALUES(?,?,?,?,?,?,?)',
        [('word'+str(i).zfill(2), '中文释义 '+str(i), 'Readers learn together.', '学习笔记\\n第二行', 1,
          'mastered' if i%2 else 'new', '2026-11-01T10:00:00+08:00') for i in range(55)])
    for i,kind in enumerate(['note','card','expression']):
        db.execute('INSERT INTO knowledge_items(request_id,kind,title,content,tags,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
            ('fixture-'+str(i),kind,kind+' 笔记 <img src=x onerror=window.badExport=1>', '中文内容与英文表达。\\nA thoughtful habit.', '["写作"]', '2026-11-01T10:00:00+08:00', '2026-11-02T10:00:00+08:00'))
    db.executemany('INSERT INTO checkins(date,reading_seconds,articles_completed,manual) VALUES(?,?,?,?)',
        [('2026-11-01',60,1,0),('2026-11-02',120,0,1)])
    db.commit()
logging.getLogger('werkzeug').setLevel(logging.ERROR)
server = make_server('127.0.0.1', 0, application, threaded=True)
print('PORT:'+str(server.server_port), flush=True)
server.serve_forever()
`, path.join(temporary, 'browser.sqlite3')], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
  let output = '', stderr = '';
  server.stderr.on('data', data => stderr = (stderr + data).slice(-5000));
  origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Server startup: ' + stderr)), 15000);
    server.once('exit', code => { clearTimeout(timer); reject(new Error('Server exit ' + code + ' ' + stderr)); });
    server.stdout.on('data', data => { output += data; const m = output.match(/PORT:(\d+)/); if (m) { clearTimeout(timer); resolve('http://127.0.0.1:' + m[1]); } });
  });
  browser = await chromium.launch({headless: true, ...(process.env.EXPORT_TEST_BROWSER ? {channel: process.env.EXPORT_TEST_BROWSER} : {})});
});
after(async () => {
  await browser?.close();
  if (server && server.exitCode === null) { const stopped = new Promise(resolve => server.once('exit', resolve)); server.kill(); await stopped; }
  if (temporary) {
    const target = path.resolve(temporary);
    assert.equal(path.dirname(target), path.resolve(os.tmpdir()));
    assert.ok(path.basename(target).startsWith('margin-exports-browser-'));
    fs.rmSync(target, {recursive: true, force: true});
  }
  assert.deepEqual(errors, [], 'Browser script errors');
});
async function setup(t, options = {}) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1060}, acceptDownloads: true, ...options});
  t.after(() => context.close()); context.setDefaultTimeout(12000);
  const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message)); page.on('dialog', d => d.accept());
  await page.goto(origin + '/#exports'); await ready(page);
  return {context, page};
}
async function ready(page, dataset = 'vocabulary') {
  await page.waitForFunction(dataset => typeof exportCenter !== 'undefined' && exportCenter?.dataset === dataset && exportCenter.previewValid, dataset);
}
async function choose(page, dataset) { await page.locator(`[data-export-action="dataset"][data-dataset="${dataset}"]`).click(); await ready(page, dataset); }
async function download(page, fmt, action = '#export-download') {
  if (fmt) await page.locator('#export-format').selectOption(fmt);
  const [result] = await Promise.all([page.waitForEvent('download'), page.locator(action).click()]);
  const file = path.join(temporary, (++downloadNumber) + '-' + result.suggestedFilename());
  await result.saveAs(file);
  assert.equal(await result.failure(), null);
  return {data: fs.readFileSync(file), filename: result.suggestedFilename()};
}
async function screenshot(page, name) {
  if (process.env.EXPORT_SCREENSHOTS) {
    fs.mkdirSync(process.env.EXPORT_SCREENSHOTS, {recursive: true});
    await page.screenshot({path: path.join(process.env.EXPORT_SCREENSHOTS, name + '.png'), fullPage: true});
  }
}
test('all versus selected records, pagination and filter changes match downloaded files', async t => {
  const {page} = await setup(t);
  assert.equal(await page.locator('.export-record').count(), 50);
  await page.locator('[data-export-id="1"]').check(); await page.locator('[data-export-id="2"]').check();
  const selected = await download(page, 'json');
  assert.match(selected.filename, /^margin-vocabulary-\d{4}-\d{2}-\d{2}\.json$/);
  assert.deepEqual(JSON.parse(selected.data).items.map(i => i.id), [1, 2]);
  await page.locator('#export-more').click();
  await page.waitForFunction(() => exportCenter.items.length === 55 && exportCenter.previewValid);
  assert.equal(await page.locator('.export-record input:checked').count(), 2);
  await page.locator('[data-export-action="select-loaded"]').click();
  assert.equal(JSON.parse((await download(page, 'json')).data).count, 55);
  await page.locator('[data-export-action="clear-selection"]').click(); assert.ok(await page.locator('#export-download').isDisabled());
  await page.locator('[name="export-scope"][value="all"]').check();
  await page.locator('#export-filters [name="mastery"]').selectOption('mastered');
  assert.ok(await page.locator('#export-download').isDisabled()); assert.ok(await page.locator('#export-more').isDisabled());
  await page.locator('#export-filters button').click(); await ready(page);
  const filtered = JSON.parse((await download(page, 'json')).data);
  assert.equal(filtered.count, 27); assert.ok(filtered.items.every(i => i.mastery === 'mastered'));
  await page.locator('.export-page h1').scrollIntoViewIfNeeded(); await screenshot(page, 'exports-desktop');
});
test('notes support Anki cards and both Obsidian and Notion packages without rendering user HTML', async t => {
  const {page} = await setup(t); await choose(page, 'notes');
  assert.equal(await page.locator('#export-preview-content img').count(), 0);
  assert.equal(await page.evaluate(() => window.badExport), undefined);
  const markdown = (await download(page, 'md')).data.toString('utf8');
  assert.ok(markdown.includes('&lt;img')); assert.ok(!markdown.includes('<img'));
  for (const fmt of ['obsidian', 'notion']) {
    const result = await download(page, fmt); assert.match(result.filename, /\.zip$/); assert.equal(result.data.subarray(0, 2).toString(), 'PK');
  }
  await page.locator('#export-format').selectOption('anki');
  await page.waitForFunction(() => exportCenter.previewValid && exportCenter.filters.kind === 'card');
  assert.equal(await page.locator('#export-filters [name="kind"]').inputValue(), 'card');
  assert.equal(await page.locator('.export-record').count(), 1);
  await page.locator('#export-deck').fill('我的学习::阅读卡片');
  const anki = await download(page); assert.match(anki.filename, /\.apkg$/); assert.equal(anki.data.subarray(0, 2).toString(), 'PK');
  await page.locator('#export-filters [name="kind"]').selectOption('note');
  assert.equal(await page.locator('#export-format').inputValue(), 'md'); assert.ok(await page.locator('#export-download').isDisabled());
});
test('reports use the requested dates and export real PDF and Excel files', async t => {
  const {page} = await setup(t); await choose(page, 'report');
  await page.locator('#export-filters [name="from"]').fill('2026-11-01');
  await page.locator('#export-filters [name="to"]').fill('2026-11-03');
  await page.locator('#export-filters button').click(); await ready(page, 'report');
  assert.match(await page.locator('#export-preview-status').textContent(), /3 天/);
  assert.equal(await page.locator('.export-report-metrics strong').first().textContent(), '3');
  assert.ok(await page.locator('#export-scope').isHidden());
  const pdf = await download(page, 'pdf'); assert.ok(pdf.data.subarray(0, 5).equals(Buffer.from('%PDF-')));
  const excel = await download(page, 'xlsx'); assert.match(excel.filename, /\.xlsx$/);
  await screenshot(page, 'report-desktop');
});
test('reading list downloads and copy fallback contain sources without private activity', async t => {
  const {page} = await setup(t); await choose(page, 'reading_list');
  const html = (await download(page, 'html')).data.toString('utf8');
  assert.ok(html.includes('https://example.com/reading')); assert.ok(!html.includes('98765'));
  const json = JSON.parse((await download(page, 'json')).data);
  assert.ok(json.items.every(i => !('content' in i) && !('progress' in i) && !('reading_seconds' in i)));
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async () => { throw new Error('unavailable'); }}}));
  await page.locator('#export-copy').click(); await page.waitForSelector('#export-copy-fallback:not([hidden])');
  assert.match(await page.locator('#export-copy-fallback').inputValue(), /example\.com\/reading/);
  assert.ok(!(await page.locator('#export-copy-fallback').inputValue()).includes('98765'));
});
test('failed and cancelled downloads preserve selection and returning after navigation can download again', async t => {
  const {page} = await setup(t); await page.locator('[data-export-id="1"]').check();
  await page.route('**/api/exports/file', route => route.abort());
  await page.locator('#export-download').click();
  await page.waitForFunction(() => document.querySelector('#export-download-status').textContent.includes('可重试'));
  assert.ok(await page.locator('[data-export-id="1"]').isChecked()); assert.ok(await page.locator('#export-download').isEnabled());
  await page.unroute('**/api/exports/file');
  let release, intercepted;
  const gate = new Promise(resolve => release = resolve);
  const arrived = new Promise(resolve => intercepted = resolve);
  await page.route('**/api/exports/file', async route => { intercepted(); await gate; await route.fulfill({status: 200, contentType: 'text/plain', body: 'late response'}).catch(() => {}); });
  await page.locator('#export-download').click(); await arrived; await page.locator('#export-cancel').click();
  await page.waitForFunction(() => document.querySelector('#export-download-status').textContent.includes('已取消'));
  release(); await page.unroute('**/api/exports/file');
  assert.ok(await page.locator('[data-export-id="1"]').isChecked());
  let releaseSecond, arrivedSecond;
  const secondGate = new Promise(resolve => releaseSecond = resolve), secondArrived = new Promise(resolve => arrivedSecond = resolve);
  await page.route('**/api/exports/file', async route => { arrivedSecond(); await secondGate; await route.fulfill({status: 200, body: 'late response'}).catch(() => {}); });
  await page.locator('#export-download').click(); await secondArrived;
  await page.locator('a[data-nav="library"]').click(); await page.waitForSelector('.article-grid');
  releaseSecond(); await page.unroute('**/api/exports/file');
  await page.locator('a[data-nav="exports"]').click(); await ready(page);
  assert.ok(await page.locator('#export-download').isEnabled());
  assert.equal(JSON.parse((await download(page, 'json')).data).count, 55);
});
test('a delayed initial preview cannot replace the next page or break remounting', async t => {
  const {page} = await setup(t);
  await page.locator('a[data-nav="library"]').click(); await page.waitForSelector('.article-grid');
  let release, arrived;
  const gate = new Promise(resolve => release = resolve), intercepted = new Promise(resolve => arrived = resolve);
  await page.route('**/api/exports/preview', async route => { const response = await route.fetch(); arrived(); await gate; await route.fulfill({response}).catch(() => {}); });
  await page.locator('a[data-nav="exports"]').click(); await intercepted;
  await page.locator('a[data-nav="library"]').click(); await page.waitForSelector('.article-grid'); release();
  await page.unroute('**/api/exports/preview', {behavior: 'wait'});
  assert.ok(await page.locator('.article-grid').isVisible());
  await page.locator('a[data-nav="exports"]').click(); await ready(page);
  assert.ok(await page.locator('#export-download').isEnabled());
});
test('API tokens are ephemeral in the page, rotation and revocation invalidate previous access', async t => {
  const {page, context} = await setup(t);
  await page.locator('.export-integrations summary').click();
  await page.locator('#export-generate-token').click(); await page.waitForSelector('#export-token-result:not([hidden])');
  const first = await page.locator('#export-api-token').inputValue();
  assert.ok(first.startsWith('margin_'));
  assert.equal(await page.locator('#export-api-token').getAttribute('type'), 'password');
  await page.locator('[data-export-action="toggle-token"]').click(); assert.equal(await page.locator('#export-api-token').getAttribute('type'), 'text');
  assert.equal((await context.request.get(origin + '/api/integrations/v1/notes', {headers: {Authorization: 'Bearer ' + first}})).status(), 200);
  assert.ok(!(await page.evaluate(() => JSON.stringify(localStorage))).includes(first));
  await page.locator('#export-generate-token').click();
  await page.waitForFunction(first => document.querySelector('#export-api-token').value !== first, first);
  assert.equal((await context.request.get(origin + '/api/integrations/v1/notes', {headers: {Authorization: 'Bearer ' + first}})).status(), 401);
  const second = await page.locator('#export-api-token').inputValue();
  await page.locator('a[data-nav="library"]').click(); await page.waitForSelector('.article-grid');
  assert.equal(await page.evaluate(() => exportCenter.token), '');
  await page.locator('a[data-nav="exports"]').click(); await ready(page);
  await page.locator('.export-integrations summary').click();
  await page.waitForFunction(() => document.querySelector('#export-api-status').textContent.includes('已启用'));
  assert.equal(await page.locator('#export-api-token').inputValue(), '');
  await page.locator('#export-revoke-token').click();
  await page.waitForFunction(() => document.querySelector('#export-api-status').textContent.includes('未启用'));
  assert.equal((await context.request.get(origin + '/api/integrations/v1/notes', {headers: {Authorization: 'Bearer ' + second}})).status(), 401);
  await screenshot(page, 'api-guide');
});
test('phone layout has no horizontal page overflow and supports filtering and download', async t => {
  const {page} = await setup(t, {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
  await choose(page, 'notes');
  await page.locator('#export-filters [name="tag"]').fill('写作'); await page.locator('#export-filters button').click(); await ready(page, 'notes');
  const result = await download(page, 'json'); assert.equal(JSON.parse(result.data).count, 3);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.ok(await page.evaluate(() => [...document.querySelectorAll('.export-settings,.export-preview,.export-tabs')].every(e => e.getBoundingClientRect().right <= innerWidth)));
  await page.locator('.export-tabs').scrollIntoViewIfNeeded(); await screenshot(page, 'exports-phone');
});
