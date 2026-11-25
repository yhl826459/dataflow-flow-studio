import {types,dualSegment,portIssues} from './model.js';

// Keep the editor's launch eligibility and save-time explanation on the same topology rules.
export function handGraphIssue(graph) {
  const portIssue=portIssues(graph)[0];
  if(portIssue)return portIssue;
  const byId = new Map(graph.nodes.map(node => [node.id,node]));
  const present = new Set(graph.nodes.map(node => node.type));
  const feedback = present.has('gate') || present.has('refine');
  const fusion = present.has('fusion');
  const finish = fusion ? 'fusion' : 'output';
  const manual = present.has('prompt');
  if (manual && (feedback || present.has('score'))) return {message:'中文提示分割使用输入、中文目标描述、分割、可选融合和输出。'};
  const order = ['input',manual?'prompt':'qwen','segment',...(present.has('score') || feedback ? ['score'] : [])];
  const expectedNodes = [...order,...(feedback ? ['gate','refine'] : []),...(fusion ? ['fusion'] : []),'output'];
  const seen = new Set();
  for (const node of graph.nodes) {
    if (!expectedNodes.includes(node.type) || (node.implementation && node.implementation.kind !== 'native')) {
      return {nodeId:node.id,message:'「' + node.name + '」无法接入视频分割入口，该入口只接收视频处理所需的原生算子。当前组合只能演练。'};
    }
    if (seen.has(node.type)) return {nodeId:node.id,message:'视频分割入口只接受一个「' + types[node.type].label + '」，图中出现了多个。'};
    seen.add(node.type);
  }
  const connections = order.slice(1).map((to,i) => [order[i],to,'next']);
  if(dualSegment(graph))connections[0]=['input','segment','next'];
  if (feedback) connections.push(['score','gate','next'],['gate',finish,'pass'],['gate',finish,'review'],
    ['gate','refine','retry'],['refine','segment','next']);
  else connections.push([order.at(-1),finish,'next']);
  if (fusion) connections.push(['fusion','output','next']);

  const matched = new Set();
  for (const edge of graph.edges) {
    const source = byId.get(edge.from), dest = byId.get(edge.to);
    const condition = edge.condition || 'next';
    const connection = connections.find(([from,,route]) => from === source?.type && route === condition);
    if (!connection || !dest) return {edgeId:edge.id,message:'这条连线的端点或支路条件与视频分割入口不匹配。'};
    if (connection[1] !== dest.type) {
      const route = {next:'下一步',pass:'通过',review:'待复核',retry:'重试'}[condition];
      return {nodeId:source.id,edgeId:edge.id,message:'服务器要求「' + source.name + '」的「' + route +
        '」连接「' + types[connection[1]].label + '」，当前连接的是「' + dest.name + '」。'};
    }
    if (matched.has(connection)) return {edgeId:edge.id,message:'视频分割入口不接受重复连线。'};
    matched.add(connection);
  }
  const missing = expectedNodes.filter(type => !present.has(type));
  if (missing.length) return {message:'视频分割入口还需要以下算子：' + missing.map(type => types[type].label).join('、') + '。'};
  if (matched.size !== connections.length) return {message:'尚未连接视频分割入口要求的所有支路。'};
  return null;
}

export function supportsHandGraph(graph) {
  return !handGraphIssue(graph);
}
