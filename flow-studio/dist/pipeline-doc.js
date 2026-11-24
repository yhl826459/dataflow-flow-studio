const imagePath = '/api/pipeline-doc/image';
const maxImageBytes = 10 * 1024 * 1024;
const imageTypes = new Set(['image/png','image/jpeg','image/webp','image/gif']);
let openEditor = null;

function imageUrl(value, graphId) {
  try {
    const url = new URL(value, location.href);
    const params = url.searchParams;
    if (url.origin === location.origin && url.pathname === imagePath && !url.hash &&
        !url.username && !url.password && [...params].length === 2 &&
        params.get('id') === graphId && /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,200}$/.test(params.get('file') || '')) {
      return url.pathname + url.search;
    }
  } catch {}
  return null;
}

function appendInline(parent, text, graphId) {
  const pattern = /!\[([^\]\n]*)\]\\?\(([^)\s\\]+)\\?\)/g;
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(text.slice(end, match.index));
    const url = imageUrl(match[2], graphId);
    if (url) {
      const img = document.createElement('img');
      img.alt = match[1] || '流程插图';
      img.loading = 'lazy';
      img.addEventListener('error', () => {
        img.replaceWith('〔图片加载失败：' + img.alt + '。请重新插入或刷新预览。〕');
      }, {once:true});
      img.src = url;
      parent.append(img);
    } else {
      parent.append('〔图片未载入：' + (match[1] || '仅支持本流水线上传的图片') + '〕');
    }
    end = match.index + match[0].length;
  }
  parent.append(text.slice(end));
}

function preview(container, text, graphId) {
  const fragment = document.createDocumentFragment();
  let paragraph, list;
  for (const line of text.split(/\r?\n/)) {
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    const item = line.match(/^\s*([-*+]|\d+[.)])\s+(.+)$/);
    if (!line.trim()) { paragraph = list = null; continue; }
    if (heading) {
      const element = document.createElement('h' + Math.min(6, heading[1].length + 1));
      appendInline(element, heading[2], graphId);
      fragment.append(element);
      paragraph = list = null;
    } else if (item) {
      const tag = /^\d/.test(item[1]) ? 'ol' : 'ul';
      if (list?.localName !== tag) {
        list = document.createElement(tag);
        fragment.append(list);
      }
      const entry = document.createElement('li');
      appendInline(entry, item[2], graphId);
      list.append(entry);
      paragraph = null;
    } else {
      if (!paragraph) { paragraph = document.createElement('p'); fragment.append(paragraph); }
      else paragraph.append(document.createElement('br'));
      appendInline(paragraph, line, graphId);
      list = null;
    }
  }
  if (!fragment.childNodes.length) {
    const empty = document.createElement('p');
    empty.className = 'doc-empty';
    empty.textContent = '正文为空。';
    fragment.append(empty);
  }
  container.replaceChildren(fragment);
}

function template(graph) {
  const lines = ['# ' + graph.name, '', '## 用途', graph.description || '在这里说明这条流水线解决什么问题。', '', '## 输入与输出', '- 输入：', '- 输出：', '', '## 算子说明'];
  for (const node of graph.nodes || []) lines.push('- ' + node.name);
  return lines.join('\n');
}

export function openPipelineDoc(graph) {
  if (openEditor && !openEditor.close()) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'pipeline-doc';
  dialog.setAttribute('aria-labelledby','pd-title');
  dialog.innerHTML = '<div class="pd-header"><div><small>流水线说明文档</small><h2 id="pd-title"></h2></div><button type="button" data-close aria-label="关闭说明文档">×</button></div>' +
    '<div class="pd-bar"><div class="pd-tabs" role="group" aria-label="文档视图"><button data-view="preview">预览</button><button data-view="edit">编辑</button></div><span data-stamp></span></div>' +
    '<div class="pd-scroll"><div data-conflict hidden><p>服务器已有其他人保存的新版本。请对照两份正文，整合要保留的内容。</p><details><summary>查看服务器最新正文</summary><pre></pre></details><button class="btn" data-rebase>已对照，使用当前草稿继续编辑</button></div>' +
    '<article class="pd-preview" data-preview></article><div data-editor hidden><div class="pd-tools" role="group" aria-label="编辑工具"><button data-prefix="## ">小标题</button><button data-prefix="- ">项目列表</button><button data-prefix="1. ">编号列表</button><button data-image>插入图片…</button><input type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden></div><label class="pd-text-label">正文<textarea spellcheck="false"></textarea></label></div></div>' +
    '<div class="pd-footer"><div role="status" aria-live="polite"></div><button class="linkbtn" data-retry hidden>重新读取</button><button class="btn primary" data-save>保存文档</button></div>';
  document.body.append(dialog);
  const find = query => dialog.querySelector(query);
  const textarea = find('textarea'), files = find('input[type="file"]'), feedback = find('[role="status"]');
  const id = graph.id, endpoint = '/api/pipeline-doc?' + new URLSearchParams({id});
  const draftKey = 'flow-studio.doc-draft.' + id;
  const life = new AbortController();
  let baseRevision = null, latest = null, baseline = '', mode = 'preview';
  let busy = '', conflict = false, dirty = false;
  const stores = [];
  try { stores.push(sessionStorage); } catch {}
  try { stores.push(localStorage); } catch {}
  let draft;
  for (const storage of stores) {
    try {
      const value = JSON.parse(storage.getItem(draftKey));
      if (typeof value?.text === 'string' && (value.baseRevision === null || Number.isSafeInteger(value.baseRevision))) { draft = value; break; }
    } catch {}
  }
  if (draft) {
    textarea.value = draft.text; baseRevision = draft.baseRevision; dirty = true; mode = 'edit';
  }
  find('#pd-title').textContent = graph.name;

  function remember() {
    const record = JSON.stringify({text:textarea.value,baseRevision});
    for (const storage of stores) try { storage.setItem(draftKey,record); } catch {}
  }
  function clearDraft(text) {
    for (const storage of stores) try {
      if (JSON.parse(storage.getItem(draftKey))?.text === text) storage.removeItem(draftKey);
    } catch {}
  }
  function message(text, error = false) {
    feedback.textContent = text;
    feedback.classList.toggle('pd-error',error);
  }
  function refresh() {
    find('[data-editor]').hidden = mode !== 'edit';
    find('[data-preview]').hidden = mode !== 'preview';
    for (const tab of dialog.querySelectorAll('[data-view]')) tab.setAttribute('aria-pressed',String(tab.dataset.view === mode));
    if (mode === 'preview') preview(find('[data-preview]'), textarea.value, id);
    for (const button of dialog.querySelectorAll('.pd-tools button')) button.disabled = Boolean(busy);
    textarea.readOnly = Boolean(busy);
    find('[data-conflict]').hidden = !conflict;
    find('[data-save]').disabled = Boolean(busy || conflict || latest === null || (!dirty && latest.revision > 0));
    find('[data-save]').textContent = busy === 'saving' ? '正在保存…' : '保存文档';
    const stamp = latest?.updatedAt && new Date(latest.updatedAt);
    let status = latest?.revision ? '文档 v' + latest.revision : latest ? '新文档' : '';
    if (stamp && Number.isFinite(stamp.getTime())) status += ' · ' + stamp.toLocaleString('zh-CN',{hour12:false});
    if (dirty) status += (status ? ' · ' : '') + '草稿待保存';
    find('[data-stamp]').textContent = status;
  }
  function edited() {
    dirty = true;
    remember();
    message('');
    refresh();
  }
  async function request(url, options = {}) {
    const response = await fetch(url, {...options,cache:'no-store',signal:life.signal});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error(data.error || '服务器未完成请求（HTTP ' + response.status + '）。'),{status:response.status});
    return data;
  }
  async function load() {
    busy = 'loading'; find('[data-retry]').hidden = true;
    message('正在读取说明文档…'); refresh();
    try {
      const doc = await request(endpoint);
      if (typeof doc.text !== 'string' || !Number.isSafeInteger(doc.revision) || doc.revision < 0) throw Error('服务器返回的文档格式无效。');
      latest = doc;
      baseline = doc.revision === 0 && !doc.text ? template(graph) : doc.text;
      if (dirty && doc.revision > 0 && textarea.value === doc.text) {
        clearDraft(textarea.value); dirty = false;
      }
      conflict = dirty && baseRevision !== doc.revision && !(baseRevision === null && doc.revision === 0);
      if (conflict) {
        find('[data-conflict] pre').textContent = doc.text;
        mode = 'edit';
        message('保存前请先处理版本冲突。',true);
      } else {
        baseRevision = doc.revision;
        if (!dirty) textarea.value = baseline;
        message(dirty ? '已保留本机草稿。' : doc.revision === 0 ? '可编辑这份初稿，保存后会生成第一个文档版本。' : '');
      }
    } catch (error) {
      if (life.signal.aborted) return;
      latest = null;
      if (!dirty) { textarea.value = baseline || template(graph); mode = 'edit'; }
      message('暂时无法读取文档。' + error.message + ' 可以继续编辑本机草稿。',true);
      find('[data-retry]').hidden = false;
    } finally { if (!life.signal.aborted) { busy = ''; refresh(); } }
  }
  async function save() {
    if (find('[data-save]').disabled) return;
    busy = 'saving'; message('正在保存…'); refresh();
    const text = textarea.value;
    try {
      const doc = await request(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,expectedRevision:baseRevision})});
      if (!Number.isSafeInteger(doc.revision) || doc.revision < 1) throw Error('服务器没有确认文档版本。');
      latest = doc; baseRevision = doc.revision; baseline = text; dirty = false;
      clearDraft(text); message('已保存。');
    } catch (error) {
      if (life.signal.aborted) return;
      dirty = true; remember();
      if (error.status === 409) { await load(); return; }
      message('保存失败，已保留当前草稿。' + error.message,true);
    } finally { if (!life.signal.aborted) { busy = ''; refresh(); } }
  }
  function insertLines(prefix) {
    const source = textarea.value, start = textarea.selectionStart, end = textarea.selectionEnd;
    const first = start > 0 ? source.lastIndexOf('\n',start-1) + 1 : 0;
    const lines = source.slice(first,end).split('\n');
    if (lines.length > 1 && lines.at(-1) === '') lines.pop();
    const decorated = lines.map((line,index) => (prefix === '1. ' ? (index+1)+'. ' : prefix) + line);
    const last = end > first && source[end-1] === '\n' ? end-1 : end;
    textarea.setRangeText(decorated.join('\n'),first,last,'end');
    textarea.focus(); edited();
  }
  async function insertImages(selected) {
    if (!selected.length || busy) return;
    busy = 'uploading'; refresh();
    const errors = [], images = [];
    const first = textarea.selectionStart, end = textarea.selectionEnd;
    for (const [index,file] of selected.entries()) {
      message('正在上传第 ' + (index+1) + '/' + selected.length + ' 张图片…');
      try {
        const extension = file.name.split('.').at(-1)?.toLowerCase();
        const mime = file.type || ({png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',gif:'image/gif'})[extension];
        if (!imageTypes.has(mime)) throw Error('支持 PNG、JPEG、WebP 和 GIF。');
        if (!file.size || file.size > maxImageBytes) throw Error('图片须大于 0 字节，且不超过 10 MiB。');
        const answer = await request(imagePath+'?'+new URLSearchParams({id}),{method:'POST',headers:{'Content-Type':mime},body:file});
        const url = imageUrl(answer.url, id);
        if (!url) throw Error('服务器未返回本流水线的图片地址。');
        const alt = file.name.replace(/\.[^.]+$/,'').replace(/[\[\]\\\r\n]/g,' ').trim() || '流程插图';
        images.push({alt,url});
      } catch (error) {
        if (life.signal.aborted) return;
        errors.push(file.name+'：'+error.message);
      }
    }
    busy = '';
    if (images.length) {
      const left = first > 0 && textarea.value[first-1] !== '\n' ? '\n' : '';
      const right = end < textarea.value.length && textarea.value[end] === '\n' ? '' : '\n';
      const links = images.map(({alt,url}) => '!['+alt+']('+url+')');
      textarea.setRangeText(left+links.join('\n')+right,first,end,'end');
      mode = 'preview';
      edited();
      find('[data-view="preview"]').focus({preventScroll:true});
      const firstImage = [...find('[data-preview]').querySelectorAll('img')].find(img => img.getAttribute('src') === images[0].url);
      firstImage?.scrollIntoView({block:'nearest'});
    } else {
      refresh(); textarea.focus();
    }
    const feedback = images.length ? '已插入 ' + images.length + ' 张图片。点击「保存文档」保留这次修改。' : '';
    message([feedback,...errors].filter(Boolean).join('　'),Boolean(errors.length));
  }
  function close() {
    if ((dirty || busy === 'uploading' || busy === 'saving') && !window.confirm('离开说明文档？未保存的正文将保留为本机草稿。')) return false;
    if (dirty) remember();
    life.abort();
    dialog.close(); dialog.remove();
    openEditor = null;
    return true;
  }
  textarea.addEventListener('input',edited);
  files.addEventListener('change',() => {
    const selected = Array.from(files.files); files.value = ''; void insertImages(selected);
  });
  dialog.addEventListener('click',event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.hasAttribute('data-close')) close();
    else if (button.dataset.view) { mode=button.dataset.view; refresh(); if (mode==='edit') textarea.focus(); }
    else if (button.dataset.prefix) insertLines(button.dataset.prefix);
    else if (button.hasAttribute('data-image')) files.click();
    else if (button.hasAttribute('data-save')) void save();
    else if (button.hasAttribute('data-retry')) void load();
    else if (button.hasAttribute('data-rebase') && latest) {
      baseRevision = latest.revision; conflict = false; mode = 'edit'; remember(); refresh();
      message('当前草稿已基于最新版本，可以继续编辑或保存。');
    }
  });
  dialog.addEventListener('cancel',e => { e.preventDefault(); close(); });
  dialog.addEventListener('keydown',e => {
    if ((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==='s') { e.preventDefault();e.stopPropagation();void save(); }
  });
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}},{signal:life.signal});
  openEditor = {close};
  refresh(); dialog.showModal(); void load();
}
