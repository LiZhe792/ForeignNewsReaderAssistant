/* Optional real-browser tests. Run with: node --test tests/test_reading_flow_browser.cjs */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {before, after, test} = require('node:test');
const {chromium} = require('playwright');

const root = path.join(__dirname, '..');
let browserOptions, server, origin, defaults, temporary;
const content = Array.from({length: 8}, (_, i) =>
  `Paragraph ${i + 1}. The journey through the city brings a reader's well-being into focus. ` +
  'A journey gives every reader a chance to discover a garden and learn about the world. '.repeat(7)
).join('\n\n');

before(async () => {
  temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'margin-flow-'));
  const localPython = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const python = process.env.READING_TEST_PYTHON || (fs.existsSync(localPython) ? localPython : 'python');
  server = spawn(python, ['-u', '-c', `
import sys
from app import create_app
from werkzeug.serving import make_server
server = make_server('127.0.0.1', 0, create_app({'DATABASE': sys.argv[1]}), threaded=True)
print('PORT:' + str(server.server_port), flush=True)
server.serve_forever()
`, path.join(temporary, 'test.sqlite3')], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
  let errors = '', output = '';
  server.stderr.on('data', data => { errors = (errors + data).slice(-5000); });
  origin = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Test server did not start: ' + errors)), 15000);
    server.once('exit', code => { clearTimeout(timeout); reject(new Error(`Test server exited (${code}): ${errors}`)); });
    server.stdout.on('data', data => {
      output += data;
      const match = output.match(/PORT:(\d+)/);
      if (match) { clearTimeout(timeout); resolve('http://127.0.0.1:' + match[1]); }
    });
  });
  browserOptions = {headless: true};
  // An optional alternate Chromium build can be supplied in constrained CI environments.
  if (process.env.READING_TEST_CHROMIUM_MODULE) {
    const {default: runtime} = await import(pathToFileURL(process.env.READING_TEST_CHROMIUM_MODULE).href);
    browserOptions = {...browserOptions, executablePath: await runtime.executablePath(), args: runtime.args};
  }
  defaults = await (await fetch(origin + '/api/settings')).json();
});

after(async () => {
  if (server && server.exitCode === null) {
    const stopped = new Promise(resolve => server.once('exit', resolve));
    server.kill();
    await stopped;
  }
  if (temporary) fs.rmSync(temporary, {recursive: true, force: true});
});

async function readerPage(t, settings = {}, viewport = {width: 1440, height: 960}, clock = false) {
  const browser = await chromium.launch(browserOptions);
  t.after(() => browser.close());
  const context = await browser.newContext({viewport});
  const api = async (endpoint, method = 'GET', data) => {
    const response = await context.request.fetch(origin + '/api' + endpoint, {method, data});
    assert.equal(response.ok(), true, await response.text());
    return response.json();
  };
  for (const word of (await api('/vocabulary')).words) await api('/vocabulary/' + word.id, 'DELETE', {});
  await api('/settings', 'PATCH', {...defaults, ...settings});
  const article = await api('/articles', 'POST', {title: 'Reading flow browser test', content});
  const page = await context.newPage();
  const errors = [], dictionaryRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().includes('/api/dictionary?')) dictionaryRequests.push(request.url()); });
  t.after(() => assert.deepEqual(errors, []));
  if (clock) await page.clock.install();
  await page.goto(`${origin}/#reader/${article.id}`);
  await countPending(page, 0);
  return {page, api, articleId: article.id, dictionaryRequests};
}

async function countPending(page, count) {
  await page.waitForFunction(expected => document.querySelector('#pending-word-count')?.textContent === String(expected), count);
}
const word = (page, text, paragraph = 0) => page.locator(`#reader-p-${paragraph} [data-word="${text}"]`).first();
const settleLayout = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const closeQueue = page => page.locator('#pending-words-dialog [aria-label="关闭待查词"]').click();
async function textAnchor(page) {
  return page.locator('#article-body .paragraph').evaluateAll(paragraphs => {
    const top = document.querySelector('.reader-scroll').getBoundingClientRect().top + 20;
    for (const paragraph of paragraphs) {
      const words = [...paragraph.querySelectorAll('[data-word]')];
      const index = words.findIndex(word => word.getBoundingClientRect().top >= top);
      if (index >= 0) return {id: paragraph.id, index, y: words[index].getBoundingClientRect().top};
    }
    throw new Error('No visible reading anchor');
  });
}
async function assertAnchor(page, anchor) {
  await settleLayout(page);
  const y = await page.locator(`#${anchor.id} [data-word]`).nth(anchor.index).evaluate(el => el.getBoundingClientRect().top);
  assert.ok(Math.abs(y - anchor.y) < 3, `reading text moved by ${y - anchor.y} pixels`);
}
async function queue(page) {
  await page.locator('.pending-open').click();
  await page.locator('#pending-words-dialog').waitFor();
}
async function screenshot(page, name) {
  if (!process.env.READING_TEST_SCREENSHOTS) return;
  fs.mkdirSync(process.env.READING_TEST_SCREENSHOTS, {recursive: true});
  await page.screenshot({path: path.join(process.env.READING_TEST_SCREENSHOTS, name + '.png')});
}

test('fluent reading defers words, restores them after refresh, and collects lazily without losing position', async t => {
  const {page, api, articleId, dictionaryRequests} = await readerPage(t);
  assert.equal(await page.locator('.reading-flow-bar select').inputValue(), 'fluent');
  await word(page, 'journey').click();
  await countPending(page, 1);
  await word(page, 'journey').click();
  await countPending(page, 1);
  assert.equal((await api(`/articles/${articleId}/pending-words`)).words.length, 1);
  assert.equal((await api('/vocabulary')).words.length, 0);
  assert.equal(dictionaryRequests.length, 0);
  assert.equal(await page.locator('.reader-margin').isVisible(), false);
  await page.reload();
  await countPending(page, 1);
  await page.locator('[data-reader-action="next"]').click();
  await page.locator('[data-reader-action="next"]').click();
  await settleLayout(page);
  const before = await textAnchor(page);
  await queue(page);
  assert.equal(await page.locator('.pending-word').count(), 1);
  assert.equal(await page.locator('.pending-definition').getAttribute('open'), null);
  assert.equal(dictionaryRequests.length, 0);
  await page.locator('.pending-definition summary').click();
  await page.locator('.pending-definition .definition').waitFor();
  assert.equal(dictionaryRequests.length, 1);
  await screenshot(page, 'desktop-pending-words');
  await page.locator('[data-flow-op="collect"]').click();
  await countPending(page, 0);
  const saved = (await api('/vocabulary')).words;
  assert.equal(saved[0].word, 'journey');
  assert.equal(saved[0].article_id, articleId);
  assert.match(saved[0].context, /journey/i);
  await closeQueue(page);
  await settleLayout(page);
  await assertAnchor(page, before);
});

test('intensive mode folds definitions and an asynchronous response does not undo scrolling', async t => {
  const {page} = await readerPage(t, {reading_mode: 'intensive'});
  let release;
  await page.route('**/api/dictionary?word=journey&provider=*', async route => {
    await new Promise(resolve => { release = resolve; });
    await route.continue();
  });
  await word(page, 'journey').click();
  await page.waitForFunction(() => document.querySelector('.lookup-loading'));
  await page.locator('.reader-scroll').press('Space');
  await settleLayout(page);
  const before = await textAnchor(page);
  release();
  await page.locator('.lookup-result').waitFor();
  await settleLayout(page);
  await assertAnchor(page, before);
  assert.equal(await page.locator('.lookup-definition').getAttribute('open'), null);
  assert.equal(await page.locator('.lookup-definition .definition').isVisible(), false);
  await page.locator('.lookup-definition > summary').click();
  assert.equal(await page.locator('.lookup-definition .definition').isVisible(), true);
  await page.locator('[data-action="return-reading"]').click();
  await settleLayout(page);
  await assertAnchor(page, before);
});

test('density and mastered-word preferences are applied and survive a reload', async t => {
  const {page, api} = await readerPage(t);
  const journey = await api('/vocabulary', 'POST', {word: 'journey', meaning: '旅程'});
  await api(`/vocabulary/${journey.id}`, 'PATCH', {mastery: 'learning'});
  const city = await api('/vocabulary', 'POST', {word: 'city', meaning: '城市'});
  await api(`/vocabulary/${city.id}`, 'PATCH', {mastery: 'mastered'});
  await page.reload();
  await countPending(page, 0);
  assert.equal(await page.locator('#reader-p-0 [data-word="journey"].learning-cue').count(), 1);
  assert.equal(await page.locator('#reader-p-0 [data-word="city"].learning-cue').count(), 0);
  await page.locator('[data-reader-action="settings"]').click();
  const dialog = page.locator('#reader-settings-dialog');
  await dialog.locator('[data-reader-setting="hint_density"]').selectOption('detailed');
  await dialog.locator('[data-reader-setting="suppress_mastered"]').uncheck();
  await dialog.getByRole('button', {name: '完成', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('#reader-settings-status').textContent === '阅读设置已保存');
  assert.ok(await page.locator('#reader-p-0 [data-word="journey"].learning-cue').count() > 1);
  assert.equal(await page.locator('#reader-p-0 [data-word="city"].learning-cue').count(), 1);
  await page.reload();
  await countPending(page, 0);
  assert.equal((await api('/settings')).hint_density, 'detailed');
  assert.equal((await api('/settings')).suppress_mastered, false);
  await page.locator('[data-reader-action="settings"]').click();
  await dialog.locator('[data-reader-setting="hint_density"]').selectOption('quiet');
  await dialog.getByRole('button', {name: '完成', exact: true}).click();
  assert.equal(await page.locator('.reader-hint').isVisible(), false);
  await word(page, 'journey').click();
  await countPending(page, 1);
  assert.equal(await page.locator('[data-flow-paragraph="0"]').isVisible(), false);
  await queue(page);
  assert.equal(await page.locator('.pending-word').count(), 1);
});

test('failed staging and collection can be retried without losing a manually entered meaning', async t => {
  const {page, api, articleId} = await readerPage(t);
  let reject = true;
  await page.route('**/pending-words', async route => {
    if (route.request().method() === 'POST' && reject) {
      reject = false;
      return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Test interruption'})});
    }
    return route.continue();
  });
  await word(page, 'well-being').click();
  await page.locator('[data-flow-action="retry-save"]').waitFor();
  assert.equal((await api(`/articles/${articleId}/pending-words`)).words.length, 0);
  await page.locator('[data-flow-action="retry-save"]').click();
  await countPending(page, 1);
  await page.route('**/api/dictionary?word=well-being&provider=*', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({found: false, message: '请手动填写释义。'}),
  }));
  await queue(page);
  await page.locator('.pending-definition summary').click();
  await page.locator('[data-pending-meaning]').fill('幸福与安康');
  let failCollection = true;
  await page.route('**/collect', route => failCollection
    ? route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Test interruption'})})
    : route.continue());
  await page.locator('[data-flow-op="collect"]').click();
  await page.waitForFunction(() => !document.querySelector('[data-flow-op="collect"]')?.disabled);
  assert.equal(await page.locator('[data-pending-meaning]').inputValue(), '幸福与安康');
  assert.equal((await api(`/articles/${articleId}/pending-words`)).words.length, 1);
  failCollection = false;
  await page.locator('[data-flow-op="collect"]').click();
  await countPending(page, 0);
  assert.equal((await api('/vocabulary')).words[0].meaning, '幸福与安康');
});

test('stage reminders count active time, pause during dialogs and do not steal focus', async t => {
  const {page} = await readerPage(t, {reminder_minutes: 1}, undefined, true);
  await page.locator('.reader-scroll').focus();
  await page.clock.runFor(30000);
  await page.locator('[data-reader-action="settings"]').click();
  await page.clock.runFor(30000);
  assert.equal(await page.locator('#flow-reminder').isVisible(), false);
  await page.locator('#reader-settings-dialog').getByRole('button', {name: '完成', exact: true}).click();
  await page.locator('.reader-scroll').press('ArrowRight');
  await page.clock.runFor(32000);
  assert.equal(await page.locator('#flow-reminder').isVisible(), true);
  assert.equal(await page.locator('.reader-scroll').evaluate(el => el === document.activeElement), true);
  await page.locator('[data-flow-action="reminder-later"]').click();
  assert.equal(await page.locator('#flow-reminder').isVisible(), false);
  await page.clock.runFor(60000);
  assert.equal(await page.locator('#flow-reminder').isVisible(), false);
});

test('an asynchronous queue update preserves the meaning being typed and its caret', async t => {
  const {page, dictionaryRequests} = await readerPage(t);
  await page.route('**/api/dictionary?word=well-being&provider=*', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({found: false, message: '请手动填写释义。'}),
  }));
  await word(page, 'well-being').click();
  await countPending(page, 1);
  let release, started;
  const waiting = new Promise(resolve => { started = resolve; });
  await page.route('**/pending-words', async route => {
    if (route.request().method() === 'POST' && route.request().postDataJSON().word === 'journey') {
      await new Promise(resolve => { release = resolve; started(); });
    }
    await route.continue();
  });
  await word(page, 'journey').click();
  await waiting;
  await queue(page);
  await page.locator('.pending-definition summary').click();
  const meaning = page.locator('[data-pending-meaning]');
  await meaning.fill('幸福与安康');
  await meaning.press('ArrowLeft');
  const caret = await meaning.evaluate(el => el.selectionStart);
  release();
  await countPending(page, 2);
  await settleLayout(page);
  assert.equal(await meaning.inputValue(), '幸福与安康');
  assert.equal(await meaning.evaluate(el => document.activeElement === el), true, 'typing focus must survive the update');
  assert.equal(await meaning.evaluate(el => el.selectionStart), caret);
  assert.equal(dictionaryRequests.length, 1, 'a new queued word stays collapsed');
});

test('staging a word midway through a paragraph keeps the same reading line', async t => {
  const {page} = await readerPage(t);
  await page.locator('#reader-p-0').evaluate(paragraph => {
    const scroller = document.querySelector('.reader-scroll');
    scroller.scrollTop += paragraph.getBoundingClientRect().top - scroller.getBoundingClientRect().top + paragraph.clientHeight / 2;
  });
  await settleLayout(page);
  const candidates = page.locator('#reader-p-0 [data-word="journey"]');
  const index = await candidates.evaluateAll(words => {
    const rect = document.querySelector('.reader-scroll').getBoundingClientRect();
    return words.findIndex(word => word.getBoundingClientRect().top > rect.top + 20 && word.getBoundingClientRect().bottom < rect.bottom - 20);
  });
  assert.ok(index >= 0);
  const anchor = await textAnchor(page);
  await candidates.nth(index).click();
  await countPending(page, 1);
  await assertAnchor(page, anchor);
});

test('mobile lookup and queue overlays keep the current paragraph in place', async t => {
  const {page} = await readerPage(t, {reading_mode: 'intensive'}, {width: 390, height: 844});
  await page.locator('[data-reader-action="next"]').click();
  await settleLayout(page);
  await word(page, 'journey', 1).scrollIntoViewIfNeeded();
  const before = await textAnchor(page);
  await word(page, 'journey', 1).click();
  await page.locator('.lookup-result').waitFor();
  assert.equal(await page.locator('.reader-margin').isVisible(), true);
  await page.locator('[data-action="defer-lookup"]').click();
  await countPending(page, 1);
  await page.locator('[data-action="return-reading"]').click();
  await settleLayout(page);
  await assertAnchor(page, before);
  await queue(page);
  await screenshot(page, 'mobile-pending-words');
  await closeQueue(page);
  await settleLayout(page);
  await assertAnchor(page, before);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});
