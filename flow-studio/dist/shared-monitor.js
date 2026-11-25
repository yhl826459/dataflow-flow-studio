import {mountCanvas} from './canvas.js';
import {seedGraphs,types} from './model.js';
import {nodeSummary,summaryFields} from './operator-summary.js';
import {absPanel} from './abs-monitor.js';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[char]);
const statuses = {running:'运行中', launching:'准备视频', completed:'已完成', failed:'失败',
  interrupted:'已中断', waiting:'等待中', done:'已完成', skipped:'已跳过', paused:'已暂停', queued:'排队中'};
const status = key => statuses[key] ?? key;
const goal = task => task === 'objects' ? '找物' : task === 'hands' ? '找手' : '—';
const time = value => value ? new Date(value).toLocaleString('zh-CN') : '—';
let state = {runs:[], queueItems:[], queueOrder:[]};
let tab = 'monitor';
let selected = '';
let graphs = null;
let selectedGraph = '';
let selectedNode = '';
let canvas;
const loading = new Set();

function pipelines() {
  if (!graphs?.length) {
    $('view').innerHTML = '<div class="listpanel empty">'+(graphs ? '暂无已保存的流水线。' : '正在读取已保存的编排…')+'</div>';
    return;
  }
  const graph = graphs.find(item => item.id === selectedGraph) ?? graphs[0];
  selectedGraph = graph.id;
  const node = graph.nodes.find(item => item.id === selectedNode) ?? graph.nodes[0];
  $('view').innerHTML = `<div class="monitorbar"><label class="label">选择流水线
    <select class="field" id="pipeline-select">
    ${graphs.map(item => `<option value="${esc(item.id)}" ${item.id===graph.id?'selected':''}>${esc(item.name)} · v${esc(item.version)}</option>`).join('')}
    </select></label></div>
    <div class="editor-grid"><section class="board">
      <div class="boardhead"><strong>${esc(graph.name)}</strong><span class="caption">已保存 · ${esc(time(graph.updatedAt))}</span></div>
      <div id="shared-canvas" class="canvas-host"></div>
      <div class="boardfoot"><span>${esc(graph.description || '点击画布节点查看算子说明。')}</span></div>
    </section><aside class="inspector" aria-label="算子说明">
      <div class="boxhead"><strong>算子说明</strong></div><div id="shared-operator" class="inspect-body" aria-live="polite"></div>
    </aside></div>`;
  function showOperator(item) {
    selectedNode = item?.id ?? '';
    if (!item) {
      $('shared-operator').textContent = '这条流水线还没有算子。';
      return;
    }
    const config = {...item.config};
    // Free-text parameter values stay in the owner workbench.
    if (item.type === 'filter') Object.assign(config,{key:'输入字段',value:'设定文本'});
    if (item.type === 'transform') Object.assign(config,{sourceKey:'输入字段',targetKey:'输出字段'});
    const summary = Object.hasOwn(types,item.type) ? nodeSummary({...item,config},graph) : item.notes;
    if (item.implementation?.kind === 'native' && !item.notes?.logic) summary.logic='执行此节点保存的服务器 Python 算子。';
    if (item.implementation && !item.notes?.model) summary.model='由自定义算子决定。';
    $('shared-operator').innerHTML = `<strong>${esc(item.name)}</strong><dl class="op-summary">${summaryFields.map(([key,label]) =>
      `<dt>${label}</dt><dd>${esc(summary?.[key] || '暂无说明')}</dd>`).join('')}</dl>`;
  }
  $('pipeline-select').onchange = event => {selectedGraph=event.target.value; selectedNode=''; render();};
  showOperator(node);
  canvas = mountCanvas($('shared-canvas'), {graph,viewKey:'shared-'+graph.id,mode:'readonly',selected:node?.id,
    onSelect:id=>showOperator(graph.nodes.find(item=>item.id===id))});
}

function fallback(run) {
  const graph = seedGraphs()[0];
  const enabled = new Set(run.enabledTypes);
  if (!enabled.size) return graph;
  if (!enabled.has('fusion')) graph.edges = graph.edges.filter(e => e.from !== 'fusion')
    .map(e => e.to === 'fusion' ? {...e, to:'output'} : e);
  if (!enabled.has('gate') && enabled.has('output')) {
    const last = enabled.has('score') ? 'score' : enabled.has('segment') ? 'segment' : null;
    if (last) graph.edges.push({id:last+'-output',from:last,to:'output',condition:'next',label:''});
  }
  return {...graph, nodes:graph.nodes.filter(n => enabled.has(n.type)),
    edges:graph.edges.filter(e => enabled.has(e.from) && enabled.has(e.to))};
}

function clipList(clips, fusion, key, opened) {
  if (!clips.length) return '';
  const seams=fusion?.reviewSeams??[], notes=fusion?.reviewNotes??[], empty=fusion?.emptyFrameRanges??[];
  return `<details class="queue-clips" data-clip-key="${esc(key)}" ${opened.has(key)?'open':''}>
    <summary>${clips.length} 段 · ${clips.filter(c => c.status === 'done').length} 段已完成${seams.length?' · '+seams.length+' 处接缝待复核':''}${notes.length||empty.length?' · 融合待复核':''}</summary>
    <ol>${clips.map(c => `<li><b>第 ${c.index} 段</b> · 帧 ${c.firstFrame}–${c.firstFrame+c.frames-1}
      <small>${esc(status(c.status))} ${c.steps.filter(s => s.status !== 'waiting').map(s =>
        esc(s.stage)+'（'+esc(status(s.status))+'）'+(s.detail?' · '+esc(s.detail):'')).join(' · ')}</small></li>`).join('')}</ol>
    ${notes.length?`<ul>${notes.map(n=>`<li>第 ${esc(n.part)} 段 · ${esc(n.localId)} · 帧 ${esc(n.firstFrame)}–${esc(n.lastFrame)} · ${esc(n.reason)}</li>`).join('')}</ul>`:''}
    ${empty.length?`<p>无掩码帧待复核 · ${empty.map(([a,b])=>esc(a)+'–'+esc(b-1)).join('、')}</p>`:''}
    ${seams.length?`<strong>待复核接缝</strong><ul>${seams.map(s =>
      `<li>第 ${s.left} → ${s.right} 段 · 帧 ${s.firstFrame}–${s.firstFrame+s.frames-1} · ${esc(s.reason)}</li>`).join('')}</ul>`:''}
  </details>`;
}

function monitor(opened) {
  const run = state.runs.find(r => r.id === selected) ?? state.runs.find(r => r.status === 'running') ?? state.runs[0];
  selected = run.id;
  const graph = run.graph ?? fallback(run);
  const names = new Map(graph.nodes.map(n => [n.id,n.name]));
  $('view').innerHTML = `<div class="monitorbar"><select class="field" id="run-select" aria-label="选择运行记录">
    ${state.runs.map(r => `<option value="${esc(r.id)}" ${r.id===run.id?'selected':''}>${esc(r.name)} · ${esc(status(r.status))} · ${esc(time(r.startedAt))}</option>`).join('')}
    </select></div><div class="editor-grid">
    <div><section class="board"><div class="boardhead"><strong>执行路径</strong><span class="caption">${esc(graph.name)}</span></div>
    <div id="shared-canvas" class="canvas-host"></div></section>
    ${absPanel(run,esc,opened)}<section class="events"><div class="boxhead"><strong>执行事件</strong></div><div class="eventlog">
    ${run.events.slice().reverse().map(e => `<div class="event"><span class="time">${esc(time(e.time))}</span><b>${esc(names.get(e.nodeId)??'任务')}</b><span>${esc(e.message)}</span></div>`).join('')}
    </div></section></div><aside class="inspector"><div class="boxhead"><strong>${esc(run.name)}</strong>
    <span class="badge">${esc(status(run.status))}</span></div><div class="inspect-body">
    <p>目标 · ${esc(goal(run.task))}</p><p>开始 · ${esc(time(run.startedAt))}</p><p>结束 · ${esc(time(run.endedAt))}</p>
    ${run.current?`<p>当前算子 · ${esc(names.get(run.current)??run.current)}</p>`:''}
    ${run.activity?`<p>${esc(run.activity)}</p>`:''}
    ${run.fusion?`<p>融合 · ${run.fusion.tracks} 条轨迹</p>`:''}
    ${clipList(run.clips,run.fusion,run.ref,opened)}
    </div></aside></div>`;
  $('run-select').onchange = event => {selected = event.target.value; render();};
  canvas = mountCanvas($('shared-canvas'), {graph, viewKey:run.id, mode:'monitor',
    running:run.status === 'running', states:run.nodeStates, activeEdges:run.activeEdges});
}

function queue(opened) {
  const byRef = new Map(state.queueItems.map(item => [item.ref,item]));
  const byRun = new Map(state.runs.map(run => [run.ref,run]));
  const waiting = state.queueOrder.map(ref => byRef.get(ref)).filter(Boolean);
  const running = state.queueItems.filter(item => ['running','launching'].includes(item.status));
  const active = new Set(running.map(item => item.ref));
  running.push(...state.runs.filter(run => run.status === 'running' && !active.has(run.ref)));
  const recent = state.runs.filter(run => ['completed','failed','interrupted'].includes(run.status)).slice(0,6);
  function row(item) {
    const run = byRun.get(item.ref);
    let clips = run?.clips ?? [];
    if (!clips.length && item.clipPlan) {
      const {startFrame, frameCount, parts, overlap=0} = item.clipPlan;
      const stride = 81-overlap;
      clips = Array.from({length:parts}, (_,i) => ({index:i+1,firstFrame:startFrame+i*stride,
        frames:Math.min(81,frameCount-i*stride),status:'waiting',steps:[]}));
    }
    return `<li class="queue-row"><div><strong>${esc(item.name)}</strong><small>${esc(goal(item.task))}</small></div>
    <span>${esc(status(item.status))}</span>${run?`<button class="linkbtn" data-run="${esc(run.id)}">查看运行</button>`:''}
    ${clipList(clips,run?.fusion,item.ref,opened)}</li>`;
  }
  return `<section class="queue-monitor queue-page"><h2>服务器执行顺序</h2>
    <h3>执行中 · ${running.length} 条</h3><ul>${running.map(row).join('')||'<li class="caption">当前没有运行中的序列。</li>'}</ul>
    <h3>待处理 · ${waiting.length} 条</h3><ol>${waiting.map(row).join('')||'<li class="caption">暂无待处理序列。</li>'}</ol>
    ${recent.length?`<h3>最近处理记录</h3><ul>${recent.map(row).join('')}</ul>`:''}</section>`;
}

function render() {
  const opened = new Set([...$('view').querySelectorAll('details[open]')].map(d => d.dataset.clipKey));
  for (const pane of ['pipelines','monitor','queue']) {
    const button = $(pane+'-tab');
    button.classList.toggle('active', tab===pane);
    button.setAttribute('aria-pressed', String(tab===pane));
  }
  canvas?.destroy();
  canvas = null;
  if (tab === 'pipelines') pipelines();
  else if (tab === 'queue') $('view').innerHTML = queue(opened);
  else if (!state.runs.length) $('view').innerHTML = '<div class="listpanel empty">暂无服务器运行记录。</div>';
  else monitor(opened);
}

async function poll() {
  const design = tab === 'pipelines';
  const path = design ? '/api/pipelines' : '/api/live';
  if (loading.has(path)) return;
  loading.add(path);
  try {
    const response = await fetch(path, {cache:'no-store'});
    if (!response.ok) throw Error();
    const data = await response.json();
    const updated = design ? data.pipelines : data;
    const changed = JSON.stringify(updated) !== JSON.stringify(design ? graphs : state);
    if (design) graphs = updated;
    else state = updated;
    if ((tab === 'pipelines') === design) {
      $('connection').textContent = '已连接 amax · '+new Date().toLocaleTimeString('zh-CN');
      if (changed || !$('view').childElementCount) render();
    }
  } catch {
    if ((tab === 'pipelines') === design) $('connection').textContent = '暂时无法读取 amax 工作台，页面会自动重试。';
  } finally { loading.delete(path); }
}

for (const pane of ['pipelines','monitor','queue']) {
  $(pane+'-tab').onclick = () => {tab=pane; render(); poll();};
}
$('refresh').onclick = poll;
$('view').onclick = event => {
  const button = event.target.closest('[data-run]');
  if (button) {selected = button.dataset.run; tab='monitor'; render();}
};
poll();
setInterval(poll,4000);
