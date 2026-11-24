import assert from 'node:assert/strict';
import test from 'node:test';
import {seedGraphs,validateGraph} from './dist/model.js';
import {reviewGraph} from './dist/review-logic.js';
import {supportsHandGraph} from './dist/dataset-picker.js';

function hand({score = true, feedback = true} = {}) {
  const [graph] = seedGraphs();
  if (feedback) return graph;
  const tail = score ? 'score' : 'segment';
  const ids = new Set(['input','qwen','segment',tail,'output']);
  graph.nodes = graph.nodes.filter(n => ids.has(n.id));
  graph.edges = graph.edges.filter(e => ids.has(e.from) && ids.has(e.to));
  graph.edges.push({id:'finish',from:tail,to:'output',condition:'next'});
  return graph;
}
const records = () => seedGraphs()[1];
const config = (g,id) => g.nodes.find(n => n.id === id).config;

test('three server layouts and the JSONL template retain their available modes', () => {
  for (const graph of [hand(),hand({feedback:false}),hand({feedback:false,score:false})]) {
    assert.equal(supportsHandGraph(graph),true);
    assert.deepEqual(reviewGraph(graph),{modes:['demo','live'],findings:[]});
  }
  assert.deepEqual(reviewGraph(records()),{modes:['demo','records'],findings:[]});
});

test('branch errors carry node and edge IDs without changing the start validation messages', () => {
  const graph = hand();
  const edge = graph.edges.find(e => e.condition === 'retry');
  edge.condition = 'pass';
  const report = reviewGraph(graph);
  assert.deepEqual(report.modes,[]);
  assert.deepEqual(validateGraph(graph),[...new Set(report.findings.map(f => f.message))]);
  assert.ok(report.findings.some(f => f.scope === 'graph' && f.nodeId === 'gate' && f.edgeId === edge.id && f.message.includes('重复')));
  assert.ok(report.findings.some(f => f.nodeId === 'gate' && f.message.includes('缺少「重试」')));
});

test('reversed gate branches can be rehearsed but not submitted to the server', () => {
  const graph = hand();
  const retry = graph.edges.find(e => e.condition === 'retry');
  const review = graph.edges.find(e => e.condition === 'review');
  // Exchange the branches to different output nodes (no implicit non-retry cycle).
  const extra = {...graph.nodes.find(n => n.type === 'output'),id:'out2',name:'第二份结果'};
  graph.nodes.push(extra);
  retry.to = extra.id;
  review.to = 'refine';
  graph.edges.find(e => e.id === 'refine-segment').to = 'output';
  assert.deepEqual(validateGraph(graph),[]);
  const report = reviewGraph(graph);
  assert.deepEqual(report.modes,['demo']);
  assert.equal(report.findings[0].level,'warning');
  assert.equal(report.findings[0].nodeId,'out2');

  graph.nodes.pop();
  retry.to = 'output';
  const issue = reviewGraph(graph).findings[0];
  assert.equal(issue.edgeId,retry.id);
  assert.equal(issue.nodeId,'gate');
  assert.match(issue.message,/提示词改写/);
});

test('server-only parameters and null values are located, while rehearsal stays available', () => {
  const graph = hand();
  config(graph,'gate').maxRetries = 3;
  config(graph,'qwen').sample_fps = 10;
  config(graph,'input').task = null;
  config(graph,'score').every = null;
  config(graph,'segment').extra = true;
  const {modes,findings} = reviewGraph(graph);
  assert.deepEqual(modes,['demo']);
  assert.ok(findings.every(f => f.scope === 'live' && f.level === 'error'));
  assert.deepEqual(new Set(findings.map(f => f.nodeId)),new Set(['gate','qwen','input','score','segment']));
});

test('hand and object targets both support the server pipeline', () => {
  assert.equal(config(hand(),'input').task,'objects');
  for (const task of ['hands','objects']) {
    const graph = hand();
    config(graph,'input').task = task;
    assert.deepEqual(reviewGraph(graph),{modes:['demo','live'],findings:[]});
  }
});

test('overlapping names preserve distinct error locations; cycles are still rejected', () => {
  const graph = records();
  graph.nodes.filter(n => n.type === 'filter' || n.type === 'transform').forEach(n => { n.x = null; n.name = '处理'; });
  graph.edges.find(e => e.from === 'filter').to = 'transform';
  const {findings,modes} = reviewGraph(graph);
  assert.deepEqual(modes,[]);
  assert.equal(validateGraph(graph).filter(m => m.includes('坐标')).length,1);
  assert.deepEqual(findings.filter(f => f.message.includes('坐标')).map(f => f.nodeId),['transform','filter']);
  assert.ok(findings.some(f => f.message.includes('循环')));
});

test('known built-in field constraints block records only; external fields are advisory', () => {
  const graph = records();
  config(graph,'transform').targetKey = 'constructor';
  config(graph,'filter').key = 'provided_by_input';
  const report = reviewGraph(graph);
  assert.deepEqual(report.modes,['demo']);
  assert.ok(report.findings.some(f => f.scope === 'records' && f.level === 'error' && f.nodeId === 'transform'));
  assert.ok(report.findings.some(f => f.level === 'warning' && f.nodeId === 'filter'));
  config(graph,'transform').targetKey = 'clean_text';
  assert.deepEqual(reviewGraph(graph).modes,['demo','records']);
  config(graph,'filter').key = 'clean_text';
  assert.deepEqual(reviewGraph(graph).findings,[]);

  // Custom code can redefine the output schema. Built-in field semantics no longer apply.
  graph.nodes.find(n => n.type === 'transform').implementation = {kind:'records',code:'def run(records, config): return records'};
  config(graph,'transform').targetKey = 'constructor';
  assert.deepEqual(reviewGraph(graph),{findings:[],modes:['demo','records']});
});

test('a saved native code version is checked by shape only; actual code is never run', () => {
  const graph = hand();
  const node = graph.nodes.find(n => n.type === 'qwen');
  node.implementation = {kind:'native',code:'raise RuntimeError()',baseHash:'0'.repeat(64)};
  assert.deepEqual(reviewGraph(graph).modes,['demo','live']);
  delete node.implementation.baseHash;
  const report = reviewGraph(graph);
  assert.deepEqual(report.modes,['demo']);
  assert.equal(report.findings[0].nodeId,node.id);
});

test('mixed executor types have a located advisory; arbitrary IDs work', () => {
  const graph = hand({feedback:false,score:false});
  const node = graph.nodes.find(n => n.type === 'qwen');
  node.id = 'my-prompt';
  graph.edges.find(e => e.from === 'qwen').from = node.id;
  graph.edges.find(e => e.to === 'qwen').to = node.id;
  assert.deepEqual(reviewGraph(graph).modes,['demo','live']);
  node.type = 'filter';
  node.config = {};
  assert.deepEqual(validateGraph(graph),[]);
  const {findings,modes} = reviewGraph(graph);
  assert.deepEqual(modes,['demo']);
  assert.equal(findings[0].nodeId,node.id);
});
