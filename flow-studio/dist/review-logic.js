import {graphIssues, types} from './model.js';
import {canProcessRecords} from './records.js';
import {handGraphIssue} from './hand-graph.js';

function reviewRecords(graph) {
  const findings = [];
  const nodes = new Map(graph.nodes.map(node => [node.id,node]));
  const successor = new Map(graph.edges.map(edge => [edge.from,edge.to]));
  // A structurally valid records graph is a single chain. Original input fields survive built-in steps.
  const knownFields = new Set();
  let node = graph.nodes.find(node => node.type === 'input');
  while (node) {
    const config = node.config ?? {};
    if (node.implementation) {
      if (new TextEncoder().encode(node.implementation.code).length > 65536) {
        findings.push({level:'error',scope:'records',nodeId:node.id,message:'「' + node.name + '」的源码超过服务器上限 64 KiB。'});
      }
      knownFields.clear();
    } else if (node.type === 'transform' || node.type === 'filter') {
      const transforms = node.type === 'transform';
      const source = (transforms ? config.sourceKey : config.key) ?? 'text';
      const target = config.targetKey ?? 'text';
      if (knownFields.size && !knownFields.has(source)) {
        const consequence = transforms ? '缺少该字段的记录会使转换失败。' : '缺少该字段的记录会被滤掉。';
        findings.push({level:'warning', scope:'records', nodeId:node.id,
          message:'「' + node.name + '」读取「' + source + '」。请确认输入记录或上游 Python 提供该字段。' + consequence});
      }
      knownFields.add(source);
      if (transforms) {
        if (['__proto__','constructor','prototype'].includes(target)) {
          findings.push({level:'error', scope:'records', nodeId:node.id,
            message:'「' + node.name + '」的输出字段「' + target + '」是内置转换器的保留名称，请更换输出字段。'});
        }
        knownFields.add(target);
      }
    }
    node = nodes.get(successor.get(node.id));
  }
  return findings;
}

function reviewHandParameters(graph) {
  const findings = [];
  const block = (message,nodeId,edgeId) => findings.push({level:'error',scope:'live',message,nodeId,edgeId});
  if (typeof graph.id !== 'string' || !graph.id.length || [...graph.id].length > 128 ||
      !Number.isInteger(graph.version) || graph.version < 1) {
    block('服务器启动需要有效的流水线标识和已保存的正整数版本号。');
  }
  for (const node of graph.nodes) {
    const config = node.config === undefined ? {} : node.config;
    if (config === null) {
      block('「' + node.name + '」的服务器参数需要是对象。',node.id);
      continue;
    }
    const fields = types[node.type].fields;
    const unsupported = Object.keys(config).filter(key => !fields.some(field => field.key === key));
    if (unsupported.length) block('「' + node.name + '」含有服务器入口不支持的参数「' + unsupported.join('、') + '」。',node.id);
    for (const field of fields) {
      if (config[field.key] === null) block('「' + node.name + '」的「' + field.label + '」在服务器上不能设为 null。',node.id);
    }
    if (node.type === 'qwen' && (config.sample_fps ?? 5) !== 5) block('当前服务器的 Qwen 提词固定按 5 fps 抽帧。',node.id);
    if (node.type === 'gate' && (config.maxRetries ?? 2) !== 2) block('当前服务器的质量门控固定最多重试 2 次。',node.id);
    if (Object.hasOwn(node,'implementation')) {
      const impl = node.implementation;
      if (!impl || Object.keys(impl).length !== 3 || typeof impl.baseHash !== 'string' || !/^[a-f0-9]{64}$/.test(impl.baseHash) ||
          new TextEncoder().encode(impl.code).length > 65536) {
        block('「' + node.name + '」的源码版本或大小不符合服务器要求，请重新打开算子编辑器并保存。',node.id);
      }
    }
  }
  for (const edge of graph.edges) {
    if (Object.hasOwn(edge,'condition') && !edge.condition) {
      block('请给服务器连线设置明确的支路条件，普通连线使用 next。',edge.from,edge.id);
    }
  }
  return findings;
}

// Modes describe only what this saved graph can run. Input files, code and server access are checked at launch.
export function reviewGraph(graph) {
  const findings = graphIssues(graph).map(issue => ({...issue,level:'error',scope:'graph'}));
  if (findings.length) return {findings,modes:[]};
  const modes = ['demo'];
  if (canProcessRecords(graph)) {
    findings.push(...reviewRecords(graph));
    if (!findings.some(f => f.level === 'error')) modes.push('records');
  } else {
    const topologyIssue = handGraphIssue(graph);
    if (topologyIssue) findings.push({level:'warning',scope:'live',...topologyIssue});
    else {
      findings.push(...reviewHandParameters(graph));
      if (!findings.some(f => f.level === 'error')) modes.push('live');
    }
  }
  return {findings,modes};
}
