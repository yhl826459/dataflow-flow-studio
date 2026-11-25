import { icon, types, dualSegment, segmentPorts } from './model.js';

const SVG = 'http://www.w3.org/2000/svg';
const WIDTH = 174, HEIGHT = 72;
const views = new Map();
let serial = 0;
const statusText = { running: '执行中', done: '已完成', skipped: '已跳过', failed: '失败', waiting: '待执行' };
const fallbackColors = { input: '#617399', qwen: '#8559c8', segment: '#3685c0', score: '#bd7a20', gate: '#bd7a20', refine: '#9261bd', output: '#138274', transform: '#5279ba', filter: '#ad7430' };

function nodeSummary(node) {
  const c=node.config || {};
  switch(node.type) {
    case 'input': return 'RGB 视频清单';
    case 'qwen': return (c.sample_fps ?? 5)+' fps · 目标描述';
    case 'segment': return '逐实例二值掩码';
    case 'score': return '每 '+(c.every ?? 6)+' 帧采样';
    case 'gate': return '均分 > '+(c.threshold ?? 7)+' · 最多 '+(c.maxRetries ?? 2)+' 轮';
    case 'refine': return '低分帧反馈';
    case 'output': return 'JSONL 汇总';
    case 'transform': return (c.sourceKey || 'text')+' → '+(c.targetKey || 'text');
    case 'filter': return (c.key || 'text')+' · '+({equals:'等于指定值',contains:'包含指定值',nonempty:'非空'}[c.rule] || '非空');
    default: return types[node.type]?.label || '自定义算子';
  }
}

function html(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}
function svg(tag, attrs) {
  const el = document.createElementNS(SVG, tag);
  for (const [k,v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}
const clamp = (n, lo, hi) => Math.min(Math.max(n, lo), hi);
function route(source, dest, peer, peers, nodes, port) {
  const a = [source.x + WIDTH + 4, source.y + HEIGHT / 2];
  const b = [dest.x - 7, dest.y + HEIGHT * (port==='rgb' ? .25 : port==='targets' ? .75 : .5)];
  const offset = (peer - (peers-1)/2) * 34;
  const gap = b[0] - a[0], sameRow = Math.abs(b[1] - a[1]) < HEIGHT;
  const crossesNode = sameRow && nodes.some(n => n !== source && n !== dest && n.x > a[0] && n.x < b[0] && Math.abs(n.y-source.y) < HEIGHT);
  let points, label;
  if (gap > 15 && !crossesNode && peers === 1) {
    const x = (a[0]+b[0])/2;
    points = [a, [x,a[1]], [x,b[1]], b];
    label = [x, (a[1]+b[1])/2 - (sameRow ? 16 : 0)];
  } else if (gap > 15 && sameRow && peers > 1 && !crossesNode) {
    const y = (a[1]+b[1])/2 + offset;
    points = [a, [a[0]+6,a[1]], [a[0]+6,y], [b[0]-6,y], [b[0]-6,b[1]], b];
    label = [(a[0]+b[0])/2,y+(offset < 0 ? -11 : 11)];
  } else {
    const y = sameRow ? Math.max(a[1],b[1]) + HEIGHT/2 + 37 + peer*22 : (a[1]+b[1])/2 + offset;
    const x1 = a[0]+14+peer*3, x2 = b[0]-15-peer*3;
    points = [a, [x1,a[1]], [x1,y], [x2,y], [x2,b[1]], b];
    label = [(x1+x2)/2,y-10];
  }
  return {points, label};
}
function rounded(points) {
  const pts = points.filter((p, i) => !i || p[0] !== points[i-1][0] || p[1] !== points[i-1][1]);
  let d = 'M ' + pts[0].join(' ');
  for (let i=1; i<pts.length-1; i++) {
    const [x,y]=pts[i], prev=pts[i-1], next=pts[i+1];
    const l1=Math.hypot(x-prev[0],y-prev[1]), l2=Math.hypot(next[0]-x,next[1]-y);
    const r=Math.min(9,l1/2,l2/2);
    d+=' L '+(x+(prev[0]-x)*r/l1)+' '+(y+(prev[1]-y)*r/l1);
    d+=' Q '+x+' '+y+' '+(x+(next[0]-x)*r/l2)+' '+(y+(next[1]-y)*r/l2);
  }
  return d+' L '+pts[pts.length-1].join(' ');
}
function controlButton(label, content) {
  const btn = html('button','fc-control');
  btn.type = 'button';
  btn.title = label;
  btn.setAttribute('aria-label',label);
  btn.innerHTML = content;
  return btn;
}
const glyphs = {
  minus: '<path d="M4 9h10"/>', plus: '<path d="M4 9h10M9 4v10"/>',
  fit: '<path d="M3 7V3h4m4 0h4v4m0 4v4h-4m-4 0H3v-4"/>',
  map: '<path d="m2 4 4-1.5 5 1.5 5-1.5v11l-5 1.5-5-1.5L2 15zM6 3v10m5-9v10"/>',
  collapse: '<path d="m5 7 4 4 4-4"/>'
};
function glyph(key) {
  return '<svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+glyphs[key]+'</svg>';
}

export function mountCanvas(host, opts) {
  const {graph, mode='edit', selected, states={}, activeEdges=[]} = opts;
  const editable = mode === 'edit';
  const monitoring = mode === 'monitor';
  const running = monitoring && opts.running === true;
  const nodes = graph.nodes.map(n => ({...n, x:Number(n.x)||0, y:Number(n.y)||0}));
  const byId = new Map(nodes.map(n => [n.id,n]));
  const dual=dualSegment(graph);
  const active = new Set(activeEdges);
  const key = opts.viewKey || graph.id || nodes.map(n=>n.id).join('|') || 'new';
  const uid = 'fcn'+(++serial);
  const saved = views.get(key);
  let zoom = saved?.zoom ?? 1, tx = saved?.tx ?? 0, ty = saved?.ty ?? 0;
  let connection = null, edgeSelection = null, drag = null, autoFit = !saved;
  const handlers = new AbortController();
  const listen = (el,type,fn,more={}) => el.addEventListener(type,fn,{signal:handlers.signal,...more});
  const root = html('div','fc-surface');
  // Polling replaces the canvas. Continue the motion from the same document clock.
  if (running) root.style.setProperty('--fc-phase', -(document.timeline.currentTime ?? performance.now())+'ms');
  root.tabIndex = 0;
  root.setAttribute('role','region');
  root.setAttribute('aria-label',monitoring?'运行监控画布':'流水线编排画布');
  const world = html('div','fc-world');
  const wires = svg('svg',{class:'fc-wires','aria-label':'节点连接'});
  const defs = svg('defs',{});
  for (const kind of ['base','active','selected']) {
    const marker = svg('marker',{id:uid+kind, viewBox:'0 0 8 8', refX:'7',refY:'4',markerWidth:'6.5',markerHeight:'6.5',orient:'auto',markerUnits:'userSpaceOnUse'});
    marker.append(svg('path',{d:'M1 1 L7 4 L1 7',fill:'none',stroke:kind==='base'?'#8297a3':'#138878','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round'}));
    defs.append(marker);
  }
  const edgeLayer = svg('g',{});
  const draftPath = svg('path',{class:'fc-draft',fill:'none',display:'none'});
  wires.append(defs,edgeLayer,draftPath);
  world.append(wires); root.append(world);
  const actions = html('div','fc-controls');
  const minus = controlButton('缩小',glyph('minus')), plus = controlButton('放大',glyph('plus'));
  const fitBtn = controlButton('适应画布',glyph('fit')), percentage=html('span','fc-percentage');
  actions.append(minus, percentage, plus, html('i','fc-divider'), fitBtn);
  root.append(actions);
  const mini = html('div','fc-minimap'), mapBtn = controlButton('收起缩略图',glyph('collapse'));
  const map = svg('svg',{class:'fc-mini-svg',role:'img','aria-label':'全图导航'});
  mini.append(map,mapBtn); root.append(mini);
  const hint=html('div','fc-hint');
  hint.setAttribute('role','status');
  root.append(hint);
  const nodeEls=new Map(), edgeEls=[];
  const selectedEdges=html('div','fc-edge-actions');
  const edgeEdit=html('button','', '编辑连线'), edgeDelete=html('button','fc-delete','删除连线');
  edgeEdit.type=edgeDelete.type='button';
  if (opts.onEditEdge) selectedEdges.append(edgeEdit);
  selectedEdges.append(edgeDelete);
  root.append(selectedEdges);
  host.replaceChildren(root);

  function coordinates(clientX, clientY) {
    const rect=root.getBoundingClientRect();
    return [(clientX-rect.left-tx)/zoom,(clientY-rect.top-ty)/zoom];
  }
  function bounds() {
    if (!nodes.length) return {x:0,y:0,w:400,h:250};
    const xx=nodes.flatMap(n=>[n.x,n.x+WIDTH]), yy=nodes.flatMap(n=>[n.y,n.y+HEIGHT]);
    for (const e of edgeEls) if(e.geom) {
      for (const p of e.geom.points) {xx.push(p[0]);yy.push(p[1]);}
      if(e.label) { xx.push(e.geom.label[0]-23,e.geom.label[0]+23); yy.push(e.geom.label[1]-10,e.geom.label[1]+10); }
    }
    const left=Math.min(...xx), top=Math.min(...yy);
    return {x:left,y:top,w:Math.max(...xx)-left,h:Math.max(...yy)-top};
  }
  const miniView=svg('rect',{class:'fc-mini-view',rx:'3'});
  function drawMini() {
    const b=bounds();
    const pad=36;
    map.setAttribute('viewBox',[b.x-pad,b.y-pad,b.w+pad*2,b.h+pad*2].join(' '));
    map.replaceChildren();
    for(const e of edgeEls) if(e.geom) map.append(svg('path',{class:'fc-mini-edge',d:rounded(e.geom.points),fill:'none'}));
    for(const n of nodes) map.append(svg('rect',{x:n.x,y:n.y,width:WIDTH,height:HEIGHT,rx:12,fill:types[n.type]?.color||fallbackColors[n.type]||'#849399'}));
    map.append(miniView);
    moveMini();
  }
  function moveMini() {
    for(const [prop,value] of Object.entries({x:-tx/zoom,y:-ty/zoom,width:root.clientWidth/zoom,height:root.clientHeight/zoom})) miniView.setAttribute(prop,value);
  }
  function transform() {
    world.style.transform='translate('+tx+'px,'+ty+'px) scale('+zoom+')';
    root.style.backgroundPosition=tx+'px '+ty+'px';
    root.style.backgroundSize=(20*zoom)+'px '+(20*zoom)+'px';
    percentage.textContent=Math.round(zoom*100)+'%';
    moveMini();
    if(!autoFit) views.set(key,{zoom,tx,ty});
  }
  function fit() {
    const b=bounds(), w=root.clientWidth, h=root.clientHeight;
    if(!w || !h) return;
    const side=w<440?22:42, top=h>270?45:20, bottom=h>270?72:33;
    zoom=clamp(Math.min((w-2*side)/b.w,(h-top-bottom)/b.h),w<440?.62:.45,1.12);
    tx=w<440 && b.w*zoom>w-2*side ? side-b.x*zoom : (w-b.w*zoom)/2-b.x*zoom;
    ty=top+(h-top-bottom-b.h*zoom)/2-b.y*zoom;
    transform();
  }
  function zoomBy(factor,cx=root.clientWidth/2,cy=root.clientHeight/2) {
    autoFit=false;
    const next=clamp(zoom*factor,.35,2.2);
    tx=cx-(cx-tx)*next/zoom;
    ty=cy-(cy-ty)*next/zoom;
    zoom=next;
    transform();
  }
  function edgeFocus(id=null) {
    edgeSelection=id;
    selectedEdges.classList.toggle('fc-visible',id!==null && editable);
    for(const entry of edgeEls) {
      const isSelected=id!==null && entry.edge.id===id;
      entry.g.classList.toggle('fc-chosen',isSelected);
      entry.path.setAttribute('marker-end','url(#'+uid+(isSelected?'selected':entry.g.classList.contains('fc-active')?'active':'base')+')');
    }
  }
  function resetConnection() {
    connection=null; hint.textContent=''; draftPath.setAttribute('display','none');
    root.classList.remove('fc-connecting');
    nodeEls.forEach(el=>el.classList.remove('fc-origin'));
  }
  function setConnection(id) {
    if (connection===id) { resetConnection(); return; }
    resetConnection(); edgeFocus(); connection=id;
    root.classList.add('fc-connecting'); nodeEls.get(id)?.classList.add('fc-origin');
    hint.textContent='选择目标节点的输入端口 · Esc 取消';
  }
  function finishConnection(id,port) {
    if(!connection || connection===id) return;
    if(dual && byId.get(id)?.type==='prompt'){hint.textContent='中文目标描述由启动参数提供，无需接入视频。';return;}
    if(id===dual?.id && !port){hint.textContent='请选择 RGB 视频或目标描述端口';return;}
    const start=connection; resetConnection();
    opts.onConnect?.(start,id,port);
  }
  function refreshLines() {
    for(const entry of edgeEls) {
      const {edge,peer,peers}=entry;
      const geom=route(byId.get(edge.from),byId.get(edge.to),peer,peers,nodes,edge.targetPort); entry.geom=geom;
      const d=rounded(geom.points);
      entry.path.setAttribute('d',d); entry.hit.setAttribute('d',d);
      entry.flow?.setAttribute('d',d);
      if(entry.label) entry.label.setAttribute('transform','translate('+geom.label.join(' ')+')');
    }
    drawMini();
  }
  const edgeGroups=new Map();
  const incoming=new Map();
  const edgeName=e=>'选择连线 '+byId.get(e.from).name+' 至 '+byId.get(e.to).name+(e.targetPort?'，'+segmentPorts[e.targetPort]:'')+(e.label?'，'+e.label:'');
  graph.edges.forEach(e=>{
    if(!byId.has(e.from)||!byId.has(e.to)) return;
    if(running && states[e.to]==='running') incoming.set(e.to,(incoming.get(e.to)||0)+1);
    const k=JSON.stringify([e.from,e.to]);
    if(!edgeGroups.has(k)) edgeGroups.set(k,[]);
    edgeGroups.get(k).push(e);
  });
  for(const group of edgeGroups.values()) for(const [i,edge] of group.entries()) {
    // A visited edge at a join may belong to an earlier retry round.
    const current=running && states[edge.to]==='running' &&
      (edge.targetPort ? active.has(edge.id) : opts.currentEdge!=null ? edge.id===opts.currentEdge : incoming.get(edge.to)===1 && active.has(edge.id));
    const g=svg('g',{class:'fc-edge'+(active.has(edge.id)||current?' fc-active':'')+(current?' fc-current':'')});
    g.dataset.condition=edge.condition || 'next';
    const path=svg('path',{class:'fc-edge-line',fill:'none','marker-end':'url(#'+uid+(active.has(edge.id)||current?'active':'base')+')'});
    const hit=svg('path',{class:'fc-edge-hit',fill:'none'});
    const flow=current?svg('path',{class:'fc-flow-mark',fill:'none','aria-hidden':'true'}):null;
    g.append(path);
    if(flow) g.append(flow);
    g.append(hit);
    if(editable) {
      g.setAttribute('role','button'); g.setAttribute('tabindex','0');
      g.setAttribute('aria-label',edgeName(edge));
      listen(g,'click',ev=>{ev.stopPropagation();resetConnection();edgeFocus(edge.id);});
      listen(g,'keydown',ev=>{
        if(ev.key==='Enter'||ev.key===' ') { ev.preventDefault();edgeFocus(edge.id);selectedEdges.querySelector('button')?.focus(); }
      });
    }else if(monitoring && edge.targetPort){
      const description=segmentPorts[edge.targetPort]+(active.has(edge.id)?' · 已就绪':' · 等待输入');
      g.setAttribute('role','img');g.setAttribute('aria-label',description);
      const title=svg('title',{});title.textContent=description;g.append(title);
    }
    let label=null;
    if(edge.label) {
      label=svg('g',{class:'fc-edge-label'});
      const text=String(edge.label), chars=Array.from(text);
      const visible=chars.length>16?chars.slice(0,15).join('')+'…':text;
      const width=Array.from(visible).reduce((a,c)=>a+(c.charCodeAt(0)>255?10.5:6),12);
      label.append(svg('rect',{x:-width/2,y:-9,width,height:18,rx:5}));
      const caption=svg('text',{'text-anchor':'middle',dy:'3.5'}); caption.textContent=visible; label.append(caption); g.append(label);
    }
    edgeLayer.append(g);
    edgeEls.push({edge,g,path,hit,flow,label,peer:i,peers:group.length});
  }
  function makePort(n,dir,key) {
    const port=html('button','fc-port fc-port-'+dir);
    port.type='button'; port.setAttribute('aria-label',n.name+(dir==='out'?' 输出端口':' 输入端口'));
    port.title=dir==='out'?'从此处连接':'连接到此处';
    if(key){
      port.dataset.port=key;
      port.title=port.getAttribute('aria-label')+' · '+segmentPorts[key];
      port.setAttribute('aria-label',n.name+' '+segmentPorts[key]+' 输入端口');
      port.append(html('span','fc-port-label',segmentPorts[key]));
    }
    if(!editable) {port.disabled=true;port.tabIndex=-1;return port;}
    listen(port,'pointerdown',ev=>{
      ev.stopPropagation();
      if(ev.button!==0||dir!=='out') return;
      drag={kind:'port',pointer:ev.pointerId,n,startX:ev.clientX,startY:ev.clientY,didMove:false};
      root.setPointerCapture(ev.pointerId);
    });
    listen(port,'click',ev=>{
      ev.preventDefault();ev.stopPropagation();
      if(dir==='out') setConnection(n.id);
      else if(connection) finishConnection(n.id,key);
      else { hint.textContent='先选择来源节点的输出端口'; }
    });
    return port;
  }
  for(const n of nodes) {
    const type=types[n.type]||{};
    const node=html('div','fc-node'+(selected===n.id?' fc-selected':''));
    node.dataset.nodeId=n.id;node.dataset.type=n.type;
    node.style.setProperty('--fc-node-color',type.color||fallbackColors[n.type]||'#5c7a84');
    node.style.left=n.x+'px';node.style.top=n.y+'px';
    const body=html('button','fc-node-body');
    body.type='button';body.title=n.name;body.setAttribute('aria-label','查看 '+n.name);
    const badge=html('span','fc-node-icon');
    badge.innerHTML=icon(type.icon || 'box',19);
    const details=html('span','fc-node-details');
    const name=html('strong','',n.name||type.label||'算子');
    const state=states[n.id]||'waiting';
    const sub=html('small','',!monitoring?nodeSummary(n):state==='running'&&!running?'上次记录：执行中':(statusText[state]||'待执行'));
    if(running && state==='running') node.classList.add('fc-executing');
    sub.title=sub.textContent;
    if(monitoring) {node.dataset.state=state;sub.prepend(html('i','fc-state-dot'));}
    details.append(name,sub);body.append(badge,details);
    node.append(body);
    if(n.id===dual?.id){
      node.classList.add('fc-dual');
      for(const key of Object.keys(segmentPorts)) node.append(makePort(n,'in',key));
    }else if(n.type!=='input' && !(dual && n.type==='prompt')) node.append(makePort(n,'in'));
    if(n.type!=='output') node.append(makePort(n,'out'));
    listen(body,'pointerdown',ev=>{
      if(ev.button!==0||!editable||connection) return;
      ev.stopPropagation();
      drag={kind:'node',pointer:ev.pointerId,n,startX:ev.clientX,startY:ev.clientY,x:n.x,y:n.y,didMove:false};
      root.setPointerCapture(ev.pointerId);
    });
    listen(body,'click',ev=>{
      ev.stopPropagation();
      if(connection && n.type!=='input') {finishConnection(n.id);return;}
      if(!editable && !monitoring) nodeEls.forEach((el,id)=>el.classList.toggle('fc-selected',id===n.id));
      edgeFocus();opts.onSelect?.(n.id);
    });
    listen(body,'keydown',ev=>{
      if(!editable||!ev.altKey) return;
      const delta={ArrowLeft:[-10,0],ArrowRight:[10,0],ArrowUp:[0,-10],ArrowDown:[0,10]}[ev.key];
      if(delta) {ev.preventDefault();opts.onMove?.(n.id,n.x+delta[0],n.y+delta[1]);}
    });
    world.append(node);nodeEls.set(n.id,node);
  }
  if(!nodes.length) {
    const msg=html('div','fc-empty',editable?'从算子库拖入第一个节点':'这条流水线还没有节点');
    root.append(msg);
  }
  listen(root,'pointerdown',ev=>{
    if(ev.button!==0&&ev.button!==1) return;
    if(ev.target.closest('.fc-node,.fc-edge,.fc-controls,.fc-minimap,.fc-edge-actions')) return;
    resetConnection();edgeFocus();
    root.focus({preventScroll:true});
    drag={kind:'pan',pointer:ev.pointerId,startX:ev.clientX,startY:ev.clientY,x:tx,y:ty,didMove:false};
    root.setPointerCapture(ev.pointerId);
  });
  listen(root,'pointermove',ev=>{
    if(drag && ev.pointerId===drag.pointer) {
      const dx=ev.clientX-drag.startX,dy=ev.clientY-drag.startY;
      if(!drag.didMove && Math.hypot(dx,dy)>4) {
        drag.didMove=true;
        if(drag.kind==='port') setConnection(drag.n.id);
        else if(drag.kind==='pan') root.classList.add('fc-panning');
        else nodeEls.get(drag.n.id).classList.add('fc-moving');
      }
      if(drag.didMove && drag.kind==='pan') {autoFit=false;tx=drag.x+dx;ty=drag.y+dy;transform();}
      if(drag.didMove && drag.kind==='node') {
        const n=drag.n,el=nodeEls.get(n.id);
        n.x=Math.round(drag.x+dx/zoom);n.y=Math.round(drag.y+dy/zoom);
        el.style.left=n.x+'px';el.style.top=n.y+'px';refreshLines();
      }
    }
    if(connection) {
      const n=byId.get(connection), [x,y]=coordinates(ev.clientX,ev.clientY);
      const sx=n.x+WIDTH+4, sy=n.y+HEIGHT/2, bend=Math.max(24,Math.abs(x-sx)/2);
      draftPath.setAttribute('d','M'+sx+' '+sy+' C'+(sx+bend)+' '+sy+' '+(x-bend)+' '+y+' '+x+' '+y);
      draftPath.removeAttribute('display');
    }
  });
  listen(root,'pointerup',ev=>{
    if(!drag||ev.pointerId!==drag.pointer) return;
    const d=drag;drag=null;
    if(root.hasPointerCapture(ev.pointerId)) root.releasePointerCapture(ev.pointerId);
    root.classList.remove('fc-panning');
    if(d.kind==='node') {
      nodeEls.get(d.n.id)?.classList.remove('fc-moving');
      if(d.didMove) opts.onMove?.(d.n.id,d.n.x,d.n.y);
      else {edgeFocus();opts.onSelect?.(d.n.id);}
    }
    if(d.kind==='port') {
      if(!d.didMove) setConnection(d.n.id);
      else {
        const hit=document.elementFromPoint(ev.clientX,ev.clientY);
        const el=hit?.closest('.fc-node');
        const dest=el&&nodes.find(n=>String(n.id)===el.dataset.nodeId);
        if(dest && dest.type!=='input' && dest.id!==d.n.id) finishConnection(dest.id,hit.closest('.fc-port-in')?.dataset.port);
        else resetConnection();
      }
    }
  });
  listen(root,'pointercancel',()=>{drag=null;root.classList.remove('fc-panning');nodeEls.forEach(el=>el.classList.remove('fc-moving'));resetConnection();});
  listen(root,'keydown',ev=>{
    if(ev.key==='Escape') {resetConnection();edgeFocus();}
    if(ev.key==='Delete' && edgeSelection!==null && editable) {ev.preventDefault();opts.onDeleteEdge?.(edgeSelection);}
    if(ev.key==='+'||ev.key==='=') zoomBy(1.15);
    if(ev.key==='-') zoomBy(1/1.15);
  });
  listen(root,'wheel',ev=>{
    ev.preventDefault();
    if(ev.ctrlKey||ev.metaKey) {
      const rect=root.getBoundingClientRect();zoomBy(Math.exp(-ev.deltaY*.005),ev.clientX-rect.left,ev.clientY-rect.top);
    } else {autoFit=false;tx-=ev.shiftKey?ev.deltaY:ev.deltaX;ty-=ev.shiftKey?0:ev.deltaY;transform();}
  },{passive:false});
  if(editable) {
    function addAt(type,clientX,clientY) {
      const [x,y]=coordinates(clientX,clientY);
      opts.onAdd?.(type,Math.round(x-WIDTH/2),Math.round(y-HEIGHT/2));
    }
    const editor=host.closest('.editor-grid');
    if(editor) {
      let touchDrag=null;
      function clearTouchDrag() {
        touchDrag?.label?.remove();
        touchDrag=null;
        root.classList.remove('fc-dropping');
      }
      handlers.signal.addEventListener('abort',clearTouchDrag,{once:true});
      listen(editor,'touchstart',ev=>{
        if(ev.touches.length!==1) {clearTouchDrag();return;}
        const card=ev.target.closest('[data-op] .nodeicon')?.closest('[data-op]');
        if(!card) return;
        const t=ev.changedTouches[0];
        touchDrag={id:t.identifier,card,x:t.clientX,y:t.clientY,label:null};
      },{passive:true});
      listen(editor,'dragstart',ev=>{if(touchDrag) ev.preventDefault();},{capture:true});
      listen(editor,'contextmenu',ev=>{if(touchDrag) ev.preventDefault();});
      listen(editor,'touchmove',ev=>{
        if(!touchDrag) return;
        const t=Array.from(ev.changedTouches).find(t=>t.identifier===touchDrag.id);
        if(!t) return;
        if(!touchDrag.label) {
          if(Math.hypot(t.clientX-touchDrag.x,t.clientY-touchDrag.y)<6) return;
          touchDrag.label=html('span','fc-touch-label',touchDrag.card.textContent.trim());
          editor.append(touchDrag.label);
          if(root.getBoundingClientRect().bottom<100) root.scrollIntoView({block:'center',behavior:'instant'});
        }
        ev.preventDefault();
        touchDrag.label.style.left=t.clientX+'px';
        touchDrag.label.style.top=t.clientY+'px';
        root.classList.toggle('fc-dropping',root.contains(document.elementFromPoint(t.clientX,t.clientY)));
      },{passive:false});
      listen(editor,'touchend',ev=>{
        if(!touchDrag) return;
        const t=Array.from(ev.changedTouches).find(t=>t.identifier===touchDrag.id);
        if(!t) return;
        const {card,label}=touchDrag;
        clearTouchDrag();
        if(!label) return;
        ev.preventDefault();
        if(root.contains(document.elementFromPoint(t.clientX,t.clientY))) addAt(card.dataset.op,t.clientX,t.clientY);
      },{passive:false});
      listen(editor,'touchcancel',clearTouchDrag,{passive:true});
    }
    listen(root,'dragover',ev=>{
      if(!Array.from(ev.dataTransfer.types).includes('application/x-flow-type')) return;
      ev.preventDefault();ev.dataTransfer.dropEffect='copy';root.classList.add('fc-dropping');
    });
    listen(root,'dragleave',ev=>{if(!root.contains(ev.relatedTarget)) root.classList.remove('fc-dropping');});
    listen(root,'drop',ev=>{
      root.classList.remove('fc-dropping');
      const type=ev.dataTransfer.getData('application/x-flow-type');
      if(!type||!Array.from(ev.dataTransfer.types).includes('application/x-flow-type')) return;
      ev.preventDefault();
      addAt(type,ev.clientX,ev.clientY);
    });
  }
  listen(edgeDelete,'click',()=>{if(edgeSelection!==null) opts.onDeleteEdge?.(edgeSelection);});
  listen(edgeEdit,'click',()=>{if(edgeSelection!==null) opts.onEditEdge?.(edgeSelection);});
  listen(minus,'click',()=>zoomBy(1/1.2));listen(plus,'click',()=>zoomBy(1.2));
  const resetFit=()=>{views.delete(key);autoFit=true;fit();};
  listen(fitBtn,'click',resetFit);
  listen(mapBtn,'click',()=>{
    const collapsed=mini.classList.toggle('fc-collapsed');
    mapBtn.innerHTML=glyph(collapsed?'map':'collapse');
    mapBtn.setAttribute('aria-label',collapsed?'展开缩略图':'收起缩略图');mapBtn.title=mapBtn.getAttribute('aria-label');
  });
  listen(map,'pointerdown',ev=>{
    const ctm=map.getScreenCTM();if(!ctm)return;
    const p=new DOMPoint(ev.clientX,ev.clientY).matrixTransform(ctm.inverse());
    autoFit=false;tx=root.clientWidth/2-p.x*zoom;ty=root.clientHeight/2-p.y*zoom;transform();
  });
  refreshLines();
  if(autoFit) fit();else transform();
  const resize=new ResizeObserver(()=>{if(autoFit) fit();else transform();});
  resize.observe(root);
  function updateNodeDetails(updated) {
    const n=byId.get(updated.id), el=nodeEls.get(updated.id);
    if(!editable||!n||!el) return;
    if(n.name!==updated.name) {
      n.name=updated.name;
      const body=el.querySelector('.fc-node-body');
      body.title=n.name;
      body.setAttribute('aria-label','查看 '+n.name);
      body.querySelector('strong').textContent=n.name;
      for(const port of el.querySelectorAll('.fc-port-in'))
        port.setAttribute('aria-label',n.name+' '+(port.dataset.port?segmentPorts[port.dataset.port]+' ':'')+'输入端口');
      el.querySelector('.fc-port-out')?.setAttribute('aria-label',n.name+' 输出端口');
      for(const {edge,g} of edgeEls) {
        if(edge.from===n.id||edge.to===n.id) g.setAttribute('aria-label',edgeName(edge));
      }
    }
    n.config=updated.config;
    const sub=el.querySelector('.fc-node-details small');
    sub.textContent=sub.title=nodeSummary(n);
  }
  return {destroy(){resize.disconnect();handlers.abort();root.remove();},updateNodeDetails,fit:resetFit,zoomBy,getZoom:()=>zoom};
}
