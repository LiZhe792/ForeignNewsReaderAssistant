/* Account-scoped drafts, with a tab-local recovery copy until the server acknowledges a save. */
'use strict';

(() => {
  const byId = id => document.getElementById('writing-' + id);
  const {t, dateTime, localize} = MarginInterface;
  const auth = byId('auth-form');
  const form = byId('draft-form');
  const status = byId('status');
  const list = byId('list');
  const conflict = byId('conflict');
  let account, draft, remote, registering = false, inviteRequired = false;
  let saving, timer, switching = false, statusKey = '', statusError = false;
  let drafts = [];
  const local = new Map();

  const recoveryKey = () => 'margin-writing-recovery:' + account.id;
  function recover() {
    const key = recoveryKey();
    if (local.has(key)) return local.get(key);
    try { return JSON.parse(sessionStorage.getItem(key) || 'null'); }
    catch { return null; }
  }

  function remember(entry) {
    const key = recoveryKey();
    local.set(key, entry);
    try {
      if (entry) sessionStorage.setItem(key, JSON.stringify(entry));
      else sessionStorage.removeItem(key);
    } catch { /* Memory still retains the current tab's edits. */ }
  }

  function notify(key, error = false) {
    statusKey = key;
    statusError = error;
    status.textContent = key ? t('writing.' + key) : '';
    status.toggleAttribute('data-error', error);
  }

  async function api(path, method = 'GET', body) {
    const response = await fetch('/api/writing-workspace' + path, {
      method, cache: 'no-store', signal: AbortSignal.timeout(15000),
      headers: {'Content-Type': 'application/json', ...(account ? {'X-Margin-Writing-Account': account.id} : {})},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let result;
    try { result = await response.json(); }
    catch { throw new Error(response.status === 413 ? 'invalid_draft' : 'connection'); }
    if (!response.ok) throw Object.assign(new Error(result.code || 'connection'), {result});
    return result;
  }

  function failure(error) {
    if (['sign_in_required', 'account_changed'].includes(error.message)) {
      boot().catch(() => notify('connection', true));
      notify('sign_in_required', true);
    } else {
      const key = error.message;
      notify(t('writing.' + key) === 'writing.' + key ? 'connection' : key, true);
    }
  }

  function lockEditor(locked) {
    form.elements.title.readOnly = locked;
    form.elements.content.readOnly = locked;
  }

  const values = () => ({title: form.elements.title.value, content: form.elements.content.value});
  const same = (left, right) => left && right && left.title === right.title && left.content === right.content;
  const dirty = () => draft && (!draft.version || !same(values(), draft));
  const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), v => v.toString(16).padStart(2, '0')).join('');

  function renderRegister() {
    const key = registering ? 'register' : 'sign_in';
    byId('auth-title').dataset.i18n = 'writing.' + key;
    byId('auth-submit').dataset.i18n = 'writing.' + key;
    byId('auth-switch').dataset.i18n = registering ? 'writing.sign_in' : 'writing.register';
    byId('registration-help').hidden = !registering;
    byId('invite').hidden = !registering || !inviteRequired;
    auth.elements.invite.required = registering && inviteRequired;
    auth.elements.password.autocomplete = registering ? 'new-password' : 'current-password';
    localize(byId('auth'));
  }

  function renderList() {
    const selected = list.contains(document.activeElement) ? document.activeElement.dataset.draft : '';
    const nodes = drafts.map(entry => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.draft = entry.id;
      if (entry.id === draft?.id) button.setAttribute('aria-current', 'true');
      const title = document.createElement('bdi');
      title.textContent = entry.title;
      const time = document.createElement('time');
      time.dateTime = entry.updated_at;
      time.textContent = dateTime(entry.updated_at);
      button.append(title, time);
      item.append(button);
      return item;
    });
    if (!nodes.length) {
      const empty = document.createElement('li');
      empty.textContent = t('writing.empty');
      nodes.push(empty);
    }
    list.replaceChildren(...nodes);
    if (selected) [...list.querySelectorAll('button')].find(button => button.dataset.draft === selected)?.focus();
  }

  async function refreshList() {
    const who = account;
    const result = await api('/drafts');
    if (account === who) {
      drafts = result.drafts;
      renderList();
    }
  }

  function count() {
    byId('count').textContent = t('writing.count', {count: MarginInterface.number(form.elements.content.value.length)});
  }

  function showDraft(entry, edits = entry) {
    draft = entry;
    remote = null;
    conflict.hidden = true;
    form.hidden = !entry;
    byId('no-draft').hidden = !!entry;
    form.elements.title.value = edits?.title || '';
    form.elements.content.value = edits?.content || '';
    renderList();
    count();
  }

  function showConflict(entry) {
    remote = entry || null;
    conflict.hidden = false;
    byId('remote').hidden = !entry;
    byId('remote-title').textContent = entry?.title || '';
    byId('remote-content').textContent = entry?.content || '';
    byId('conflict-title').dataset.i18n = entry ? 'writing.conflict' : 'writing.draft_missing';
    localize(conflict);
    notify(entry ? 'conflict' : 'draft_missing', true);
  }

  function newDraft(title = '', content = '') {
    showDraft({id: newId(), title: '', content: '', version: 0}, {title, content});
    if (title || content) remember({id: draft.id, version: 0, title, content});
    form.elements.title.focus();
  }

  async function save() {
    clearTimeout(timer);
    while (saving) await saving;
    if (!account || !draft || !dirty()) return true;
    if (!conflict.hidden) return false;
    const snapshot = {id: draft.id, version: draft.version, ...values()};
    if (!snapshot.title.trim()) return !snapshot.content.trim() && !draft.version;
    const who = account;
    const base = draft;
    const job = (async () => {
      try {
        const {draft: stored} = await api('/drafts/' + snapshot.id, 'PUT', snapshot);
        if (who !== account || draft !== base) return false;
        draft = stored;
        // The title is canonicalized by the server, but typing during a save stays in the editor.
        if (same(snapshot, values())) form.elements.title.value = stored.title;
        if (dirty()) remember({id: stored.id, version: stored.version, ...values()});
        else remember(null);
        drafts = [stored, ...drafts.filter(entry => entry.id !== stored.id)];
        renderList();
        notify(dirty() ? 'pending' : 'saved');
        return true;
      } catch (error) {
        if (who !== account || draft !== base) return false;
        if (['conflict', 'draft_missing'].includes(error.message)) showConflict(error.result?.draft);
        else failure(error);
        return false;
      }
    })();
    saving = job;
    try { return await job; }
    finally { if (saving === job) saving = null; }
  }

  async function readyToSwitch() {
    if (!dirty()) return true;
    const saved = await save();
    if (saved && !dirty()) return true;
    if (saved && !draft.version && !values().title && !values().content) {
      remember(null);
      return true;
    }
    notify(!values().title.trim() ? 'title_required' : 'unresolved', true);
    return false;
  }

  function run(action) {
    if (switching) return;
    switching = true;
    Promise.resolve().then(action).catch(failure).finally(() => { switching = false; });
  }

  async function launch(info) {
    account = info.account;
    inviteRequired = info.invite_required;
    registering = false;
    const signedIn = !!account;
    byId('auth').hidden = signedIn;
    byId('workspace').hidden = !signedIn;
    byId('auth-switch').hidden = !info.registration;
    auth.reset();
    renderRegister();
    byId('username').textContent = account?.username || '';
    showDraft(null);
    if (!signedIn) return;
    const who = account;
    await refreshList();
    if (who !== account) return;
    const cached = recover();
    if (cached) {
      const available = drafts.find(entry => entry.id === cached.id);
      try {
        const entry = (available || cached.version) ? (await api('/drafts/' + cached.id)).draft : null;
        showDraft(entry || {id: cached.id, version: 0, title: '', content: ''}, cached);
        if (same(entry, cached)) remember(null);
        else if (entry && entry.version !== cached.version) showConflict(entry);
        else notify('recovered');
      } catch (error) {
        if (error.message !== 'draft_missing') throw error;
        showDraft({id: cached.id, version: cached.version, title: '', content: ''}, cached);
        showConflict(null);
      }
    } else if (document.body.dataset.articleTitle) {
      newDraft(document.body.dataset.articleTitle.slice(0, 140));
      document.body.dataset.articleTitle = '';
    }
  }

  async function boot() {
    return launch(await api('/session'));
  }

  byId('auth-switch').addEventListener('click', () => {
    registering = !registering;
    renderRegister();
    auth.elements.username.focus();
  });

  auth.addEventListener('submit', event => {
    event.preventDefault();
    run(async () => {
      byId('auth-submit').disabled = true;
      try {
        const data = Object.fromEntries(new FormData(auth));
        await api('/session', 'POST', {...data, register: registering});
        notify('signed_in');
        await boot();
        if (form.hidden) byId('new').focus();
      } finally { byId('auth-submit').disabled = false; }
    });
  });

  function logout(all) {
    run(async () => {
      if (!(await readyToSwitch())) return;
      if (all && !window.confirm(t('writing.confirm_sign_out'))) return;
      lockEditor(true);
      try {
        await api('/session', 'DELETE', {all_devices: all});
        notify('signed_out');
        await boot();
        auth.elements.username.focus();
      } finally { lockEditor(false); }
    });
  }

  byId('logout').addEventListener('click', () => logout(false));
  byId('logout-all').addEventListener('click', () => logout(true));
  byId('new').addEventListener('click', () => run(async () => { if (await readyToSwitch()) newDraft(); }));
  byId('sync').addEventListener('click', () => run(async () => {
    if (!await readyToSwitch()) return;
    await refreshList();
    if (draft?.version && !dirty()) {
      const current = drafts.find(entry => entry.id === draft.id);
      if (!current) showDraft(null);
      else if (current.version !== draft.version) {
        const latest = (await api('/drafts/' + draft.id)).draft;
        if (dirty()) showConflict(latest);
        else showDraft(latest);
      }
    }
    if (conflict.hidden) notify('synced');
  }));
  list.addEventListener('click', event => {
    const id = event.target.closest('button')?.dataset.draft;
    if (!id || id === draft?.id) return;
    run(async () => {
      if (!(await readyToSwitch())) return;
      const entry = (await api('/drafts/' + id)).draft;
      if (dirty() && !await readyToSwitch()) return;
      showDraft(entry);
      form.elements.title.focus();
      notify('opened');
    });
  });
  form.addEventListener('input', () => {
    if (!account || !draft) return;
    count();
    remember({id: draft.id, version: draft.version, ...values()});
    clearTimeout(timer);
    if (conflict.hidden) {
      notify('pending');
      timer = setTimeout(save, 850);
    }
  });
  form.addEventListener('submit', event => { event.preventDefault(); run(save); });
  byId('use-remote').addEventListener('click', () => run(async () => {
    if (!window.confirm(t('writing.confirm_remote'))) return;
    remember(null);
    showDraft(remote);
    await refreshList();
    (draft ? form.elements.title : byId('new')).focus();
    notify('synced');
  }));
  byId('copy').addEventListener('click', () => run(async () => {
    const {title, content} = values();
    newDraft(title.trim() || t('writing.untitled'), content);
    await save();
  }));
  byId('delete').addEventListener('click', () => run(async () => {
    if (!draft || !window.confirm(t('writing.confirm_delete'))) return;
    clearTimeout(timer);
    lockEditor(true);
    try {
      if (saving) await saving;
      if (!draft || !account) return;
      if (draft.version) {
        try { await api('/drafts/' + draft.id, 'DELETE', {version: draft.version}); }
        catch (error) {
          if (error.message === 'conflict') { showConflict(error.result.draft); return; }
          throw error;
        }
      }
      remember(null);
      showDraft(null);
      await refreshList();
      byId('new').focus();
      notify('deleted');
    } finally { lockEditor(false); }
  }));
  byId('download').addEventListener('click', () => {
    const {title, content} = values();
    const url = URL.createObjectURL(new Blob([title, '\n\n', content], {type: 'text/plain;charset=utf-8'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = (title.trim().replace(/[^\p{L}\p{N}_. -]/gu, '_') || 'draft') + '.txt';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.addEventListener('margin-interface-change', () => { notify(statusKey, statusError); renderList(); count(); });
  boot().catch(failure);
})();
