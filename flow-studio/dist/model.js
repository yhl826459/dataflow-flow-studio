// Path data for the workbench's 24-pixel line icons.
const paths = {
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
  workflow: '<rect x="2.5" y="4" width="6" height="5.5" rx="1.4"/><rect x="15.5" y="14.5" width="6" height="5.5" rx="1.4"/><path d="M8.5 6.8h5a3 3 0 0 1 3 3v4.7M11.5 6.8v7.5a3 3 0 0 0 3 3h1"/>',
  activity: '<path d="M2 12h4l3-8 5.1 16L17 12h5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.7"/><path d="m16 16 4.5 4.5"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronRight: '<path d="m9 6 6 6-6 6"/>',
  play: '<path d="M7 4.8a.6.6 0 0 1 .9-.5l12 7.2a.6.6 0 0 1 0 1l-12 7.2a.6.6 0 0 1-.9-.5Z"/>',
  save: '<path d="M4 3.5h13L20.5 7v12a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V5Z"/><path d="M7 3.5v6h9v-6M7 20v-7h10v7"/>',
  settings: '<path d="m9 3-.8 2.1-2 .8L4 5.4 2.5 8l1.6 1.8-.2 2.2L2.3 14l1.6 2.6 2.1-.6 2 .9 1 2.1h3.1l.9-2.1 2.1-.9 2.1.6 1.6-2.7-1.5-1.8v-2.2l1.5-1.8-1.6-2.7-2.1.5-2-.8L12.2 3Z" transform="translate(1 1)"/><circle cx="11.6" cy="12" r="3"/>',
  more: '<circle cx="5" cy="12" r="1" fill="currentColor"/><circle cx="12" cy="12" r="1" fill="currentColor"/><circle cx="19" cy="12" r="1" fill="currentColor"/>',
  box: '<path d="m12 2.8 8.3 4.6v9.2L12 21.2l-8.3-4.6V7.4L12 2.8Z"/><path d="m3.7 7.4 8.3 4.5 8.3-4.5M12 12v9"/>',
  filter: '<path d="M3.5 4.5h17l-6.7 7.9v5.2L10 20v-7.6Z"/>',
  stages: '<path d="M4 6h8M4 12h5m-5 6h11"/><circle cx="16" cy="6" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="18" r="2"/>',
  grid: '<rect x="3.5" y="3.5" width="6.5" height="6.5" rx="1.3"/><rect x="14" y="3.5" width="6.5" height="6.5" rx="1.3"/><rect x="3.5" y="14" width="6.5" height="6.5" rx="1.3"/><rect x="14" y="14" width="6.5" height="6.5" rx="1.3"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5.2l3.5 2.1"/>',
  check: '<path d="m4.5 12 4.5 4.5 10-10"/>',
  edit: '<path d="m14 5 5 5-9.7 9.7-6.1 1 1-6.1L14 5Zm0 0 2.2-2.2a1.5 1.5 0 0 1 2.1 0l2.9 2.9a1.5 1.5 0 0 1 0 2.1L19 10"/>',
  trash: '<path d="M3.5 6.5h17m-12-3.3h7l.7 3.3m-10 0 .7 13h10.2l.7-13M10 10v6m4-6v6"/>',
  link: '<path d="m9 14.5-1.1 1a3.6 3.6 0 1 1-5.1-5.1L7 6.3a3.6 3.6 0 0 1 5.1 0m-.2 11.4a3.6 3.6 0 0 0 5.1 0l4.2-4.1a3.6 3.6 0 0 0-5.1-5.1l-1.1 1M8.5 15.5l7-7"/>',
  eye: '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z"/><circle cx="12" cy="12" r="2.8"/>',
  sliders: '<path d="M3.5 6.5h6m4 0h7m-17 6h11m4 0h2m-17 6h2m4 0h11"/><circle cx="11.5" cy="6.5" r="2"/><circle cx="16.5" cy="12.5" r="2"/><circle cx="7.5" cy="18.5" r="2"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  history: '<path d="M3 11a9 9 0 1 1 2 7M3 5v6h6m3-4v5l3 2"/>',
  download: '<path d="M12 3.5v11m-4-4 4 4 4-4M4 15v4.5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V15"/>',
  upload: '<path d="M12 15V4m-4 4 4-4 4 4M4 15v4.5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V15"/>',
  copy: '<rect x="8" y="8" width="12.5" height="12.5" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  folder: '<path d="M3.5 5h6l2.3 2.5H20a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 .5-1Z"/>',
  stack: '<path d="m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5"/>',
  node: '<rect x="5" y="5" width="14" height="14" rx="3"/><path d="M1 12h4m14 0h4"/>',
  grip: '<g fill="currentColor" stroke="none"><circle cx="9" cy="5" r="1.5"/><circle cx="15" cy="5" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="19" r="1.5"/><circle cx="15" cy="19" r="1.5"/></g>',
  code: '<path d="m8 7-5 5 5 5m8-10 5 5-5 5M14 4l-4 16"/>',
  branch: '<circle cx="6.5" cy="5" r="2"/><circle cx="17.5" cy="6.5" r="2"/><circle cx="6.5" cy="19" r="2"/><path d="M6.5 7v10m0-3a4 4 0 0 1 4-4h3a4 4 0 0 0 4-1.5"/>',
  terminal: '<rect x="2.5" y="4" width="19" height="16" rx="2.5"/><path d="m6.5 8 3.5 3.5L6.5 15m6 .5h4"/>',
  layout: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/>',
  monitor: '<rect x="2.5" y="3.5" width="19" height="13" rx="2"/><path d="M12 16.5v4m-4 0h8"/>',
  hand: '<path d="M6.8 12.8 5.1 11a1.6 1.6 0 0 0-2.5 2l4.3 6A5.6 5.6 0 0 0 11.5 21h2.9a5.5 5.5 0 0 0 5.5-5.5v-5a1.5 1.5 0 0 0-3 0V7a1.5 1.5 0 0 0-3 0V4a1.5 1.5 0 0 0-3 0v3a1.5 1.5 0 0 0-3 0v6Z"/>',
  scan: '<path d="M3 9V5a2 2 0 0 1 2-2h4m6 0h4a2 2 0 0 1 2 2v4m0 6v4a2 2 0 0 1-2 2h-4m-6 0H5a2 2 0 0 1-2-2v-4M3 12h18"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
  checkCircle: '<circle cx="12" cy="12" r="9"/><path d="m7.5 12 3 3 6-6"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  logo: '<path d="m12 2.5 8.2 4.8v9.4L12 21.5l-8.2-4.8V7.3Z"/><path d="m6.5 8.8 5.5 3.3 5.5-3.3M12 12v6.2m-4.2-5.3 4.2 2.4 4.2-2.4"/>',
  arrowRight: '<path d="M3 12h17m-6-6 6 6-6 6"/>',
  minus: '<path d="M5 12h14"/>',
  fit: '<path d="M8.5 3.5h-5v5m12-5h5v5m0 7v5h-5m-7 0h-5v-5"/>',
  video: '<rect x="2.5" y="5.5" width="13" height="13" rx="2"/><path d="m15.5 10 5.5-3v10l-5.5-3"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx=".8"/><path d="M9 2.5V6m6-3.5V6M9 18v3.5m6-3.5v3.5M2.5 9H6m-3.5 6H6m12-6h3.5M18 15h3.5"/>',
  sparkles: '<path d="M13 3.5c.7 4.2 2.5 6.3 6.5 7-4 .7-5.8 2.8-6.5 7-.7-4.2-2.5-6.3-6.5-7 4-.7 5.8-2.8 6.5-7ZM5 14.5c.4 2.1 1.3 3.2 3.3 3.6-2 .4-2.9 1.5-3.3 3.6-.4-2.1-1.3-3.2-3.3-3.6 2-.4 2.9-1.5 3.3-3.6Z"/>'
};

export function icon(name, size = 18) {
  const path = Object.hasOwn(paths, name) ? paths[name] : paths.node;
  const side = Number.isFinite(size) ? Math.max(8, Math.min(size, 256)) : 18;
  return '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" width="' + side + '" height="' + side + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">' + path + '</svg>';
}

export const types = {
  input: {label:'视频输入', icon:'video', color:'#427B9A', description:'按序列读取 RGB 视频和任务清单。', fields:[
    {key:'task', label:'目标任务', type:'select', default:'objects', options:[{value:'hands',label:'交互手部'},{value:'objects',label:'交互物体'}]}
  ]},
  qwen: {label:'Qwen 提词', icon:'sparkles', color:'#8061B2', description:'从有序 RGB 帧中识别目标并复核描述短语。', fields:[
    {key:'sample_fps', label:'抽样帧率（fps）', type:'number', min:1, max:30, step:1, default:5}
  ]},
  segment: {label:'GenCeption 分割', icon:'scan', color:'#137F73', description:'为每个目标短语分别生成逐帧二值掩码。', fields:[]},
  score: {label:'ABS 评分', icon:'activity', color:'#B3702E', description:'在采样帧上评价各实例的局部掩码质量。', fields:[
    {key:'every', label:'采样间隔（帧）', type:'number', min:1, step:1, default:6}
  ]},
  gate: {label:'质量门控', icon:'branch', color:'#997228', description:'根据质量结果选择通过、重试或待复核支路。', fields:[
    {key:'threshold', label:'通过阈值（严格大于）', type:'number', min:0, max:10, step:0.1, default:7},
    {key:'maxRetries', label:'最多重试轮数', type:'number', min:0, max:10, step:1, default:2}
  ]},
  refine: {label:'提示词改写', icon:'edit', color:'#AE5D7F', description:'结合低分帧、局部掩码与历轮提示，改写当前实例的分割短语。', fields:[]},
  fusion: {label:'全视频掩码融合', icon:'layers', color:'#467A85', description:'使用共享帧匹配同一实例，输出整段视频的掩码轨迹。', fields:[]},
  output: {label:'结果输出', icon:'folder', color:'#387F67', description:'汇总各实例的分割掩码与处理状态。', fields:[]},
  python: {label:'Python 记录算子', icon:'code', color:'#59739C', description:'在服务器上用 Python 读取并写出一批 JSON 记录。', fields:[]},
  transform: {label:'数据转换', icon:'code', color:'#596EA4', description:'定义一次数据转换的输入和输出字段。', fields:[
    {key:'sourceKey',label:'输入字段',type:'text',default:'text'},
    {key:'targetKey',label:'输出字段',type:'text',default:'text'},
    {key:'operation',label:'转换方式',type:'select',default:'trim',options:[
      {value:'trim',label:'去除首尾空格'},{value:'uppercase',label:'转为大写'},{value:'lowercase',label:'转为小写'},{value:'copy',label:'原样复制'}
    ]}
  ]},
  filter: {label:'记录筛选', icon:'filter', color:'#687CAB', description:'按指定字段过滤记录。', fields:[
    {key:'key',label:'筛选字段',type:'text',default:'text'},
    {key:'rule',label:'保留条件',type:'select',default:'nonempty',options:[
      {value:'nonempty',label:'非空'},{value:'equals',label:'等于指定内容'},{value:'contains',label:'包含指定内容'}
    ]},
    {key:'value',label:'比较内容',type:'text',default:'',optional:true}
  ]}
};

function makeNode(type, x, y, name = types[type].label) {
  return {id:type, type, name, x, y, config:Object.fromEntries(types[type].fields.map(f => [f.key, f.default]))};
}

function makeEdge(id, from, to, label = '', condition = 'next') {
  return {id, from, to, label, condition};
}

export function seedGraphs() {
  return [{
    id:'hand-seg', name:'手部实例分割', version:1,
    description:'从 RGB 视频提取目标短语，逐实例分割，并用 ABS 评分驱动提示词重试。',
    nodes:[
      makeNode('input',0,15), makeNode('qwen',220,15), makeNode('segment',440,15),
      makeNode('score',660,15), makeNode('gate',440,185), makeNode('refine',215,185),
      makeNode('fusion',665,185), makeNode('output',665,355)
    ],
    edges:[
      makeEdge('in-qwen','input','qwen'), makeEdge('qwen-segment','qwen','segment'),
      makeEdge('segment-score','segment','score'), makeEdge('score-gate','score','gate'),
      makeEdge('gate-pass','gate','fusion','通过','pass'), makeEdge('gate-retry','gate','refine','重试','retry'),
      makeEdge('refine-segment','refine','segment'), makeEdge('gate-review','gate','fusion','待复核','review'),
      makeEdge('fusion-output','fusion','output')
    ]
  },{
    id:'record-prep', name:'文本清单整理', version:1,
    description:'可编辑示例，清理 JSONL 的 text 字段并筛除空白记录。',
    nodes:[makeNode('input',0,50,'JSONL 输入'),makeNode('transform',220,50,'清理文本'),makeNode('filter',440,50,'筛除空记录'),makeNode('output',660,50,'输出清单')],
    edges:[makeEdge('input-transform','input','transform'),makeEdge('transform-filter','transform','filter'),makeEdge('filter-output','filter','output')]
  }];
}

const presentText = value => typeof value === 'string' && value.trim().length > 0;
const validId = value => presentText(value) && !Object.hasOwn(Object.prototype, value) && value !== 'prototype';
const gateRoutes = ['pass','retry','review'];
const routeNames = {pass:'通过', retry:'重试', review:'待复核', next:'下一步'};

function reachable(starts, adjacent) {
  const seen = new Set(starts);
  const queue = [...starts];
  for (let i = 0; i < queue.length; i++) {
    for (const next of adjacent.get(queue[i]) || []) {
      if (!seen.has(next)) { seen.add(next); queue.push(next); }
    }
  }
  return seen;
}

function checkConfig(node) {
  const config = node.config ?? {};
  if (typeof config !== 'object' || Array.isArray(config)) return [node.name + '的参数格式有误'];
  const errors = [];
  for (const field of types[node.type].fields) {
    const value = config[field.key] ?? field.default;
    if (field.type === 'number') {
      if (typeof value !== 'number' || !Number.isFinite(value) || value < (field.min ?? -Infinity) ||
          value > (field.max ?? Infinity) || (field.step === 1 && !Number.isInteger(value))) {
        errors.push(node.name + '的「' + field.label + '」超出允许范围');
      }
    } else if (field.type === 'select' && !field.options.some(option => option.value === value)) {
      errors.push(node.name + '的「' + field.label + '」取值无效');
    } else if (field.type === 'text' && (typeof value !== 'string' || (!field.optional && !presentText(value)))) {
      errors.push(node.name + '需要填写「' + field.label + '」');
    }
  }
  return errors;
}

export function graphIssues(graph) {
  const issues = [];
  const add = (message, nodeId, edgeId) => issues.push({message, nodeId, edgeId});
  if (!graph || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) return [{message:'流水线缺少节点或连线列表'}];
  if (!presentText(graph.name)) add('请填写流水线名称');
  if (!graph.nodes.length) return [...issues,{message:'请先添加输入、处理和输出节点'}];
  const byId = new Map();
  for (const node of graph.nodes) {
    if (!node || !validId(node.id) || byId.has(node.id)) { add('节点标识缺失或重复'); continue; }
    byId.set(node.id,node);
    if (!presentText(node.name)) add('请为所有节点填写名称',node.id);
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) add('节点坐标格式有误',node.id);
    if ((node.type==='python' || node.implementation!=null) &&
        (!presentText(node.implementation?.code) || !['native','records'].includes(node.implementation?.kind) ||
         (node.type==='python' && node.implementation.kind!=='records'))) {
      add(node.name + '需要已保存的有效算子代码',node.id);
    }
    if (!Object.hasOwn(types,node.type)) add('无法识别算子类型「' + String(node.type) + '」',node.id);
    else for (const message of checkConfig(node)) add(message,node.id);
  }
  const inputs = [...byId.values()].filter(n => n.type === 'input');
  const outputs = [...byId.values()].filter(n => n.type === 'output');
  if (inputs.length !== 1) add('流水线需要一个输入节点');
  if (!outputs.length) add('请添加至少一个输出节点');
  const incoming = new Map(), outgoing = new Map(), routes = new Map(), ids = new Set();
  const forward = new Map(), degree = new Map([...byId.keys()].map(id => [id,0]));
  for (const id of byId.keys()) { incoming.set(id,[]); outgoing.set(id,[]); routes.set(id,new Set()); forward.set(id,[]); }
  for (const edge of graph.edges) {
    if (!edge || !validId(edge.id) || ids.has(edge.id)) { add('连线标识缺失或重复'); continue; }
    ids.add(edge.id);
    if (!byId.has(edge.from) || !byId.has(edge.to)) { add('连线包含不存在的节点',undefined,edge.id); continue; }
    const source = byId.get(edge.from), dest = byId.get(edge.to);
    const condition = edge.condition || 'next';
    if (edge.from === edge.to) add('一个节点不能连接到自身',source.id,edge.id);
    if (source.type === 'output') add('输出节点不能再连接下一步',source.id,edge.id);
    if (dest.type === 'input') add('输入节点不能接收上游连线',dest.id,edge.id);
    if (source.type === 'gate' ? !gateRoutes.includes(condition) : condition !== 'next') {
      add(source.name + '的支路条件无效',source.id,edge.id);
    }
    if (routes.get(edge.from).has(condition)) add(source.name + '的「' + (routeNames[condition] || condition) + '」支路重复',source.id,edge.id);
    routes.get(edge.from).add(condition);
    incoming.get(edge.to).push(edge.from);
    outgoing.get(edge.from).push(edge.to);
    if (!(source.type === 'gate' && condition === 'retry')) {
      forward.get(edge.from).push(edge.to);
      degree.set(edge.to,degree.get(edge.to)+1);
    }
  }
  for (const node of byId.values()) {
    const nodeRoutes = routes.get(node.id);
    if (node.type === 'gate') {
      for (const condition of gateRoutes) if (!nodeRoutes.has(condition)) add(node.name + '缺少「' + routeNames[condition] + '」支路',node.id);
    } else if (node.type !== 'output' && !nodeRoutes.has('next')) {
      add(node.name + '尚未连接下一步',node.id);
    }
  }
  if (inputs.length === 1) {
    const seen = reachable([inputs[0].id],outgoing);
    for (const node of byId.values()) if (!seen.has(node.id)) add('从输入无法到达「' + node.name + '」',node.id);
  }
  if (outputs.length) {
    const pathsToOutput = reachable(outputs.map(n => n.id),incoming);
    for (const node of byId.values()) if (!pathsToOutput.has(node.id)) add('「' + node.name + '」没有通向输出的路径',node.id);
  }
  // Removing bounded retry edges must leave a DAG. This also catches implicit loops behind a passing branch.
  const queue = [...degree.keys()].filter(id => degree.get(id) === 0);
  for (let i = 0; i < queue.length; i++) {
    for (const id of forward.get(queue[i])) {
      degree.set(id,degree.get(id)-1);
      if (!degree.get(id)) queue.push(id);
    }
  }
  if (queue.length !== byId.size) add('仅质量门控的「重试」支路可以形成循环');
  return issues;
}

export function validateGraph(graph) {
  return [...new Set(graphIssues(graph).map(issue => issue.message))];
}

export function createDemoRun(graph, {scenario = 'pass'} = {}) {
  const errors = validateGraph(graph);
  if (errors.length) throw new Error(errors.join('；'));
  if (!gateRoutes.includes(scenario)) throw new Error('未知演练场景');
  const snapshot = structuredClone(graph);
  const first = snapshot.nodes.find(n => n.type === 'input');
  const startedAt = new Date().toISOString();
  const nodeStates = Object.fromEntries(snapshot.nodes.map(n => [n.id,n.id === first.id ? 'running' : 'waiting']));
  return {
    id:'demo-' + crypto.randomUUID(), pipelineId:graph.id, version:graph.version, graph:snapshot, mode:'demo',
    status:'running', scenario, startedAt, endedAt:null, current:first.id, nodeStates, activeEdges:[],
    events:[{time:startedAt,nodeId:first.id,message:'开始本地演练，进入「' + first.name + '」'}],
    steps:0, retryCounts:{}, outcome:null
  };
}

export function advanceRun(run) {
  if (run.status !== 'running' || run.mode !== 'demo') return run;
  const now = new Date().toISOString();
  const next = {...run, nodeStates:{...run.nodeStates}, activeEdges:[...run.activeEdges],
    events:[...run.events], retryCounts:{...run.retryCounts}, steps:(run.steps || 0)+1};
  const node = run.graph.nodes.find(n => n.id === run.current);
  function finish(status, message) {
    next.status = status;
    next.endedAt = now;
    if (node) next.nodeStates[node.id] = status === 'failed' ? 'failed' : 'done';
    for (const id of Object.keys(next.nodeStates)) if (next.nodeStates[id] === 'waiting') next.nodeStates[id] = 'skipped';
    next.events.push({time:now,nodeId:node?.id ?? '',message});
    return next;
  }
  const maxRetries = run.graph.nodes.filter(n => n.type === 'gate').reduce((sum,n) => sum + Number(n.config?.maxRetries ?? 2),0);
  if (!node || next.steps > run.graph.nodes.length * (1+maxRetries) + 1) return finish('failed','演练已停止：运行记录与图结构不一致');
  if (node.type === 'output') {
    next.outcome = next.outcome || 'pass';
    return finish('completed',next.outcome === 'review' ? '演练结束，结果进入待复核支路' : '演练结束，已到达输出节点');
  }
  let condition = 'next';
  if (node.type === 'gate') {
    const retries = next.retryCounts[node.id] || 0;
    const budget = node.config?.maxRetries ?? 2;
    if (run.scenario === 'review') condition = 'review';
    else if (run.scenario === 'retry' && retries === 0) {
      condition = budget > 0 ? 'retry' : 'review';
      if (condition === 'retry') next.retryCounts[node.id] = retries+1;
    } else condition = 'pass';
    if (condition === 'review') next.outcome = 'review';
  }
  const edge = run.graph.edges.find(e => e.from === node.id && (e.condition || 'next') === condition);
  const dest = edge && run.graph.nodes.find(n => n.id === edge.to);
  if (!dest) return finish('failed','演练已停止，找不到「' + routeNames[condition] + '」的后续节点');
  next.nodeStates[node.id] = 'done';
  next.nodeStates[dest.id] = 'running';
  next.current = dest.id;
  next.activeEdge = edge.id;
  if (!next.activeEdges.includes(edge.id)) next.activeEdges.push(edge.id);
  const branch = node.type === 'gate' ? ' · ' + routeNames[condition] : '';
  next.events.push({time:now,nodeId:dest.id,edgeId:edge.id,condition,message:node.name + branch + ' → ' + dest.name});
  return next;
}
