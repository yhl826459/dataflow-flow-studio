function serialize(data) {
  if (!data || !Array.isArray(data.graphs) || !data.drafts || Array.isArray(data.drafts) ||
      typeof data.drafts !== 'object' || !Array.isArray(data.custom)) {
    throw new Error('资料库数据格式无效');
  }
  return JSON.stringify({graphs:data.graphs, drafts:data.drafts, custom:data.custom});
}

// The data itself is persisted by the app. Link a small pending-write marker to
// those exact browser contents so a reload can resume a deferred save.
const pendingKey = 'flow-studio.personal.v1.library-pending';
function fingerprint(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return text.length + ':' + (h >>> 0);
}

export function createLibraryStore({read, apply, report}) {
  const initial = serialize(read());
  let observed = initial;
  let confirmed = initial;
  let revision = null;
  let requested = false, blocked = false, sending = false, applying = false, loading = null;
  let timer;
  let ownedMarker = null, prior = null;
  try {
    const raw = globalThis.localStorage?.getItem(pendingKey);
    const parsed = raw && JSON.parse(raw);
    if (parsed && parsed.snapshot === fingerprint(initial)) { prior = parsed; ownedMarker = raw; }
  } catch { /* Browser-only persistence can be unavailable. */ }

  function remember() {
    ownedMarker = JSON.stringify({revision:revision ?? prior?.revision ?? null, snapshot:fingerprint(observed)});
    try { globalThis.localStorage?.setItem(pendingKey, ownedMarker); } catch {}
  }

  function forget() {
    try {
      if (ownedMarker && globalThis.localStorage?.getItem(pendingKey) === ownedMarker) {
        globalThis.localStorage.removeItem(pendingKey);
      }
    } catch {}
    ownedMarker = null;
  }

  function conflict() {
    clearTimeout(timer);
    blocked = true;
    forget();
    report('资料库已被其他页面修改。当前草稿已留在浏览器，请先导出，再刷新载入最新版本。');
  }

  function schedule() {
    clearTimeout(timer);
    if (!blocked && revision !== null && !sending && observed !== confirmed) {
      timer = setTimeout(save, 250);
    }
  }

  async function save() {
    if (blocked || sending || revision === null || observed === confirmed) return;
    const snapshot = observed;
    sending = true;
    let success = false;
    try {
      const response = await fetch('/api/library', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({expectedRevision:revision, data:JSON.parse(snapshot)})
      });
      if (response.status === 409) { conflict(); return; }
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const saved = await response.json();
      if (!Number.isSafeInteger(saved.revision) || saved.revision <= revision) throw new Error('服务器未确认新版本');
      revision = saved.revision;
      confirmed = snapshot;
      if (observed === confirmed) forget();
      else remember();
      success = true;
    } catch (error) {
      report('工作台资料库未确认保存，草稿已留在浏览器。可先导出备份，下次编辑时将重试。' + '（' + error.message + '）');
    } finally {
      sending = false;
      // Queue an edit made during this request, including an undo to the original value.
      if (success || observed !== snapshot) schedule();
    }
  }

  async function load() {
    try {
      const response = await fetch('/api/library', {cache:'no-store'});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const result = await response.json();
      if (!Number.isSafeInteger(result.revision) || result.revision < 0 || !Object.hasOwn(result,'data')) {
        throw new Error('资料库版本无效');
      }
      observed = serialize(read());
      if (result.data !== null) {
        const remote = serialize(result.data);
        const edited = !!prior || observed !== initial;
        const sameBase = prior ? prior.revision === result.revision : initial === remote;
        if (edited && observed !== remote && !sameBase) { conflict(); return; }
        if (!edited && observed !== remote) {
          // apply can call changed() through the app's browser persistence hook.
          // Keep revision unset until application of the remote state has finished.
          applying = true;
          try { apply(result.data); } finally { applying = false; }
          observed = serialize(read());
        }
        confirmed = remote;
      } else if (prior) {
        confirmed = null;
      }
      revision = result.revision;
      if (observed === confirmed) forget();
      else remember();
      schedule();
    } catch (error) {
      report('工作台资料库读取失败，当前编辑保存在浏览器。下次编辑时将重试。' + '（' + error.message + '）');
    }
  }

  function connect() {
    requested = true;
    if (blocked || revision !== null) return Promise.resolve();
    if (!loading && observed !== initial && !ownedMarker) remember();
    if (!loading) loading = load().finally(() => { loading = null; });
    return loading;
  }

  function changed() {
    const snapshot = serialize(read());
    if (snapshot === observed) return;
    observed = snapshot;
    if (!requested || blocked || applying) return;
    if (observed === confirmed && !sending) forget();
    else remember();
    if (revision === null) connect();
    else schedule();
  }

  return {connect, changed};
}
