import {icon,types,seedGraphs,validateGraph,createDemoRun,advanceRun} from './model.js';
import {mountCanvas} from './canvas.js';
import {createRecordRun,stepRecords,canProcessRecords} from './records.js';
import {createLibraryStore} from './library-store.js';
import {nodeSummary,summaryFields} from './operator-summary.js';
import {openOperatorEditor} from './operator-editor.js';
import {datasetFields,mountDatasetPicker,supportsHandGraph} from './dataset-picker.js?v=clip-queue';
import {createGraphReviews} from './graph-review.js';

const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone=v=>structuredClone(v);
const uid=p=>p+'-'+(crypto.randomUUID?.()??Date.now().toString(36)+Math.random().toString(36).slice(2,8));
const stamp=()=>new Date().toISOString();
const saveDateFormat=new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
function saveTime(value,empty='暂无保存记录'){
 if(typeof value!=='string')return empty;
 const moment=new Date(value);
 return Number.isFinite(moment.getTime())
   ?`<time datetime="${moment.toISOString()}" title="浏览器本地时间">${esc(saveDateFormat.format(moment))}</time>`:empty;
}
function pipelineSaveTime(g){
 // Older built-in v1 graphs recorded the time the browser first loaded its examples.
 const original=g.version===1&&['hand-seg','record-prep'].includes(g.id);
 return saveTime(original?null:g.updatedAt);
}
function sameOperator(a,b){
 if(!a||!b||a.type!==b.type||a.name!==b.name)return false;
 if(summaryFields.some(([key])=>a.notes?.[key]!==b.notes?.[key]))return false;
 const left=a.implementation,right=b.implementation;
 if(['kind','code','baseHash'].some(key=>left?.[key]!==right?.[key]))return false;
 const defaults=Object.fromEntries((types[a.type]?.fields??[]).map(f=>[f.key,f.default]));
 const keys=new Set([...Object.keys(a.config??{}),...Object.keys(b.config??{})]);
 return [...keys].every(k=>(a.config?.[k]??defaults[k])===(b.config?.[k]??defaults[k]));
}
const clock=t=>t?new Date(t).toLocaleTimeString('zh-CN',{hour12:false}):'—';
const date=t=>t?new Date(t).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'—';
const I=(name,size=17)=>icon(name,size);
const action=(a,name,label,cls='btn',more='')=>`<button type="button" class="${cls}" data-act="${a}" title="${esc(label)}" aria-label="${esc(label)}" ${more}>${name?I(name):''}${cls.includes('iconbtn')?'':esc(label)}</button>`;
const opt=(v,l,current)=>`<option value="${esc(v)}" ${String(v)===String(current)?'selected':''}>${esc(l)}</option>`;
const field=(label,key,value,type='text',extra='')=>`<label class="label">${esc(label)}<input class="field" name="${esc(key)}" type="${type}" value="${esc(value)}" ${extra}></label>`;
const badge=(text,cls='')=>`<span class="badge ${cls}">${esc(text)}</span>`;
const motif=(t,size=17)=>`<span class="nodeicon" style="--node-color:${esc(types[t]?.color??'#426c57')}">${I(types[t]?.icon??'box',size)}</span>`;
const statuses={running:'运行中',completed:'已完成',failed:'失败',interrupted:'已中断',waiting:'等待中',done:'已完成',skipped:'已跳过',pending:'等待中',queued:'排队中'};
const modeName=r=>r.mode==='live'?'服务器任务':r.mode==='records'?'数据处理':'流程演练';
const taskName=task=>task==='objects'?'找物':'找手';
const videoTask=g=>g.nodes.find(n=>n.type==='input')?.config?.task??'hands';
const defaultVideoTask=g=>g.id==='hand-seg'?'objects':videoTask(g);
function withVideoTask(g,task){
 if(task===videoTask(g))return g;
 return {...g,nodes:g.nodes.map(n=>n.type==='input'?{...n,config:{...n.config,task}}:n)};
}
const KEY='flow-studio.personal.v1';
let saved;
try{saved=JSON.parse(localStorage.getItem(KEY)??'null')}catch{}
let graphs=Array.isArray(saved?.graphs)&&saved.graphs.length?saved.graphs:seedGraphs();
let drafts=saved?.drafts??{},custom=saved?.custom??[],runs=saved?.runs??[],selectedGraph=saved?.selectedGraph??graphs[0].id;
let page='editor',selectedNode='qwen',selectedRun=null,menu=false,canvas,editView='',live=[],bridge=false,liveError='',remoteBusy=false,livePending=null;
let selectedOp=null,bridgeLabel='本机桥接已连接',directServer=false;
let videoOutputRoot='/data/yanghonglei/DataFlow/dataflow_output';
let search='',noticeTimer, lastGraph=null,awaitedOutput=null,queues=[];
let queueOrder=[],draggedQueueItem=null,queueOrderBusy=false;
const expandedClips=new Set();
const reviews=createGraphReviews({changed:renderReview,directServer:()=>directServer});
const libraryStore = createLibraryStore({
 read:()=>({graphs,drafts,custom}),
 apply:data=>{
   if(!data.graphs.length)throw Error('工作台资料库中没有可载入的流水线');
   graphs=data.graphs;drafts=data.drafts;custom=data.custom;
   reviews.reconcile(graphs);
   readRoute();render();
 },
 report:message=>notify(message)
});
const activeGraph=()=>drafts[selectedGraph]??graphs.find(g=>g.id===selectedGraph)??graphs[0];
const isDraft=()=>Object.hasOwn(drafts,selectedGraph);
const allRuns=()=>[...live,...runs].sort((a,b)=>String(b.startedAt).localeCompare(String(a.startedAt)));
const graphRuns=()=>allRuns().filter(r=>r.pipelineId===selectedGraph);
const currentRun=()=>graphRuns().find(r=>r.id===selectedRun)??graphRuns()[0];
const baseView=g=>g.id+'-'+page;
function persist(){try{localStorage.setItem(KEY,JSON.stringify({graphs,drafts,custom,runs,selectedGraph}))}catch{notify('浏览器存储空间不足，请导出流水线备份。')}libraryStore.changed()}
function notify(t){clearTimeout(noticeTimer);$('#notice').textContent=t;noticeTimer=setTimeout(()=>$('#notice').textContent='',3500)}
function setDraft(g){
 const base=graphs.find(item=>item.id===g.id);
 if(JSON.stringify(base)===JSON.stringify(g))delete drafts[g.id];
 else drafts={...drafts,[g.id]:g};
 const draftNote=$('#review-draft');
 if(draftNote)draftNote.hidden=!drafts[selectedGraph];
 persist();
}
function change(fn){const g=clone(activeGraph());fn(g);setDraft(g);render()}
function route(p,id=selectedGraph){menu=false;if(!graphs.some(g=>g.id===id))id=graphs[0].id;const h='#/'+p+'/'+encodeURIComponent(id);if(location.hash===h){readRoute();render()}else location.hash=h}
function readRoute(){const [,p,id]=location.hash.split('/');page=['catalog','editor','monitor','history','queue'].includes(p)?p:'editor';let next;try{next=decodeURIComponent(id??'')}catch{}selectedGraph=graphs.some(g=>g.id===next)?next:graphs.some(g=>g.id===selectedGraph)?selectedGraph:graphs[0].id;if(lastGraph!==selectedGraph||page!=='editor')selectedOp=null;if(lastGraph!==selectedGraph){selectedNode=activeGraph().nodes.find(n=>n.type==='qwen')?.id??activeGraph().nodes[0]?.id;selectedRun=null;lastGraph=selectedGraph}persist()}
addEventListener('hashchange',()=>{readRoute();render()});
function shell(g,body){
 const side=(act,ic,label,active,tail='')=>`<button class="sidebtn ${active?'active':''}" data-act="${act}">${I(ic)}<span>${label}</span>${tail}</button>`;
 return `<div class="shell ${menu?'open':''}"><aside class="sidebar"><div class="brand"><span class="brandmark"><i></i></span>Flow Studio</div>
 <nav class="sidegroup"><div class="sidehead">工作空间</div>${side('catalog','layers','流水线目录',page==='catalog',`<span class="num">${graphs.length}</span>`)}${side('history','activity','运行记录',page==='history')}${directServer?side('gpu-status','cpu','GPU 占用',false):''}</nav>
 <nav class="sidegroup"><div class="sidehead">我的流水线 ${action('new','plus','新建流水线','iconbtn')}</div>${graphs.map(p=>`<button class="sidebtn ${selectedGraph===p.id&&['editor','monitor'].includes(page)?'active':''}" data-open="${esc(p.id)}"><i class="pip"></i><span class="ellipsis">${esc(drafts[p.id]?.name??p.name)}</span></button>`).join('')}</nav>
 <div class="sidebottom"><span class="serverbox">${I('stack',17)}</span><div><b>个人工作空间</b><small>${bridge?esc(bridgeLabel):'保存在当前浏览器'}</small></div></div></aside>
 <main class="workspace"><header class="bar"><div class="crumb">${action('menu','menu','打开导航','iconbtn menu-mobile')}${I('layout',15)}<span>工作空间</span>${I('chevronRight',13)}<b class="ellipsis">${page==='catalog'?'流水线目录':page==='history'?'运行记录':esc(g.name)}</b></div><div class="bar-end"><i class="status-dot"></i>${bridge?esc(bridgeLabel):'浏览器工作台'}<span class="avatar" title="个人工作空间">FS</span></div></header><div class="content">${body}</div></main></div>`;
}
function typeLabel(type,g){return type==='input'&&canProcessRecords(g)?'记录输入':types[type].label}
function builtinKind(type,g){
 return ['python','transform','filter'].includes(type)||(['input','output'].includes(type)&&canProcessRecords(g))?'records':'native';
}
function builtinKey(type,kind){return 'builtin-'+kind+'-'+type}
function isBuiltinOverride(item){return ['native','records'].some(kind=>item.id===builtinKey(item.type,kind))}
function paletteItem(key,g){
 if(Object.hasOwn(types,key)){
   const saved=custom.find(item=>item.id===builtinKey(key,builtinKind(key,g)));
   return saved?{...saved,id:key}:{id:key,type:key,name:typeLabel(key,g)};
 }
 return custom.find(item=>item.id===key&&Object.hasOwn(types,item.type));
}
function operatorSaveTime(item){
 const builtin=Object.hasOwn(types,item.id)&&!item.implementation;
 return saveTime(item.savedAt,builtin?'尚无个人保存':'暂无保存记录');
}
function nodeSaveInfo(node,graph){
 const previous=graphs.find(g=>g.id===graph.id)?.nodes.find(n=>n.id===node.id);
 const pending=Boolean(drafts[graph.id])&&!sameOperator(previous,node);
 return `本节点最近保存 · ${saveTime(previous?.savedAt)}${pending?' · 改动待随流水线保存':''}`;
}
function storeTemplate(template){
 const previous=custom.find(item=>item.id===template.id);
 if(sameOperator(previous,template))return false;
 template.savedAt=stamp();
 custom=previous?custom.map(item=>item.id===template.id?template:item):[...custom,template];
 persist();
 return true;
}
function library(g){
 const items=[...Object.keys(types).filter(key=>key!=='python').map(key=>paletteItem(key,g)),...custom.filter(item=>!isBuiltinOverride(item))];
 return `<section class="palette"><div class="palette-title"><div class="row">${I('box')}<strong>算子库</strong><span class="caption">点击查看说明，拖动图标入画布</span></div><div class="palette-tools">${action('newparam','sliders','参数模板','linkbtn')}${action('newop','plus','设计算子','linkbtn')}</div></div><div class="palette-grid">${items.map(p=>`<button type="button" draggable="true" class="opcard draggable" data-op="${esc(p.id)}" aria-controls="op-inspector" aria-pressed="${selectedOp===p.id}" title="${esc(p.name)}">${motif(p.type,14)}<span class="opcard-text"><span class="ellipsis">${esc(p.name)}</span><small class="opcard-saved">${operatorSaveTime(p)}</small></span></button>`).join('')}</div></section>`;
}
function heading(g){const count=graphRuns().length;return `<div class="heading"><div><div class="brandline"><h1>${esc(g.name)}</h1><span class="version">v${esc(g.version)}</span></div><p>${esc(g.description??'')}</p><small class="pipeline-saved">流水线最近保存 · ${pipelineSaveTime(g)}</small></div><div class="actions">${action('graphsettings','settings','流水线设置','iconbtn')}${action('save','save',isDraft()?'保存修改':'保存','btn')}${directServer&&supportsHandGraph(g)?action('datasets','folder','选择数据集','btn'):''}${directServer&&supportsHandGraph(g)?action('create-preset','clock','预设任务','btn'):''}${action('start','play','启动任务','btn primary')}</div></div><nav class="tabs"><button class="tab ${page==='editor'?'active':''}" data-act="editor">${I('workflow')}编排设计</button><button class="tab ${page==='monitor'?'active':''}" data-act="monitor">${I('activity')}运行监控${count?`<span class="num">${count}</span>`:''}</button><button type="button" class="tab" data-act="pipeline-doc">${I('layers')}说明文档</button><button type="button" class="tab ${page==='queue'?'active':''}" data-act="queue">${I('stages')}数据排队</button></nav>`;}
function board(g,monitor=false,hasSnapshot=true){return `<section class="board"><div class="boardhead"><div class="row"><span class="graphmark">${I(monitor?'activity':'workflow',16)}</span><strong>${monitor?'执行路径':'编排画布'}</strong></div><span class="caption">${monitor?(hasSnapshot?'运行快照':'历史任务 · 模板视图'):g.nodes.length+' 个节点 · '+g.edges.length+' 条连线'}</span></div><div id="canvas" class="canvas-host"></div><div class="boardfoot">${monitor?`<div class="legend"><span><i class="done"></i>已完成</span><span><i class="run"></i>运行中</span><span><i class="wait"></i>待执行</span></div>`:`<span>${I('link',13)}点击输出端口，再点击目标输入端口以连线</span><span>${I(isDraft()?'edit':'check',13)}${isDraft()?'草稿待保存':'版本已保存'}</span>`}</div></section>`;}
function runGraph(r, fallback){
 if(!r)return fallback;
 if(r.graph)return r.graph;
 const template=withVideoTask(seedGraphs()[0],r.task??'hands');
 if(!Array.isArray(r.enabledTypes)||!r.enabledTypes.length)return template;
 const enabled=new Set(r.enabledTypes);
 if(!enabled.has('fusion'))template.edges=template.edges.filter(e=>e.from!=='fusion').map(e=>
   e.to==='fusion'?{...e,to:'output'}:e);
 template.nodes=template.nodes.filter(n=>enabled.has(n.type));
 template.edges=template.edges.filter(e=>enabled.has(e.from)&&enabled.has(e.to));
 if(!enabled.has('gate')&&enabled.has('output')){
   const last=enabled.has('score')?'score':enabled.has('segment')?'segment':null;
   if(last)template.edges.push({id:last+'-output',from:last,to:'output',condition:'next',label:''});
 }
 return template;
}
function executionDetails(r,snap){
 if(!r)return '';
 const lines=[],branch=r.events?.filter(e=>['retry','pass','review'].includes(e.condition)).at(-1);
 if(r.task)lines.push(['目标任务',taskName(r.task)]);
 if(r.output)lines.push(['输出目录',r.output]);
 if(r.fusion)lines.push(['全视频掩码',r.fusion.path],['实例轨迹',String(r.fusion.tracks)],
   ['待复核接缝',String(r.fusion.reviewSeams.length)]);
 const selected=r.dataset?.selection;
 if(selected?.id){
   lines.push(['来源目录',selected.sourceRoot||'yanghonglei'],['输入序列',selected.id],['输入范围',selected.mode==='whole'?'整条序列 · 分段处理':selected.mode==='clip'?'从 '+(selected.start??0)+' 秒起最多 81 帧':'完整视频']);
   if(selected.candidate)lines.push(['输入视频',selected.candidate]);
 }
 if(Number.isInteger(r.round))lines.push(['当前轮次',String(r.round+1)]);
 if(branch)lines.push(['最近分支',({pass:'通过',retry:'重试',review:'待复核'})[branch.condition]]);
 if(r.currentActivity?.sequence)lines.push(['处理序列',r.currentActivity.sequence]);
 if(r.currentActivity?.detail)lines.push(['当前活动',r.currentActivity.detail]);
 if(!branch&&r.mode==='live'){const labels=snap.edges.filter(e=>r.activeEdges?.includes(e.id)&&['retry','pass','review'].includes(e.condition)).map(e=>e.label);if(labels.length)lines.push(['已观测分支',[...new Set(labels)].join('、')])}
 return lines.map(([label,value])=>`<div class="inspect-line"><span>${esc(label)}</span><b>${esc(value)}</b></div>`).join('');
}
function fieldControl(f,n){const val=n.config?.[f.key]??f.default??'';if(f.type==='select')return `<label class="label">${esc(f.label)}<select class="field" data-config="${esc(f.key)}">${(f.options??[]).map(o=>opt(typeof o==='object'?o.value:o,typeof o==='object'?o.label:o,val)).join('')}</select></label>`;
 return `<label class="label">${esc(f.label)}<input class="field" data-config="${esc(f.key)}" type="${f.type==='number'?'number':'text'}" value="${esc(val)}" ${f.type==='number'?'step="any"':''}></label>`;}
function summaryRows(n,g){
 const summary=nodeSummary(n,g);
 return summaryFields.map(([key,label])=>`<dt>${label}</dt><dd>${esc(summary[key])}</dd>`).join('');
}
function editOperatorNotes(){
 const g=activeGraph(), libraryKey=selectedOp;
 const spec=libraryKey?paletteItem(libraryKey,g):g.nodes.find(n=>n.id===selectedNode);
 if(!spec)return;
 const defaults=nodeSummary(spec,g,false), shown=nodeSummary(spec,g);
 const help=libraryKey?'保存到算子库。之后拖入画布的节点会带上这份说明。':'保存到当前节点的草稿，再点流水线的「保存」发布这个版本。';
 const body='<p class="help">'+help+'</p>'+summaryFields.map(([key,label])=>
   `<label class="label">${label}<textarea class="field" name="${key}" rows="${key==='model'?2:3}" maxlength="2000" required>${esc(shown[key])}</textarea></label>`).join('');
 modal('编辑算子说明 · '+spec.name,body,'保存算子说明',form=>{
   const notes={};
   for(const [key,label] of summaryFields){
     const value=form.get(key).trim();
     if(!value)throw Error('请填写'+label+'。');
     if(value!==defaults[key])notes[key]=value;
   }
   const updated={...spec};delete updated.notes;
   if(Object.keys(notes).length)updated.notes=notes;
   if(sameOperator(spec,updated)){notify('算子说明已是当前版本');return;}
   if(libraryKey){
     if(Object.hasOwn(types,libraryKey))updated.id=builtinKey(spec.type,builtinKind(spec.type,g));
     storeTemplate(updated);render();notify('算子说明已保存到库中');
   }else{
     change(graph=>{const node=graph.nodes.find(n=>n.id===spec.id);delete node.notes;if(updated.notes)node.notes=updated.notes;});
     notify('节点说明已存入草稿，请保存流水线');
   }
 });
}
function inspector(g){
 const preview=selectedOp&&paletteItem(selectedOp,g);
 const n=preview||g.nodes.find(item=>item.id===selectedNode),d=n&&types[n.type];
 let inner='<p>点击算子库查看说明，或选择画布节点编辑参数。</p>';
 if(d){
 inner=`<div class="typehead">${motif(n.type,19)}<div><strong>${esc(preview?n.name:typeLabel(n.type,g))}</strong><small>${esc(preview&&n.id!==n.type?'模板 · '+typeLabel(n.type,g):n.type)}</small></div></div>
 <small class="operator-saved">${preview?'算子库最近保存 · '+operatorSaveTime(n):nodeSaveInfo(n,g)}</small>
 <dl class="op-summary">${summaryRows(n,g)}</dl>`;
 inner+=`<div class="code-inspect-actions">${action('editnotes','edit','编辑算子说明','btn')}${n.type==='fusion'?'':action(preview?'editop':'editnodecode','code',preview?'编辑算子代码':'编辑本节点代码','btn')}${preview&&n.id!==n.type&&d.fields?.length?action('editparam','sliders','修改参数模板','linkbtn'):''}</div>`;
 if(!preview){
 const ins=g.edges.filter(e=>e.to===n.id),outs=g.edges.filter(e=>e.from===n.id);const title=id=>g.nodes.find(x=>x.id===id)?.name??id;
 inner+=`<hr><label class="label">节点名称<input id="node-name" class="field" maxlength="80" value="${esc(n.name)}"></label>
 ${d.fields?.length?`<h3>算子参数</h3>${d.fields.map(f=>fieldControl(f,n)).join('')}`:''}<hr><h3>连接关系</h3>${ins.map(e=>`<div class="portline">${I('link',13)}<b>输入</b>${esc(title(e.from))}</div>`).join('')}${outs.map(e=>`<button class="portline" data-edge-edit="${esc(e.id)}" title="编辑连线条件">${I('branch',13)}<b>${esc(e.label||'输出')}</b>${esc(title(e.to))} ${I('edit',12)}</button>`).join('')}${!ins.length&&!outs.length?'<span class="caption">尚未连接</span>':''}<div class="ins-actions">${action('template','copy','存为算子模板','linkbtn')}${action('delete-node','trash','删除节点','iconbtn danger')}</div>`;
 }
 }
 return `<aside class="inspector" id="op-inspector" aria-labelledby="op-inspector-title"><div class="boxhead"><div class="row">${I(preview?'box':'sliders',16)}<strong id="op-inspector-title">${preview?'算子说明':'节点属性'}</strong></div></div><div class="inspect-body">${inner}</div></aside>`;
}
function previewOperator(button){
 finishEditingNode();
 const changed=selectedOp!==button.dataset.op;
 selectedOp=button.dataset.op;
 for(const card of document.querySelectorAll('[data-op]'))card.setAttribute('aria-pressed',String(card===button));
 document.querySelector('.fc-selected')?.classList.remove('fc-selected');
 $('#op-inspector').outerHTML=inspector(activeGraph());
 if(changed)$('#op-inspector')?.classList.add('inspect-enter');
 const header=$('#op-inspector .boxhead');
 const bounds=header.getBoundingClientRect();
 if(bounds.top<0||bounds.bottom>window.innerHeight)header.scrollIntoView({block:'start'});
}
function finishEditingNode(){
 const field=document.activeElement;
 if(field?.matches('#node-name,[data-config]'))field.blur();
}
function saveNodeField(el){
 const g=clone(activeGraph()),n=g.nodes.find(item=>item.id===selectedNode);
 if(!n)return;
 if(el.id==='node-name'){
   const name=el.value.trim();
   el.value=name||n.name;
   if(!name||name===n.name)return;
   n.name=name;
 }else{
   const key=el.dataset.config;
   const value=el.type==='number'?Number(el.value):el.value;
   if(el.type==='number'&&(!el.value||!Number.isFinite(value))){
     el.value=n.config?.[key]??types[n.type].fields.find(f=>f.key===key)?.default??'';
     return;
   }
   const prior=n.config?.[key]??types[n.type].fields.find(f=>f.key===key)?.default;
   if(value===prior)return;
   n.config={...n.config,[key]:value};
 }
 // Blur can precede a click or drag on another control. Keep its DOM and focus intact.
 setDraft(g);
 canvas.updateNodeDetails(n);
 $('#op-inspector .op-summary').innerHTML=summaryRows(n,g);
 $('#op-inspector .operator-saved').innerHTML=nodeSaveInfo(n,g);
 const pending=isDraft(),saveButton=$('[data-act="save"]');
 saveButton.lastChild.textContent=saveButton.title=pending?'保存修改':'保存';
 saveButton.setAttribute('aria-label',saveButton.title);
 $('.boardfoot span:last-child').innerHTML=I(pending?'edit':'check',13)+(pending?'草稿待保存':'版本已保存');
}
function catalogue(){const list=graphs.filter(g=>(drafts[g.id]?.name??g.name).toLowerCase().includes(search.toLowerCase()));return `<div class="heading pagehead"><div><h1>流水线目录</h1><p>管理已保存的编排和当前草稿。</p></div><div class="actions">${action('import','upload','导入')}${action('new','plus','新建流水线','btn primary')}</div></div><section class="listpanel"><div class="filterbar"><div class="searchfield">${I('search')}<input id="search" aria-label="搜索流水线" placeholder="搜索流水线" value="${esc(search)}"></div><span class="caption">共 ${graphs.length} 条流水线</span></div>${list.map(p=>{const g=drafts[p.id]??p;return `<button class="catalogrow" data-open="${esc(p.id)}">${motif(g.nodes.some(n=>n.type==='segment')?'segment':'transform',20)}<span class="row-main"><strong>${esc(g.name)}</strong><small>${esc(g.description??'')}</small><small class="pipeline-saved">最近保存 · ${pipelineSaveTime(p)}</small></span><span class="caption">${g.nodes.length} 个节点</span><span class="version">v${esc(p.version)}</span>${I('chevronRight',15)}</button>`}).join('')||'<div class="empty">没有匹配的流水线</div>'}</section>`;}
function history(){return `<div class="heading pagehead"><div><h1>运行记录</h1><p>按启动时间查看任务和对应的执行路径。</p></div>${action('refresh','history','刷新')}</div><section class="listpanel"><div class="boxhead"><strong>全部任务</strong><span class="caption">${allRuns().length} 条记录</span></div>${allRuns().map(r=>`<button class="runrow" data-run="${esc(r.id)}">${I('activity')}<span class="row-main"><strong>${esc(r.graph?.name??graphs.find(p=>p.id===r.pipelineId)?.name??r.pipelineId)}</strong><small>${esc(modeName(r))} · ${esc(date(r.startedAt))}</small></span>${statusBadge(r.status)}${I('chevronRight',15)}</button>`).join('')||'<div class="empty">还没有任务记录。打开流水线后即可启动。</div>'}${liveError?`<div class="caption" style="padding:10px">${esc(liveError)}</div>`:''}</section>`;}
const statusBadge=s=>badge(statuses[s]??s,s==='completed'?'ok':s==='running'?'orange':['failed','interrupted'].includes(s)?'bad':'');
function queuePanel(graph){
 const list=queues.filter(q=>q.pipelineId===graph.id);
 if(!list.length)return '';
 const names={...statuses,launching:'正在准备视频并启动',paused:'已暂停'};
 return '<section class="queue-monitor"><h2>服务器任务队列</h2><p class="caption">按提交顺序处理，同一时间运行一条。等待中的任务会在前一条结束后自动启动。</p>'+list.map(q=>{
   const done=q.items.filter(item=>item.status==='completed').length;
   const lines=q.items.map(item=>{
     const run=live.find(r=>(r.output??r.out)===item.out);
     const label=esc(item.sequenceName??(item.selection ? ((item.selection.sourceRoot||'yanghonglei')+' / '+item.selection.id) : item.input));
     return `<li><span>${run?`<button class="linkbtn" data-run="${esc(run.id)}">${label}</button>`:label}</span><b>${esc(names[item.status]??item.status)}</b>${item.error?`<small>${esc(item.error)}</small>`:''}</li>`;
   }).join('');
   return `<details${['running','queued','paused'].includes(q.status)?' open':''}><summary>${taskName(q.task)} · ${esc(names[q.status]??q.status)} · ${done}/${q.items.length} 条已完成 · ${esc(date(q.createdAt))}</summary><ol>${lines}</ol></details>`;
 }).join('')+'</section>';
}
function queuePage(){
 const items=new Map([...queues].reverse().flatMap(q=>q.items.map(item=>[item.out,{...item,task:q.task,pipelineId:q.pipelineId}])));
 const waiting=queueOrder.map(out=>items.get(out)).filter(Boolean);
 const running=[...items.values()].filter(item=>['launching','running'].includes(item.status));
 const recent=live.filter(run=>run.clips?.length&&['completed','failed','interrupted'].includes(run.status)).slice(0,6)
   .map(run=>({...items.get(run.output),out:run.output,sequenceName:run.name,task:run.task,
     pipelineId:run.pipelineId,status:run.status}));
 for(const run of live){
   if(run.status==='running'&&!running.some(item=>item.out===run.output)){
     running.push({out:run.output,sequenceName:run.name,task:run.task,pipelineId:run.pipelineId,status:'running'});
   }
 }
 function clipsFor(item,run){
   if(run?.clips?.length)return run.clips;
   if(!item.clipPlan)return [];
   const {startFrame,frameCount,parts,overlap=0}=item.clipPlan;
   const stride=81-overlap;
   return Array.from({length:parts},(_,i)=>({index:i+1,firstFrame:startFrame+stride*i,
     frames:Math.min(81,frameCount-stride*i),status:'waiting',steps:[]}));
 }
 function row(item,index){
   const run=live.find(r=>r.output===item.out);
   const name=item.sequenceName??item.selection?.id??item.input??run?.name??item.out;
   const flow=graphs.find(g=>g.id===item.pipelineId)?.name;
   const status=item.status==='launching'?'正在准备视频':statuses[item.status];
   const moves=index===undefined?'':`<button type="button" class="linkbtn" data-queue-move="-1" aria-label="上移 ${esc(name)}" ${index===0||queueOrderBusy?'disabled':''}>上移</button><button type="button" class="linkbtn" data-queue-move="1" aria-label="下移 ${esc(name)}" ${index===waiting.length-1||queueOrderBusy?'disabled':''}>下移</button>`;
   const clips=clipsFor(item,run);
   const review=run?.fusion?.reviewSeams??[];
   const clipDetails=clips.length?`<details class="queue-clips" data-clip-output="${esc(item.out)}" ${expandedClips.has(item.out)?'open':''}>
     <summary>${clips.length} 段 · ${clips.filter(clip=>clip.status==='done').length} 段已完成${review.length?' · '+review.length+' 处接缝待复核':''}</summary>
     <ol>${clips.map(clip=>{
       const step=clip.steps.findLast(step=>step.status==='running'||step.status==='failed')??clip.steps.findLast(step=>step.status!=='waiting');
       const info=step?` · ${esc(step.stage)}${step.round>1?' 第 '+step.round+' 轮':''}${step.detail?' · '+esc(step.detail):''}`:'';
       const history=clip.steps.filter(item=>item.status!=='waiting');
       return `<li><div class="queue-clip-line"><b>第 ${clip.index} 段</b><span>帧 ${clip.firstFrame}–${clip.firstFrame+clip.frames-1}（${clip.frames} 帧）</span><small>${esc(statuses[clip.status]??(clip.status==='paused'?'已暂停':'待执行'))}${info}</small></div>
         ${history.length>1?`<div class="queue-clip-history">${history.map(item=>esc(item.stage)+'（'+esc(statuses[item.status]??item.status)+'）').join(' · ')}</div>`:''}</li>`;
     }).join('')}</ol>${review.length?`<strong>待复核接缝</strong><ul>${review.map(seam=>
       `<li>第 ${seam.left} → ${seam.right} 段 · 帧 ${seam.firstFrame}–${seam.firstFrame+seam.frames-1} · ${esc(seam.reason)}</li>`).join('')}</ul>`:''}</details>`:'';
   return `<li class="queue-row" ${index===undefined?'':`draggable="${!queueOrderBusy}" data-queue-item="${esc(item.out)}"`}>
     <div><strong>${esc(name)}</strong><small>${esc(taskName(item.task))}${item.continuation?' · 继续 ABS 评分':''}${flow?' · '+esc(flow):''}</small></div>
     <span>${esc(status??'')}</span><div class="queue-row-actions">${moves}${run?`<button class="linkbtn" data-run="${esc(run.id)}">查看运行</button>`:''}</div>${clipDetails}</li>`;
 }
 return `<section class="queue-monitor queue-page"><div class="row"><h2>服务器执行顺序</h2>${action('refresh','history','刷新','linkbtn')}</div>
   ${liveError?`<p role="status">${esc(liveError)}</p>`:''}<h3>执行中 ${running.length?'· '+running.length+' 条':''}</h3>
   <ul>${running.map(item=>row(item)).join('')||'<li class="caption">当前没有运行中的序列。</li>'}</ul>
   <h3>待处理 · ${waiting.length} 条</h3>
   <p class="caption">可拖动待处理序列，或用上移、下移调整。正在执行或准备视频的序列已锁定。</p>
   <ol>${waiting.map(row).join('')||'<li class="caption">暂无待处理序列。</li>'}</ol>
   ${recent.length?'<h3>最近处理记录</h3><ul>'+recent.map(item=>row(item)).join('')+'</ul>':''}</section>`;
}
async function changeQueueOrder(from,to){
 if(queueOrderBusy||from===to)return;
 const expected=[...queueOrder], order=[...expected], start=order.indexOf(from), end=order.indexOf(to);
 if(start<0||end<0)return;
 order.splice(start,1);order.splice(end,0,from);
 queueOrderBusy=true;render();
 try{
   const res=await fetch('/api/queues/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected,order})});
   const result=await res.json();
   if(Array.isArray(result.queueOrder)){queues=result.queues;queueOrder=result.queueOrder;}
   if(!res.ok)throw Error(result.error||'调整顺序失败。');
   notify('已更新待处理序列的顺序');
 }catch(error){notify(error.message);}
 finally{queueOrderBusy=false;if(page==='queue')render();}
}
function resumeAction(run){
 if(!run?.canResume)return '';
 if(queues.some(batch=>batch.items.some(item=>item.out===run.output&&['queued','launching','running'].includes(item.status)))){
   return '<p class="caption">续跑已进入服务器队列。</p>';
 }
 return `<div class="resume-action">${action('resume-run','play','继续执行','btn primary')}<small>从 ABS 检查点继续，复用本次已保存的分割结果。</small></div>`;
}
function confirmResume(){
 const run=currentRun();
 if(!run?.canResume)return;
 const requestId=crypto.randomUUID();
 modal('继续执行当前任务',`<p>${esc(run.name)}</p><p class="help">复用本次已完成的提词和分割，并检查已有评分记录。连接恢复后补做尚未收到回复的请求。</p>
 <p class="help">确认后加入服务器队列，按顺序继续执行。</p>`,'确认继续',async()=>{
   const res=await fetch('/api/runs/resume',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({id:run.id,requestId}),signal:AbortSignal.timeout(30000)});
   const data=await res.json();
   if(!res.ok)throw Error(data.error||'续跑请求未获确认。');
   queues=[data.queue,...queues.filter(q=>q.id!==data.queue.id)];
   render();pollLive();notify('已加入服务器续跑队列');
 });
}
function monitor(g){const rr=graphRuns(),r=currentRun();const snap=runGraph(r,g);const current=snap.nodes.find(n=>n.id===r?.current);const chosen=snap.nodes.find(n=>n.id===selectedNode);
 const info=`<aside class="inspector"><div class="boxhead"><strong>任务详情</strong>${r?statusBadge(r.status):badge('待启动')}</div><div class="inspect-body inspect-list">${r?`<div class="inspect-line"><span>执行方式</span><strong>${modeName(r)}</strong></div><div class="inspect-line"><span>启动时间</span><b>${esc(date(r.startedAt))}</b></div><div class="inspect-line"><span>当前节点</span><b>${esc(r.status==='running'?(current?.name??'等待事件'):'—')}</b></div><div class="inspect-line"><span>结束时间</span><b>${esc(date(r.endedAt))}</b></div>${executionDetails(r,snap)}${resumeAction(r)}${chosen?`<hr><div class="typehead">${motif(chosen.type)}<div><strong>${esc(chosen.name)}</strong><small>${esc(statuses[r.nodeStates?.[chosen.id]]??'无事件')}</small></div></div>`:''}${r.mode==='records'&&r.status==='completed'?action('result','download','导出处理结果','btn'):''}`: '<p>启动后可在此查看节点状态、运行方式和起止时间。</p>'}</div></aside>`;
 return `${queuePanel(g)}<div class="monitorbar"><select class="field" id="run-select" aria-label="选择运行记录" ${!r?'disabled':''}>${rr.map(x=>opt(x.id,modeName(x)+' · '+date(x.startedAt)+' · '+(statuses[x.status]??x.status)+(x.name?' · '+x.name:''),r?.id)).join('')||'<option>尚无运行记录</option>'}</select>${action('refresh','history','刷新','linkbtn')}</div>${livePending?`<p class="caption">${esc(livePending)}</p>`:''}${liveError?`<p class="caption">${esc(liveError)}</p>`:''}<div class="editor-grid"><div>${board(snap,true,!r||Boolean(r.graph))}<section class="events"><div class="boxhead"><strong>执行事件</strong><span class="caption">${r?.events?.length??0} 条</span></div><div class="eventlog">${(r?.events??[]).slice().reverse().map(e=>`<div class="event"><span class="time">${esc(clock(e.time))}</span><b>${esc(snap.nodes.find(n=>n.id===e.nodeId)?.name??'任务')}</b><span>${esc(e.message)}</span></div>`).join('')||'<div class="empty">等待首条执行事件</div>'}</div></section></div>${info}</div>`;}
function renderReview(){
 const panel=$('#graph-review');
 if(!panel)return;
 const savedGraph=graphs.find(item=>item.id===selectedGraph);
 if(!savedGraph)return;
 const job=reviews.get(savedGraph);
 const status=job?.status??'idle',active=['queued','running'].includes(status);
 const findings=job?.findings??[];
 const errors=findings.filter(item=>item.level==='error').length;
 const warnings=findings.length-errors;
 const summary=[errors&&`${errors} 项问题`,warnings&&`${warnings} 项提示`].filter(Boolean).join('，');
 const title={idle:'尚未审查',queued:'即将自动审查',running:'审查中',cancelled:'本次审查已取消',
   done:summary||'静态审查通过',partial:summary?'审查未完成 · '+summary:'审查未完成'}[status];
 const modes={demo:'流程演练',records:'JSONL 数据处理',live:'服务器视频分割',graph:'通用编排'};
 let note='';
 if(status==='queued')note='保存成功，3 秒后开始。';
 if(status==='running')note=job.progress;
 if(['done','partial'].includes(status)){
   const available=job.modes?.filter(mode=>!findings.some(f=>f.level==='error'&&f.scope===mode));
   const structure=available?'已检查节点、连线、分支和运行模式。':'';
   const compatible=available?.length?'编排兼容'+available.map(mode=>modes[mode]).join('、')+'。':'';
   note=structure+compatible+job.coverage;
 }
 const items=['done','partial'].includes(status)?findings.map(item=>{
   const node=savedGraph.nodes?.find(n=>n.id===item.nodeId);
   const edge=savedGraph.edges?.find(e=>e.id===item.edgeId);
   const name=id=>savedGraph.nodes?.find(n=>n.id===id)?.name??id;
   const where=edge?`${name(edge.from)} → ${name(edge.to)}`:node?.name;
   return `<li><b>${item.level==='error'?'问题':'提示'} · ${esc(modes[item.scope]??item.scope)}</b> ${where?`<strong>${esc(where)}</strong> · `:''}${esc(item.message)}</li>`;
 }).join(''):'';
 panel.innerHTML=`<div class="review-row"><div class="review-heading">${I('workflow',17)}<strong>编排审查</strong><span class="review-version">已保存 v${esc(savedGraph.version)}</span><span role="status" class="review-status" data-state="${status}" data-errors="${Boolean(errors)}">${esc(title)}</span><small id="review-draft" ${isDraft()?'':'hidden'}>当前草稿有改动</small></div>
 <div class="review-actions"><label><input type="checkbox" id="auto-review" ${reviews.enabled?'checked':''}>保存后自动审查</label>${active?action('review-cancel',null,'取消本次审查','btn'):action('review-now',null,'审查已保存版本','linkbtn')}</div></div>
 ${note?`<p>${esc(note)}</p>`:''}${items?`<ul class="review-findings">${items}</ul>`:''}`;
}
function render(){
 canvas?.destroy();canvas=null;
 const g=activeGraph(),app=$('#app'),view=baseView(g);
 const body=page==='catalog'?catalogue():page==='history'?history():page==='queue'?heading(g)+queuePage():heading(g)+'<section id="graph-review" aria-label="编排审查"></section>'+(page==='monitor'?monitor(g):`<div class="editor-grid"><div>${board(g)}${library(g)}</div>${inspector(g)}</div>`);
 app.innerHTML=shell(g,body);
 // Polling and edits redraw this DOM too. Only navigation should animate the page.
 if(app.dataset.view!==view){
   app.dataset.view=view;
   app.querySelector('.content').classList.add('view-enter');
 }
 renderReview();if(!$('#canvas'))return;
 const r=page==='monitor'?currentRun():null;const snap=runGraph(r,g);
 canvas=mountCanvas($('#canvas'),{graph:snap,viewKey:baseView(snap),selected:selectedOp?null:selectedNode,mode:page==='monitor'?'monitor':'edit',running:r?.status==='running',states:r?.nodeStates??{},activeEdges:r?.activeEdges??[],currentEdge:r?.status==='running'?r.activeEdge:null,onEditEdge:id=>{const edge=g.edges.find(e=>e.id===id);if(edge)wire(edge.from,edge.to,edge.id)},onSelect:id=>{finishEditingNode();const changed=selectedOp!==null||selectedNode!==id;selectedOp=null;selectedNode=id;render();if(changed)$('#op-inspector')?.classList.add('inspect-enter')},onMove:(id,x,y)=>change(q=>{const n=q.nodes.find(n=>n.id===id);if(n)Object.assign(n,{x,y})}),onAdd:addNode,onConnect:(from,to)=>wire(from,to),onDeleteEdge:id=>change(q=>q.edges=q.edges.filter(e=>e.id!==id))});}
function addNode(key,x,y){
 const spec=paletteItem(key,activeGraph());
 if(!spec||!Number.isFinite(x)||!Number.isFinite(y))return;
 if(spec.type==='python'&&!spec.implementation){notify('请先在算子库中设计并保存 Python 算子。');return;}
 finishEditingNode();
 const {name,type}=spec,id=uid(type);
 const defaults=Object.fromEntries(types[type].fields.map(f=>[f.key,f.default]));
 selectedNode=id;selectedOp=null;
 change(g=>g.nodes.push({id,type,name,x,y,config:clone({...defaults,...spec.config}),...(spec.implementation?{implementation:clone(spec.implementation)}:{}),...(spec.notes?{notes:clone(spec.notes)}:{})}));
}
function nextSavedVersion(graph){
 const g=clone(graph);
 if(!drafts[g.id])return g;
 const previous=graphs.find(item=>item.id===g.id);
 g.version=(previous?.version??0)+1;
 g.updatedAt=stamp();
 const byId=new Map(previous?.nodes.map(n=>[n.id,n]));
 for(const node of g.nodes){
   if(!sameOperator(byId.get(node.id),node))node.savedAt=g.updatedAt;
 }
 return g;
}
function save(prepared){
 if(!prepared)finishEditingNode();
 const g=prepared??nextSavedVersion(activeGraph());
 if(!drafts[g.id]){
   if(!prepared)reviews.start(graphs.find(item=>item.id===g.id),true);
   notify('当前版本已保存');return;
 }
 graphs=graphs.map(p=>p.id===g.id?g:p);
 delete drafts[g.id];
 reviews.reconcile(graphs);
 persist();render();notify('已保存 v'+g.version);
 if(!prepared)reviews.start(g,true);
}
function modal(title,body,ok,callback){const dlg=$('#dialog');if(dlg.open)dlg.close();dlg.innerHTML=`<form><div class="dialoghead"><h2>${esc(title)}</h2>${action('dismiss','close','关闭','iconbtn')}</div><div class="dialog-body">${body}<div class="formerror" role="alert"></div></div><div class="dialogfoot"><button type="button" data-act="dismiss" class="btn">取消</button><button class="btn primary" type="submit">${esc(ok)}</button></div></form>`;dlg.showModal();dlg.querySelector('form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,btn=e.submitter??form.querySelector('[type="submit"]');btn.disabled=true;try{await callback(new FormData(e.target));if(dlg.open&&form.isConnected)dlg.close()}catch(error){if(form.isConnected)form.querySelector('.formerror').textContent=error.message}finally{btn.disabled=btn.getAttribute('aria-disabled')==='true'}}}
function newFlow(){
 const baseSelect='<label class="label">起始模板<select class="field" name="base"><option value="blank">空白编排</option><option value="segment">手部分割 · 基础版（无需 ABS）</option><option value="score">手部分割 · 附 ABS 评分</option><option value="full">手部分割 · 评分与重试</option><option value="records">文本清单整理</option></select></label>';
 modal('新建流水线',field('流水线名称','name','','text','required maxlength="80" placeholder="例如：手部掩码复核"')+baseSelect+'<label class="label">说明<textarea class="field" name="desc" placeholder="描述这条流水线处理的任务"></textarea></label>','创建',f=>{
   const name=f.get('name').trim(),base=f.get('base');
   if(!name)throw Error('请输入名称');
   const [hands,records]=seedGraphs();
   let g;
   if(base==='blank'){
     g={nodes:[{id:'input',type:'input',name:'数据输入',x:30,y:55,config:{}},{id:'output',type:'output',name:'结果输出',x:385,y:55,config:{}}],edges:[{id:'input-output',from:'input',to:'output',condition:'next',label:''}]};
   }else if(base==='records'){g=records}
   else{
     g=hands;
     if(base!=='full'){
       const order=base==='score'?['input','qwen','segment','score','output']:['input','qwen','segment','output'];
       g.nodes=order.map((id,i)=>({...g.nodes.find(n=>n.id===id),x:i*212,y:70}));
       g.edges=order.slice(1).map((to,i)=>({id:order[i]+'-'+to,from:order[i],to,condition:'next',label:''}));
       g.description=base==='score'?'生成手部实例掩码并进行 ABS 评分。':'从视频提取手部目标短语，再生成逐实例掩码。';
     }
   }
   Object.assign(g,{id:uid('flow'),name,description:f.get('desc').trim()||g.description||'',version:1,updatedAt:stamp()});
   graphs.push(g);persist();route('editor',g.id);
 });
}
function settings(){const g=activeGraph();modal('流水线设置',field('名称','name',g.name,'text','required maxlength="80"')+`<label class="label">说明<textarea name="desc" class="field">${esc(g.description)}</textarea></label><div class="smallrow">${action('export','download','导出 JSON','linkbtn')}${action('duplicate','copy','复制流水线','linkbtn')}</div>`,'应用修改',f=>{const name=f.get('name').trim();if(!name)throw Error('请输入名称');change(q=>{q.name=name;q.description=f.get('desc').trim()})})}
function editCode(scope){
 if(!directServer){
   modal('打开服务器代码编辑器','<p>双击 Windows 项目里的「10_服务器流水线工作台.cmd」，在 amax 工作台的算子库中编辑 Python 或调用 Agent。</p>','知道了',()=>{});
   return;
 }
 const g=activeGraph();
 const spec=scope==='new'?{name:'',type:'python',config:{}}:scope==='node'?g.nodes.find(n=>n.id===selectedNode):paletteItem(selectedOp,g);
 if(!spec)return;
 if(scope==='library')scope=Object.hasOwn(types,spec.id)?'builtin':'template';
 const kind=spec.implementation?.kind??builtinKind(spec.type,g);
 const id=scope==='template'||scope==='node'?spec.id:uid('template');
 openOperatorEditor({modal,spec,kind,scope,modelUsage:nodeSummary(spec,g).model,onSave(name,implementation,saveMode,modelUsage){
   const notes={...spec.notes,model:modelUsage};
   if(scope==='node'){
     if(activeGraph().id!==g.id||!activeGraph().nodes.some(n=>n.id===id))throw Error('当前图上的节点已变化，请重新打开编辑器。');
     const current=activeGraph().nodes.find(n=>n.id===id);
     if(sameOperator(current,{...current,name,implementation,notes})){notify('节点代码与已打开的版本一致');return;}
     change(graph=>Object.assign(graph.nodes.find(n=>n.id===id),{name,implementation,notes}));
     notify('已更新当前节点的代码草稿');
   }else{
     const replaceBuiltin=scope==='builtin'&&saveMode==='update';
     const target=replaceBuiltin?builtinKey(spec.type,kind):id;
     const template={id:target,type:spec.type,name,config:clone(spec.config??{}),implementation,notes};
     const changed=storeTemplate(template);
     selectedOp=replaceBuiltin?spec.type:target;render();notify(changed?'算子代码已保存到库中':'算子内容已是当前版本');
   }
 }});
}
function designOp(existing,templateId='') {
 const original = existing ? activeGraph().nodes.find(x => x.id === selectedNode) : null;
 let source = original??custom.find(p=>p.id===templateId);
 const templates=custom.filter(item=>!isBuiltinOverride(item));
 const chooser = !existing && templates.length ? `<label class="label">模板<select class="field" id="op-existing" name="template"><option value="">创建新模板</option>${templates.map(p=>opt(p.id,p.name,templateId)).join('')}</select></label>` : '';
 const body = chooser + field('模板名称','name',source?.name??'','text','required maxlength="80" placeholder="例如：去除文本空白"')
   + `<label class="label">基础算子<select id="op-base" name="type" class="field">${Object.entries(types).map(([k,d])=>opt(k,d.label,source?.type??'transform')).join('')}</select></label><div id="op-fields"></div><p class="help">模板决定新加入画布的节点默认值，每个实例可以单独修改。</p>`;
 modal('设计算子模板',body,'保存到算子库',f=>{
   const type=$('#op-base').value, name=f.get('name').trim();
   if(!name)throw Error('请输入模板名称');
   const config={};
   for(const x of types[type].fields??[]) {
     const raw=f.get('cf-'+x.key);
     config[x.key]=x.type==='number'?Number(raw):raw;
   }
   const id=f.get('template')||uid('template');
   const template={id,name,type,config,...(source?.implementation?{implementation:clone(source.implementation)}:{}),...(source?.type===type&&source.notes?{notes:clone(source.notes)}:{})};
   const changed=storeTemplate(template);
   render();notify(changed?'算子模板已保存':'算子内容已是当前版本');
 });
 function fields(){
   $('#op-base').disabled=Boolean(source?.implementation);
   const python=$('#op-base option[value="python"]');
   if(python)python.disabled=source?.type!=='python';
   const t=$('#op-base').value;
   $('#op-fields').innerHTML=(types[t].fields??[]).map(f=>{
     const value=t===source?.type?source.config?.[f.key]??f.default:f.default;
     if(f.type==='select')return `<label class="label">${esc(f.label)}<select name="cf-${esc(f.key)}" class="field">${f.options.map(o=>opt(o.value??o,o.label??o,value)).join('')}</select></label>`;
     const attrs=f.type==='number'?`step="${esc(f.step??'any')}" ${f.min!=null?'min="'+esc(f.min)+'"':''} ${f.max!=null?'max="'+esc(f.max)+'"':''} required`:f.optional?'':'required';
     return field(f.label,'cf-'+f.key,value,f.type==='number'?'number':'text',attrs);
   }).join('');
 }
 fields();$('#op-base').onchange=fields;
 const selector=$('#op-existing');
 if(selector)selector.onchange=()=>{
   source=custom.find(t=>t.id===selector.value)??null;
   $('#dialog input[name="name"]').value=source?.name??'';
   $('#op-base').value=source?.type??'transform';
   fields();
 };
}
function wire(from,to,id=null){const g=activeGraph(),src=g.nodes.find(n=>n.id===from),dst=g.nodes.find(n=>n.id===to),old=g.edges.find(e=>e.id===id);if(!src||!dst||from===to)return;const conds=src.type==='gate'?{pass:'通过',retry:'重试',review:'待复核'}:{next:'下一步'};const c=old?.condition??Object.keys(conds).find(v=>!g.edges.some(e=>e.from===from&&e.condition===v))??Object.keys(conds)[0];
 const apply=(cond,label)=>{if(g.edges.some(e=>e.id!==id&&e.from===from&&(e.condition||'next')===cond))throw Error('该出口已有连线，请先选中并删除旧线。');change(q=>{const e={id:id??uid('edge'),from,to,condition:cond,label};if(id)q.edges=q.edges.map(x=>x.id===id?e:x);else q.edges.push(e)})};
 if(src.type!=='gate'&&!id){try{apply('next','')}catch(e){notify(e.message)}return}
 modal('连接属性',`<p class="help">${esc(src.name)} → ${esc(dst.name)}</p><label class="label">流转条件<select name="condition" class="field">${Object.entries(conds).map(([k,v])=>opt(k,v,c)).join('')}</select></label>`+field('连线标签','label',old?.label??''),'保存连接',f=>apply(f.get('condition'),f.get('label').trim()||(src.type==='gate'?conds[f.get('condition')]:'') ))}
function download(name,text,mime='application/json'){const url=URL.createObjectURL(new Blob([text],{type:mime}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000)}
async function importFlow(file){if(!file)return;if(file.size>2e6)throw Error('文件过大，请选择流水线定义 JSON');const payload=JSON.parse(await file.text()),g=payload.graph??payload;if(typeof g.name!=='string'||!Array.isArray(g.nodes)||!Array.isArray(g.edges)||g.nodes.length>280||g.edges.length>680)throw Error('流水线 JSON 结构无效');if(!g.nodes.every(n=>typeof n.id==='string'&&Object.hasOwn(types,n.type)&&typeof n.name==='string'&&Number.isFinite(n.x)&&Number.isFinite(n.y)&&(!n.config||typeof n.config==='object'))||!g.edges.every(e=>typeof e.id==='string'&&typeof e.from==='string'&&typeof e.to==='string'))throw Error('节点或连线缺少必要字段');g.id=uid('flow');g.version=1;g.updatedAt=stamp();graphs.push(g);persist();route('editor',g.id);notify('已作为新流水线导入')}
async function presetRequest(path='',body){
 const options=body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'};
 const result=await fetch('/api/task-presets'+path,{...options,signal:AbortSignal.timeout(30000)});
 const data=await result.json();
 if(!result.ok)throw Error(data.error||'待办请求失败。');
 return data;
}
function createTaskPreset(graph){
 const issues=validateGraph(graph);
 if(issues.length){modal('编排需要调整',`<div class="formerror">${esc(issues.join('\n'))}</div>`,'返回编排',()=>route('editor'));return;}
 const defaultTask=defaultVideoTask(graph);
 const settings=[
   {type:'score',key:'every',name:'absEvery',label:'ABS 采样间隔（帧）',range:'min="1" step="1"'},
   {type:'gate',key:'threshold',name:'refineThreshold',label:'通过阈值（严格大于）',range:'min="0" max="10" step="any"'}
 ].map(setting=>({...setting,node:graph.nodes.find(node=>node.type===setting.type)})).filter(setting=>setting.node);
 const paramFields=settings.map(({type,key,name,label,range,node})=>{
   const value=node.config?.[key]??types[type].fields.find(item=>item.key===key)?.default;
   return field(label,name,value,'number',range+' required');
 }).join('');
 let picker;
 modal('预设任务',`${field('任务名','presetName','','text','maxlength="80" required placeholder="例如：H2O 找物 · 整条序列"')}
 <label class="label">识别目标<select class="field" name="presetTask">${opt('objects','找物',defaultTask)}${opt('hands','找手',defaultTask)}</select></label>
 ${paramFields?'<div class="preset-parameters">'+paramFields+'</div>':''}${datasetFields}
 <label hidden data-preset-output><input type="hidden" name="out"></label>`,'保存预设任务',async values=>{
   const name=values.get('presetName').trim();
   if(!name)throw Error('请填写任务名。');
   const snapshot=withVideoTask(clone(graph),form.elements.presetTask.value);
   for(const {key,name,node} of settings){
     const target=snapshot.nodes.find(item=>item.id===node.id);
     target.config={...target.config,[key]:Number(values.get(name))};
   }
   await presetRequest('',{name,graph:snapshot,datasets:picker.choices()});
   notify('已保存「'+name+'」，可在「启动任务」中选择。');
 });
 const form=$('#dialog form'), target=form.elements.presetTask, submit=form.querySelector('[type="submit"]');
 form.setAttribute('data-dataset-launch','');
 form.setAttribute('data-create-preset','');
 function update(){
   const disabled=!picker?.ready;
   submit.disabled=disabled;submit.setAttribute('aria-disabled',String(disabled));
   target.disabled=Boolean(picker?.taskLocked);
 }
 picker=mountDatasetPicker(form,withVideoTask(graph,defaultTask),update,{preset:true});
 target.onchange=()=>picker.setGraph(withVideoTask(graph,target.value));
 picker.setActive(true);update();
}
function chooseTaskPreset(graph){
 let presets=[], requestIds=new Map();
 const savedGraph=graphs.find(item=>item.id===graph.id)??graph;
 const hasNativeCode=savedGraph.nodes.some(item=>item.implementation?.kind==='native');
 modal('使用预设任务',`<label class="label">预设任务<select name="preset" class="field"><option value="">正在读取…</option></select></label>
 <p class="help" data-preset-summary role="status"></p><label class="code-review" data-preset-review hidden><input name="review" type="checkbox"><span>我已检查当前流水线的自定义 Python，确认本次使用这些代码。</span></label>`,
 '加入队列',async values=>{
   const preset=presets.find(item=>item.id===values.get('preset'));
   if(!preset)throw Error('请选择一个待办。');
   if((preset.requiresReview||hasNativeCode)&&values.get('review')!=='on')throw Error('请确认你已审阅当前流水线的自定义 Python。');
   if(!requestIds.has(preset.id))requestIds.set(preset.id,crypto.randomUUID());
   const data=await presetRequest('/start',{id:preset.id,requestId:requestIds.get(preset.id),graph:savedGraph,allowNativeCode:values.get('review')==='on'});
   queues=[data.queue,...queues.filter(q=>q.id!==data.queue.id)];
   awaitedOutput=data.queue.items[0]?.out??null;
   selectedRun=null;livePending=null;route('queue',graph.id);pollLive();notify('待办已加入服务器队列');
 });
 const form=$('#dialog form'), select=form.elements.preset, summary=form.querySelector('[data-preset-summary]');
 const submit=form.querySelector('[type="submit"]');
 function show(){
   const preset=presets.find(item=>item.id===select.value);
   submit.disabled=!preset;submit.setAttribute('aria-disabled',String(!preset));
   form.querySelector('[data-preset-review]').hidden=!preset||!(preset.requiresReview||hasNativeCode);
   form.elements.review.checked=false;
   if(!preset){summary.textContent='请选择预设任务。';return;}
   const details=[taskName(preset.task),preset.sequences.length+' 条序列','使用当前已保存编排'];
   if(preset.absEvery!=null)details.push('ABS 每 '+preset.absEvery+' 帧');
   if(preset.threshold!=null)details.push('通过阈值 '+preset.threshold);
   summary.textContent=details.concat(preset.sequences.join('、')).join(' · ');
 }
 select.onchange=show;show();
 presetRequest().then(data=>{
   if(!form.isConnected)return;
   presets=data.presets.filter(item=>item.pipelineId===graph.id).reverse();
   select.replaceChildren(new Option(presets.length?'请选择待办':'此流水线还没有待办',''),
                          ...presets.map(item=>new Option(item.name,item.id)));
   if(!presets.length)summary.textContent='可用页面顶部的「预设任务」创建。';
 }).catch(err=>{if(form.isConnected)summary.textContent=err.message;});
}
function start(fromDataset=false){
 const g=activeGraph(),issues=validateGraph(g);
 const presetButton=bridge?action('choose-preset','clock','使用预设任务','btn'):'';
 if(issues.length){modal('编排需要调整',`<div class="formerror">${esc(issues.join('\n'))}</div>${presetButton}`,'返回编排',()=>route('editor'));return}
 const localOK=canProcessRecords(g),nativeNodes=g.nodes.filter(n=>n.implementation?.kind==='native');
 const nativeCode=nativeNodes.length>0;
 const recordCode=g.nodes.some(n=>n.implementation?.kind==='records'||n.type==='python');
 const fusion=g.nodes.some(n=>n.type==='fusion');
 const allowRecords=localOK&&(!recordCode||directServer),allowLive=bridge&&(!(nativeCode||fusion)||directServer)&&supportsHandGraph(g);
 const allowDatasets=allowLive&&directServer;
 const modes=[['demo','流程演练','沿当前编排模拟节点和分支，不调用模型。'],...(allowRecords?[['records','处理 JSONL 数据',recordCode?'在 amax 上用已保存的 Python 处理输入记录，可导出结果。':'按算子配置处理输入记录，可导出结果。']]:[]),...(allowLive?[['live','服务器视频分割','在 DataFlow 服务器上统一处理找手或找物任务。']]:[])];
 const def=fromDataset&&allowDatasets?'live':allowRecords?'records':'demo';
 const modeHelp=recordCode&&!localOK?'本图包含记录版 Python 和视频节点。要执行记录算子，请接入 JSONL 输入与输出。':(recordCode||nativeCode)&&!directServer?'执行自定义 Python 需要通过 amax 工作台连接。':!bridge&&!localOK?'通过服务器工作台或本机桥接可查看真实任务。':'';
 const sourceFields=allowDatasets?`${fusion?'<input type="hidden" name="sourceMode" value="dataset">':'<label class="label">输入来源<select name="sourceMode" class="field"><option value="dataset">选择服务器数据集</option><option value="manual">使用已有 JSONL 清单</option></select></label>'}${datasetFields}`:'';
 let picker,submitting=false,submittedSnapshot=null;
 const defaultTask=defaultVideoTask(g);
 let commandTask=defaultTask;
 const runFolder='run-'+new Date().toISOString().replace(/[:.]/g,'-')+'-'+crypto.randomUUID().slice(0,8);
 const defaultOut=task=>videoOutputRoot+'/'+task+'/'+runFolder;
 const taskFields=allowLive?`${field('本次目标命令','videoTask',taskName(commandTask),'text','list="video-task-options" autocomplete="off" aria-describedby="video-task-help" maxlength="16"')}
 <datalist id="video-task-options"><option value="找手" label="与物体交互的手"></option><option value="找物" label="被手操作的物体"></option></datalist>
 <p class="help" id="video-task-help">输入「找手」或「找物」，用于本次提词和提示词改写。</p><p class="help" data-task-locked hidden>此队列统一使用上方命令。移除全部条目后可以切换。</p>`:'';
 const reviewFields=nativeCode?`<div class="custom-code-confirm">
   <p class="help">本次使用带自定义 Python 的节点：${esc(nativeNodes.map(n=>n.name).join('、'))}。可在编排页打开对应节点的代码。</p>
   <label class="code-review"><input name="allowNativeCode" type="checkbox"><span>我已检查这些节点的代码，确认在 amax 上执行。</span></label>
 </div>`:'';
 modal('启动任务',`<p class="help">${esc(g.name)} · ${isDraft()?'启动时保存当前修改并冻结任务版本':'v'+esc(g.version)}</p>${presetButton}${modes.map(([k,n,d])=>`<label class="option"><input type="radio" name="mode" value="${k}" ${k===def?'checked':''}><span><strong>${n}</strong><small>${d}</small></span></label>`).join('')}
 <div data-options="demo"><p class="help">演练只在页面模拟节点和分支。要处理视频，请选择「服务器视频分割」。</p>
 <label class="label">模拟质量门控结果<select name="scenario" class="field">${opt('pass','首轮通过 → 输出','pass')}${opt('retry','低分 → 改写并重分割 → 通过','')}${opt('review','转入待复核 → 输出','')}</select></label></div>
 <div data-options="records"><label class="label">输入 JSONL（每行一个对象）<textarea class="field" name="records" rows="4">{"text":"  DataFlow  "}&#10;{"text":"   "}</textarea></label></div>
 <div data-options="live">${taskFields}${sourceFields}<div data-manual>${field('服务器输入清单（绝对路径）','input','','text','placeholder="/…/input.jsonl"')}</div>${field('服务器新输出目录（绝对路径）','out',defaultOut(defaultTask))}<p class="help">启动后会使用 amax 的 GPU。启用 ABS 时会调用评分 API。</p>${reviewFields}</div>${modeHelp?`<p class="help">${esc(modeHelp)}</p>`:''}`,'确认启动',async f=>{
   if(submitting)throw Error('正在提交当前任务。');
   const mode=f.get('mode');let r;
   if(g!==activeGraph()&&JSON.stringify(g)!==JSON.stringify(activeGraph()))throw Error('流水线已修改，请重新打开启动窗口。');
   const snap=submittedSnapshot??nextSavedVersion(g);
   if(mode==='live'){
     if(!allowLive)throw Error('当前编排无法在服务器上执行。');
     if(!commandTask)throw Error('请输入「找手」或「找物」。');
     if(nativeCode&&f.get('allowNativeCode')!=='on')throw Error('请先审阅并勾选本次任务的自定义 Python 代码。');
     const useDataset=allowDatasets&&f.get('sourceMode')==='dataset';
     const source=useDataset?picker.payload():{input:f.get('input').trim()};
     const out=source.out||f.get('out').trim();
     if(!out||(!useDataset&&!source.input))throw Error(useDataset?'请填写新的服务器输出目录。':'请填写输入清单和新输出目录。');
     submittedSnapshot=snap;submitting=true;form.inert=true;updateButton();
     try{
       const runGraph=withVideoTask(snap,commandTask);
       const res=await fetch(source.datasets?'/api/queues':'/api/launch',{
         method:'POST',headers:{'Content-Type':'application/json'},
         body:JSON.stringify({graph:runGraph,...source,out,...(nativeCode?{allowNativeCode:true}:{})}),
         signal:AbortSignal.timeout(30000)
       }).catch(error=>{
         if(error.name==='TimeoutError'||error instanceof TypeError)throw Error('提交结果暂未确认。可先在「运行监控」查看队列；用相同设置重试不会重复排队。');
         throw error;
       });
       const answer=await res.json();
       if(!res.ok||!answer.ok){
         if(useDataset&&!source.datasets)picker.recheck();
         throw Error(answer.error??answer.message??'启动未获确认');
       }
       if(answer.queue){
         queues=[answer.queue,...queues.filter(q=>q.id!==answer.queue.id)];
         awaitedOutput=answer.queue.items[0]?.out??null;livePending=null;
       }else{
         const bindings=readBindings();
         bindings[answer.output??out]={graph:runGraph,pipelineId:snap.id};
         localStorage.setItem(KEY+'.live',JSON.stringify(bindings));
         awaitedOutput=answer.output??out;livePending='服务器已受理任务，正在等待首条进度记录。';
       }
       selectedRun=null;
     }finally{submitting=false;form.inert=false;updateButton();}
   }else if(mode==='records'){r=createRecordRun(snap,f.get('records'))}else{r=createDemoRun(snap,{scenario:f.get('scenario')})}
   if(isDraft())save(snap);if(r){runs.unshift(r);selectedRun=r.id;persist()}route('monitor');if(mode==='live')pollLive();notify(mode==='live'?'已提交服务器任务':'任务已启动');
 });
 const form=$('#dialog form');
 const usingDataset=()=>allowDatasets&&form.elements.mode.value==='live'&&form.elements.sourceMode.value==='dataset';
 const updateButton=()=>{
   const submit=form.querySelector('[type="submit"]');
   submit.disabled=submitting||(form.elements.mode.value==='live'&&!commandTask)||(usingDataset()&&!picker?.ready);
   submit.setAttribute('aria-disabled',String(submit.disabled));
   const count=usingDataset()?picker?.queueLength:0;
   if(allowLive){
     const locked=Boolean(picker?.taskLocked);
     form.elements.videoTask.disabled=locked;
     form.querySelector('[data-task-locked]').hidden=!locked;
   }
   submit.textContent=submitting?'正在提交…':count?'启动队列（'+count+' 条）':'确认启动';
 };
 if(allowDatasets)picker=mountDatasetPicker(form,withVideoTask(g,defaultTask),updateButton);
 const toggle=()=>{
   const mode=form.elements.mode.value;
   for(const el of form.querySelectorAll('[data-options]'))el.hidden=el.dataset.options!==mode;
   form.querySelector('[data-manual]').hidden=usingDataset();
   picker?.setActive(usingDataset());
   form.toggleAttribute('data-dataset-launch',usingDataset());updateButton();
 };
 form.addEventListener('change',e=>{
   if(!['mode','sourceMode'].includes(e.target.name))return;
   form.querySelector('.formerror').textContent='';toggle();
 });
 form.elements.videoTask?.addEventListener('input',e=>{
   if(picker?.taskLocked)return;
   const value=e.target.value.trim().toLowerCase();
   const task=new Map([['找手','hands'],['找物','objects'],['hands','hands'],['objects','objects']]).get(value);
   if(task===commandTask)return;
   commandTask=task;
   e.target.setAttribute('aria-invalid',String(!task));
   form.querySelector('.formerror').textContent='';
   const out=form.elements.out;
   out.defaultValue=task?defaultOut(task):'';out.value=out.defaultValue;
   picker?.setGraph(task?withVideoTask(g,task):null);
   updateButton();
 });
 toggle();
}
function readBindings(){try{return JSON.parse(localStorage.getItem(KEY+'.live')??'{}')}catch{return {}}}
async function pollLive(force=false){if(!bridge||remoteBusy)return;remoteBusy=true;try{const res=await fetch('/api/live',{cache:'no-store'}),data=await res.json();if(!res.ok||data.error)throw Error(data.error||'进度读取失败');if(!queueOrderBusy&&!draggedQueueItem){queues=data.queues??[];queueOrder=data.queueOrder??[]}const b=readBindings();live=(data.runs??[]).map(r=>({...r,...(b[r.output??r.out]??{})}));const waiting=awaitedOutput&&live.find(r=>(r.output??r.out)===awaitedOutput);if(waiting){selectedRun=waiting.id;awaitedOutput=null;livePending=null}liveError=data.warning??''}catch(e){liveError='服务器进度暂不可用：'+e.message}finally{remoteBusy=false;if((['monitor','history','queue'].includes(page))&&!$('#dialog').open&&!draggedQueueItem)render();if(force)notify(liveError||'已刷新任务记录')}}
async function probe(){if(!['127.0.0.1','localhost','[::1]'].includes(location.hostname))return;try{const res=await fetch('/api/bridge',{cache:'no-store'});if(!res.ok)return;const info=await res.json();bridge=info.connected===true;directServer=bridge&&info.mode==='direct';if(info.outputRoot)videoOutputRoot=info.outputRoot;if(directServer)bridgeLabel=info.host+' · 服务器已连接';if(bridge){render();await libraryStore.connect();pollLive()}}catch{}}
document.addEventListener('click',e=>{const t=e.target.closest('button,[data-run],[data-open]');if(!t){if(menu&&!e.target.closest('aside')){menu=false;render()}return}
 const a=t.dataset.act,g=activeGraph();
 if(t.hasAttribute('data-queue-move')){
   const from=t.closest('[data-queue-item]').dataset.queueItem;
   void changeQueueOrder(from,queueOrder[queueOrder.indexOf(from)+Number(t.dataset.queueMove)]);
   return;
 }
 if(a==='review-cancel'){reviews.cancel(selectedGraph);return}
 if(a==='choose-preset'){chooseTaskPreset(g);return}
 if(a==='resume-run'){confirmResume();return}
 if(a==='create-preset'){createTaskPreset(g);return}
 if(a==='editnotes'){editOperatorNotes();return}
 if(a==='pipeline-doc'){import('./pipeline-doc.js').then(m=>m.openPipelineDoc(g)).catch(error=>notify('说明文档未能打开：'+error.message));return}
 if(a==='gpu-status'){import('./gpu-status.js').then(m=>m.openGpuStatus()).catch(error=>notify('GPU 面板未能打开：'+error.message));return}
 if(a==='review-now'){reviews.start(graphs.find(item=>item.id===selectedGraph),false);return}
 if(t.dataset.open){route('editor',t.dataset.open);return}if(t.dataset.run){const r=allRuns().find(x=>x.id===t.dataset.run);if(r){if(!graphs.some(g=>g.id===r.pipelineId)&&r.graph&&!validateGraph(r.graph).length){graphs.push(clone(r.graph));persist()}selectedGraph=r.pipelineId;lastGraph=r.pipelineId;selectedRun=r.id;route('monitor',r.pipelineId)}return}
 if(t.hasAttribute('data-op')){previewOperator(t);return}if(t.dataset.edgeEdit){const edge=g.edges.find(x=>x.id===t.dataset.edgeEdit);if(edge)wire(edge.from,edge.to,edge.id);return}
 switch(a){case 'menu':menu=!menu;render();break;case 'catalog':case 'history':case 'editor':case 'monitor':case 'queue':route(a);if(a==='queue')pollLive();break;case 'new':newFlow();break;case 'newop':editCode('new');break;case 'newparam':designOp(false);break;case 'editparam':designOp(false,selectedOp);break;case 'editop':editCode('library');break;case 'editnodecode':editCode('node');break;case 'template':designOp(true);break;case 'start':start();break;case 'datasets':start(true);break;case 'save':save();break;case 'graphsettings':settings();break;case 'dismiss':$('#dialog').close();break;
 case 'delete-node':{const n=g.nodes.find(n=>n.id===selectedNode);if(!n)return;modal('删除节点',`<p>删除「${esc(n.name)}」以及与它相连的连线？</p>`,'删除',()=>{change(q=>{q.nodes=q.nodes.filter(x=>x.id!==n.id);q.edges=q.edges.filter(x=>x.from!==n.id&&x.to!==n.id)});selectedNode=null;render()});break}
 case 'duplicate':{const c=clone(g);c.id=uid('flow');c.name+=' · 副本';c.version=1;c.updatedAt=stamp();graphs.push(c);persist();$('#dialog').close();route('editor',c.id);break}
 case 'export':download(g.name+'.json',JSON.stringify({graph:g},null,2));break;case 'import':$('#import-file').click();break;case 'refresh':if(bridge)pollLive(true);else{render();notify('已刷新运行记录')}break;case 'result':{const r=currentRun();if(r)download('result.jsonl',(r.records??[]).map(x=>JSON.stringify(x)).join('\n')+'\n','application/x-ndjson');break}}
});
document.addEventListener('change',e=>{const el=e.target;if(el.id==='import-file'){importFlow(el.files[0]).catch(err=>notify(err.message));el.value='';return}if(el.id==='run-select'){selectedRun=el.value;render();return}
 if(el.id==='auto-review'){reviews.setEnabled(el.checked);$('#auto-review')?.focus();return}
 if(el.id==='node-name'||el.dataset.config)saveNodeField(el)});
document.addEventListener('input',e=>{if(e.target.id==='search'){search=e.target.value;render();$('#search')?.focus()}});
document.addEventListener('dragstart',e=>{
 const queued=e.target.closest('[data-queue-item][draggable="true"]');
 if(queued){
   draggedQueueItem=queued.dataset.queueItem;
   e.dataTransfer.setData('text/plain',draggedQueueItem);
   e.dataTransfer.effectAllowed='move';
   queued.classList.add('queue-dragging');return;
 }
 const el=e.target.closest('[data-op]');if(!el)return;
 e.dataTransfer.setData('application/x-flow-type',el.dataset.op);e.dataTransfer.effectAllowed='copy';
});
document.addEventListener('dragover',e=>{
 const target=e.target.closest('[data-queue-item]');
 if(!draggedQueueItem||!target)return;
 e.preventDefault();e.dataTransfer.dropEffect='move';
 $('.queue-drop-target')?.classList.remove('queue-drop-target');target.classList.add('queue-drop-target');
});
document.addEventListener('drop',e=>{
 const target=e.target.closest('[data-queue-item]');
 if(!draggedQueueItem||!target)return;
 e.preventDefault();
 const from=draggedQueueItem;draggedQueueItem=null;
 $('.queue-dragging')?.classList.remove('queue-dragging');
 $('.queue-drop-target')?.classList.remove('queue-drop-target');
 void changeQueueOrder(from,target.dataset.queueItem);
});
document.addEventListener('dragend',()=>{
 draggedQueueItem=null;$('.queue-dragging')?.classList.remove('queue-dragging');
 $('.queue-drop-target')?.classList.remove('queue-drop-target');
});
document.addEventListener('toggle',e=>{
 const details=e.target;
 if(!(details instanceof HTMLDetailsElement)||!details.dataset.clipOutput)return;
 if(details.open)expandedClips.add(details.dataset.clipOutput);
 else expandedClips.delete(details.dataset.clipOutput);
},true);
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='s'&&page==='editor'&&!document.querySelector('dialog[open]')){e.preventDefault();save()}});
$('#dialog').addEventListener('click',e=>{if(e.target===$('#dialog')&&!$('#dialog form')?.inert)$('#dialog').close()});
$('#dialog').addEventListener('cancel',e=>{if($('#dialog form')?.inert)e.preventDefault()});
const pendingSteps=new Set();
async function tickRun(run){
 if(run.status!=='running'||pendingSteps.has(run.id))return;
 pendingSteps.add(run.id);
 try{
   const updated=run.mode==='records'?await stepRecords(run):advanceRun(run);
   const index=runs.indexOf(run);
   if(index<0)return;
   runs[index]=updated;
   persist();if((['monitor','history','queue'].includes(page))&&!$('#dialog').open&&!draggedQueueItem)render();
 }finally{pendingSteps.delete(run.id)}
}
setInterval(()=>{for(const run of runs)void tickRun(run)},1350);
setInterval(()=>{if(!document.hidden&&['monitor','history','queue'].includes(page))pollLive()},7000);
readRoute();render();probe();
