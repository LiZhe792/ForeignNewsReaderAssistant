/* Regression tests for frontend events and persistence, using Node's standard library. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const flowSource = fs.readFileSync(path.join(root, 'static/reading-flow.js'), 'utf8');

function frontend() {
  const listeners = new Map();
  const elements = new Map();
  const calls = [];
  let respond = () => new Promise(() => {});
  const element = () => ({
    innerHTML: '', textContent: '', style: {}, dataset: {},
    classList: {add() {}, remove() {}, toggle() {}},
    append() {}, remove() {}, addEventListener() {},
  });
  for (const selector of ['#app', '#confirm-dialog', '#toast-region', '#learning-panel']) {
    elements.set(selector, element());
  }
  const document = {
    querySelector: selector => elements.get(selector) || null,
    querySelectorAll: () => [],
    createElement: element,
    addEventListener: (type, handler) => {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
  };
  const window = {addEventListener() {}, getSelection: () => null};
  const context = vm.createContext({
    document, window, location: {hash: '#reader/1'}, performance: {now: () => 0},
    AbortController, setTimeout() {}, clearTimeout() {}, setInterval() {},
    matchMedia: () => ({matches: false}),
    fetch: async (url, options) => {
      calls.push({url, method: options.method || 'GET', body: options.body && JSON.parse(options.body)});
      return respond(url, options);
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'static/dictionary.js'), 'utf8'), context);
  context.MarginDictionary = window.MarginDictionary;
  vm.runInContext(flowSource, context);
  vm.runInContext(source, context);
  // The initial data load stays pending; each test controls subsequent responses.
  calls.length = 0;
  return {
    calls, elements, window,
    run: code => vm.runInContext(code, context),
    respond: callback => { respond = callback; },
    emit: async (type, event) => {
      for (const handler of listeners.get(type) || []) await handler(event);
    },
  };
}

const ok = payload => ({ok: true, json: async () => payload});

test('a failed article deletion keeps reading and completion usable', async () => {
  const ui = frontend();
  ui.run(`state.view='reader'; state.article={id:1,title:'Test'};
    activeSession={id:1,seconds:0,progress:20,position:20,dirty:false,saving:null};
    readingSessions.set(1,activeSession); confirmDelete=async()=>true;`);
  ui.respond(() => ({ok: false, json: async () => ({error: 'Delete failed'})}));
  const button = {dataset: {action: 'delete-article'}};
  await ui.emit('click', {target: {closest: selector => selector === 'button, a' ? button : null}});
  assert.equal(ui.run('activeSession?.id'), 1);
  assert.equal(ui.run('readingSessions.has(1)'), true);
  ui.respond(() => ok({progress: 100}));
  await ui.run('activeSession.seconds=12; activeSession.progress=100; activeSession.dirty=true; flushSession()');
  assert.match(ui.calls.at(-1).body.request_id,/^[A-Za-z0-9_-]{8,100}$/);
  const {request_id,...saved}=ui.calls.at(-1).body;assert.deepEqual(saved, {seconds: 12, progress: 100, position: 20});
});

test('saving drains reading time accumulated during an outage in valid batches', async () => {
  const ui = frontend();
  ui.respond(() => ok({}));
  await ui.run(`activeSession={id:1,seconds:125,progress:70,position:40,dirty:true,saving:null};
    flushSession()`);
  assert.deepEqual(ui.calls.map(call => call.body.seconds), [60, 60, 5]);
  assert.equal(ui.run('activeSession.seconds'), 0);
});

test('a successful deletion removes the old session after the server accepts it', async () => {
  const ui = frontend();
  ui.run(`state.view='reader'; state.article={id:1,title:'Test'}; state.articles=[state.article];
    activeSession={id:1,seconds:0,dirty:false,saving:null};
    readingSessions.set(1,activeSession); confirmDelete=async()=>true;`);
  ui.respond(() => ok({ok: true}));
  const button = {dataset: {action: 'delete-article'}};
  await ui.emit('click', {target: {closest: selector => selector === 'button, a' ? button : null}});
  assert.equal(ui.run('activeSession'), null);
  assert.equal(ui.run('readingSessions.has(1)'), false);
  assert.equal(ui.run('state.articles.length'), 0);
  assert.equal(ui.run('location.hash'), 'library');
});

test('finishing an old deletion preserves an article opened while waiting', async () => {
  const ui = frontend();
  ui.run(`state.view='reader'; state.article={id:1,title:'Test'};
    activeSession={id:1,seconds:0,dirty:false,saving:null};
    readingSessions.set(1,activeSession); confirmDelete=async()=>true;`);
  let finish;
  const started = new Promise(resolve => ui.respond(() => {
    resolve();
    return new Promise(done => { finish = () => done(ok({ok: true})); });
  }));
  const button = {dataset: {action: 'delete-article'}};
  const deletion = ui.emit('click', {target: {closest: selector => selector === 'button, a' ? button : null}});
  await started;
  ui.run(`state.article={id:2}; activeSession={id:2,seconds:12};
    readingSessions.set(2,activeSession); location.hash='#reader/2';`);
  finish();
  await deletion;
  assert.equal(ui.run('activeSession.id'), 2);
  assert.equal(ui.run('activeSession.seconds'), 12);
  assert.equal(ui.run('location.hash'), '#reader/2');
});

test('an interrupted save retains only unsent time for a later retry', async () => {
  const ui = frontend();
  let requests = 0;
  ui.respond(() => ++requests === 2
    ? {ok: false, json: async () => ({error: 'Offline'})} : ok({}));
  await assert.rejects(ui.run(`activeSession={id:1,seconds:125,progress:70,position:40,dirty:true,saving:null};
    flushSession()`), /Offline/);
  assert.equal(ui.run('activeSession.seconds'), 65);
  ui.respond(() => ok({}));
  await ui.run('flushSession()');
  assert.deepEqual(ui.calls.map(call => call.body.seconds), [60, 60, 60, 5]);
  assert.equal(ui.run('activeSession.seconds'), 0);
});

test('simultaneous save triggers do not submit the same reading time twice', async () => {
  const ui = frontend();
  ui.respond(() => ok({}));
  await ui.run(`activeSession={id:1,seconds:125,progress:70,position:40,dirty:true,saving:null};
    Promise.all([flushSession(),flushSession()])`);
  assert.equal(ui.calls.reduce((sum, call) => sum + call.body.seconds, 0), 125);
  assert.equal(ui.run('activeSession.seconds'), 0);
});

test('a failed page-exit save retains time without retrying in a loop', async () => {
  const ui = frontend();
  ui.respond(() => ({ok: false, json: async () => ({error: 'Offline'})}));
  await ui.run(`activeSession={id:1,seconds:125,progress:70,position:40,dirty:true,saving:null};
    flushSession(activeSession,true)`);
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.run('activeSession.seconds'), 125);
  assert.equal(ui.run('activeSession.dirty'), true);
});

test('scroll and time changes made during a save are included before it finishes', async () => {
  const ui = frontend();
  let finish;
  ui.respond(() => new Promise(resolve => { finish = () => resolve(ok({})); }));
  const saving = ui.run(`activeSession={id:1,seconds:12,progress:20,position:20,dirty:true,saving:null};
    flushSession()`);
  ui.run('activeSession.seconds+=5; activeSession.progress=80; activeSession.position=50; activeSession.dirty=true');
  ui.respond(() => ok({}));
  finish();
  await saving;
  assert.deepEqual(ui.calls.map(call => {const {request_id,...body}=call.body;return body;}), [
    {seconds: 12, progress: 20, position: 20}, {seconds: 5, progress: 80, position: 50},
  ]);
  assert.equal(ui.run('activeSession.seconds'), 0);
  assert.equal(ui.run('activeSession.dirty'), false);
});

for (const word of ['well-being', "reader's", 'reader’s']) {
  test(`selecting ${word} queries the entire word`, async () => {
    const ui = frontend();
    ui.run(`state.view='reader'; state.article={id:1};
      reader={paragraphs:[],captureAnchor(){return {};},restoreAnchor(){},overlayMargin(){return false;},ignoreWordTap(){return false;}};
      flow=Object.assign(Object.create(window.MarginFlow.prototype),{mode:'intensive',reader,lookup});`);
    const node = {};
    const body = {contains: n => n === node};
    ui.window.getSelection = () => ({toString: () => word, anchorNode: node, focusNode: node});
    const paragraph = {contains: n => n === node, dataset: {plain: `We discussed ${word} in class.`}};
    await ui.emit('mouseup', {target: {closest: selector => selector === '#article-body'
      ? body : selector === '.paragraph' ? paragraph : null}});
    assert.equal(ui.calls[0]?.url, '/api/dictionary?word=' + encodeURIComponent(word) + '&provider=auto');
  });
}

test('selecting a sentence longer than six words does not query one clicked word', async () => {
  const ui = frontend();
  const node = {};
  ui.window.getSelection = () => ({toString: () => 'one two three four five six seven', anchorNode: node, focusNode: node});
  await ui.emit('mouseup', {target: {closest: selector => selector === '#article-body'
    ? {contains: n => n === node} : selector === '[data-word]' ? {dataset: {word: 'two'}} : null}});
  assert.equal(ui.calls.length, 0);
});

test('the vocabulary form pattern compiles under modern browser validation rules', () => {
  const html = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8');
  const pattern = html.match(/name="word" pattern="([^"]+)"/)[1];
  const regex = new RegExp(`^(?:${pattern})$`, 'v');
  for (const word of ['journey', 'well-being', "reader's", 'reader’s', 'take into account']) assert.equal(regex.test(word), true);
  for (const word of ['one two three four five six seven', '123', '<script>', 'read-']) assert.equal(regex.test(word), false);
});


test('a lost reading acknowledgement retries the original receipt before saving newer progress', async () => {
  const ui=frontend();let first=true;
  ui.respond(()=>{if(first){first=false;throw new TypeError('Lost acknowledgement');}return ok({});});
  await assert.rejects(ui.run(`activeSession={id:1,seconds:15,progress:25,position:25,dirty:true,saving:null};flushSession()`));
  ui.run('activeSession.seconds+=10;activeSession.progress=70;activeSession.position=70;activeSession.dirty=true');
  await ui.run('flushSession()');
  assert.equal(ui.calls.length,3);assert.deepEqual(ui.calls[0].body,ui.calls[1].body);
  assert.notEqual(ui.calls[1].body.request_id,ui.calls[2].body.request_id);
  assert.equal(ui.calls[2].body.seconds,10);assert.equal(ui.calls[2].body.progress,70);assert.equal(ui.run('activeSession.seconds'),0);
});
