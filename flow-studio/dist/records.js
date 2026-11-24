// Each step consumes the previous row set. Edited Python runs on the private workbench.
const supported=new Set(['input','transform','filter','output','python']);
export function canProcessRecords(graph){return graph.nodes.length>0&&graph.nodes.every(node=>
 supported.has(node.type)&&(!node.implementation||node.implementation.kind==='records'))}
export function createRecordRun(graph,raw){
 if(!canProcessRecords(graph))throw Error('此图含有需要服务器的算子');
 if(graph.nodes.some(n=>(n.type==='python'||n.implementation)&&!n.implementation?.code?.trim()))throw Error('请先在算子库中保存 Python 源码');
 if(raw.length>1000000)throw Error('本机运行每次最多接收 1 MB 的 JSONL');
 const rows=raw.split(/\r?\n/).filter(s=>s.trim()).map((s,i)=>{let item;try{item=JSON.parse(s)}catch{throw Error('第 '+(i+1)+' 行 JSON 无效')}if(!item||Array.isArray(item)||typeof item!=='object')throw Error('第 '+(i+1)+' 行需要是对象');return item});
 const time=new Date().toISOString(),first=graph.nodes.find(n=>n.type==='input');
 return {id:'data-'+crypto.randomUUID(),pipelineId:graph.id,version:graph.version,graph:structuredClone(graph),mode:'records',status:'running',startedAt:time,endedAt:null,current:first.id,nodeStates:{[first.id]:'running'},activeEdges:[],events:[{time,nodeId:first.id,message:'已读取 '+rows.length+' 条输入记录'}],records:rows}
}
async function runPython(node,records,signal){
 const code=node.implementation?.code;
 if(typeof code!=='string'||!code.trim())throw Error('该节点没有已保存的 Python 源码');
 const response=await fetch('/api/operators/run',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({code,records,config:node.config??{}}),signal});
 let result;
 try{result=await response.json()}catch{throw Error('请通过 amax 服务器工作台运行 Python 算子')}
 if(!response.ok||!Array.isArray(result?.records))throw Error(result?.error||'服务器未返回有效的记录列表');
 return result.records;
}
export function stepRecords(run,{signal}={}){if(run.status!=='running')return run;const r={...run,nodeStates:{...run.nodeStates},activeEdges:[...run.activeEdges],events:[...run.events]},n=r.graph.nodes.find(x=>x.id===r.current);
 function done(detail=''){
  const time=new Date().toISOString();
  r.nodeStates[n.id]='done';r.events.push({time,nodeId:n.id,message:'输出 '+r.records.length+' 条记录'+detail});
  if(n.type==='output'){r.status='completed';r.endedAt=time;r.current=null;return r}
  const edge=r.graph.edges.find(e=>e.from===n.id);if(!edge)throw Error('节点缺少后继连线');r.activeEdges.push(edge.id);r.current=edge.to;r.nodeStates[edge.to]='running';return r;
 }
 function failed(e){
  const time=new Date().toISOString();
  r.status='failed';r.endedAt=time;r.nodeStates[r.current]='failed';r.events.push({time,nodeId:r.current,message:e.message});return r;
 }
 try{
 if(n.type==='python'||n.implementation?.kind==='records'){
  return runPython(n,r.records,signal).then(rows=>{r.records=rows;return done()}).catch(failed);
 }
 const c=n.config??{},source=c.sourceKey??'text',target=c.targetKey??'text';let detail='';
 if(n.type==='transform'){
  if(!source||!target||['__proto__','constructor','prototype'].includes(target))throw Error('目标字段无效');
  const op=c.operation??'trim';if(!['trim','uppercase','lowercase','copy'].includes(op))throw Error('无效的转换操作');
  r.records=r.records.map(row=>{if(!Object.hasOwn(row,source))throw Error('缺少字段 '+source);let v=row[source];if(op!=='copy'){if(typeof v!=='string')throw Error('字段 '+source+' 需要文本值');if(op==='trim')v=v.trim();else if(op==='uppercase')v=v.toUpperCase();else v=v.toLowerCase()}return {...row,[target]:v}})
 }else if(n.type==='filter'){
  const key=c.key??'text',rule=c.rule??'nonempty',expect=c.value??'';if(!['nonempty','equals','contains'].includes(rule))throw Error('无效的过滤条件');const before=r.records.length;
  r.records=r.records.filter(row=>{const v=Object.hasOwn(row,key)?row[key]:null;if(v==null)return false;const s=String(v);return rule==='nonempty'?s.trim().length>0:rule==='equals'?s===String(expect):s.includes(String(expect))});detail='，过滤 '+(before-r.records.length)+' 条';
 }
 return done(detail);
 }catch(e){return failed(e)}}
