// Only the server can resolve a dataset ID and verify its source.
export {supportsHandGraph} from './hand-graph.js';

export const datasetExecutionPlan=`<section class="dataset-report" aria-label="执行顺序">
  <strong>执行顺序</strong>
  <ul>
    <li>多条序列按队列顺序逐条处理。</li>
    <li>一条序列只有一段时，这段走完整条流水线。有多段时，首段先跑完它的提词、分割和已启用的评分等步骤。其余片段在当前算子全部处理完后，再一起进入下一步。同一批片段可复用模型。</li>
    <li>启用全视频掩码融合时，等本条序列的所有片段处理完再融合。初始分割和两次改词都未过线时，每个目标采用 ABS 均分最高一轮的掩码和提词，继续匹配前后段。低分回退及最终没有掩码的帧可在数据排队复核。</li>
  </ul>
</section>`;

export const datasetFields=`<div class="dataset-picker" hidden>
  <div class="dataset-heading"><div><strong>服务器数据集</strong><small data-root></small></div>
    <button type="button" class="linkbtn" data-refresh>刷新列表</button></div>
  ${datasetExecutionPlan}
  <label class="label">数据目录<select class="field" name="datasetRoot"><option value="yanghonglei">yanghonglei · datasets</option></select></label>
  <label class="label" data-group-label hidden>数据集<select class="field" name="datasetGroup"></select></label>
  <label class="label" data-search-label hidden>搜索序列<input type="search" class="field" data-search></label>
  <label class="label">序列<select multiple size="5" class="field sequence-list" name="datasetId" aria-label="数据集序列"></select></label>
  <small class="help">按 Ctrl 或 Shift 可多选，也可以跨目录逐条加入队列。多选默认处理整条序列。</small>
  <div class="dataset-feedback" role="status" aria-live="polite" data-status></div>
  <label class="label" data-candidate-label hidden>输入视频<select class="field" name="datasetCandidate"></select></label>
  <div data-plan hidden>
    <label class="label">处理范围<select class="field" name="datasetMode">
      <option value="">请选择处理范围</option><option value="whole">整条序列 · 按 30 fps 分段</option>
      <option value="clip">选取片段 · 最多 81 帧</option></select></label>
    <label class="label" data-start-label hidden>起始时间（秒）<input class="field" type="number" min="0" step="any" name="datasetStart" value="0"></label>
    <p class="help" data-plan-note></p>
  </div>
  <div class="dataset-report" aria-live="polite" data-report></div>
  <button class="btn" type="button" data-batch-add disabled>加入处理队列</button>
  <div class="batch-plan" data-batch hidden>
    <strong>处理队列</strong><ol data-batch-rows></ol>
    <label class="label">队列输出目录<input class="field" name="queueOut" placeholder="每条序列会写入独立子目录"></label>
    <small class="help">某条失败会暂停该批次的余下序列。</small>
  </div>
</div>`;

export function mountDatasetPicker(form,graph,onStateChange,{preset=false}={}){
  const panel=form.querySelector('.dataset-picker');
  const find=s=>panel.querySelector(s);
  const {datasetRoot:root,datasetGroup:group,datasetId:id,datasetCandidate:candidate,datasetMode:mode,datasetStart:start,out}=form.elements;
  const report=find('[data-report]'), status=find('[data-status]'), refresh=find('[data-refresh]');
  const life=new AbortController();
  let inspecting=null, catalog=null, loading=null, approved=null, suggestion=null, lastAuto='', timer;
  const rows=[], add=find('[data-batch-add]'), box=find('[data-batch]'), queueOut=form.elements.queueOut;
  let adding=false, requestId, submittedKey;
  if(preset)box.querySelector('strong').textContent='已选序列';
  function requestFor(path, choices){
    const key=JSON.stringify([path,choices]);
    if(key!==submittedKey){requestId=crypto.randomUUID();submittedKey=key;}
    return requestId;
  }
  function queueState(){
    add.disabled=!graph||adding||(!approved&&id.selectedOptions.length<2);
    root.disabled=group.disabled=adding;id.disabled=refresh.disabled=adding||Boolean(loading);
    find('[data-search]').disabled=adding;
    add.textContent=adding?'正在检查并加入…':preset?'添加到预设':'加入处理队列';
    box.hidden=!rows.length;
    out.closest('label').hidden=!panel.hidden&&rows.length>0;
    onStateChange();
  }
  function showQueue(){
    const list=find('[data-batch-rows]');list.replaceChildren();
    rows.forEach((row,i)=>{
      const li=document.createElement('li'), label=document.createElement('span');
      label.textContent=(row.selection.sourceRoot||'yanghonglei')+' / '+row.label+' · '+(row.selection.mode==='clip'?'片段 '+row.selection.start+' 秒起':'整条');
      li.append(label);
      for(const [action,title,disabled] of [['up','上移',i===0],['down','下移',i===rows.length-1],['remove','移除',false]]){
        const button=document.createElement('button');button.type='button';button.className='linkbtn';button.textContent=title;
        button.disabled=disabled||adding;button.setAttribute('aria-label',title+' '+row.label);
        button.onclick=()=>{
          if(action==='remove')rows.splice(i,1);
          else{const j=action==='up'?i-1:i+1;[rows[i],rows[j]]=[rows[j],rows[i]];}
          showQueue();
        };
        li.append(button);
      }
      list.append(li);
    });
    queueState();
  }
  async function addSelected(){
    if(adding||!graph)return;
    const options=[...id.selectedOptions].map(o=>({id:o.value,label:o.label})),key=root.value,checked=approved,approvedOut=suggestion?.path;
    const problems=[];
    adding=true;queueState();
    try{
      for(const [i,option] of options.entries()){
        if(life.signal.aborted)return;
        let item=options.length===1?checked:null, path=approvedOut;
        if(!item){
          const selection={id:option.id,mode:'whole'};
          if(key!=='yanghonglei')selection.sourceRoot=key;
          setStatus('正在检查 '+(i+1)+' / '+options.length+' · '+option.label);
          let data;
          try{data=await request('/inspect',{graph,selection},life.signal);}
          catch(error){if(life.signal.aborted)return;problems.push(option.label+'：'+error.message);continue;}
          if(!data.ready||!data.fingerprint||data.issues?.length||data.runtimeIssues?.length){
            problems.push(option.label+'：'+[...(data.issues??[]),...(data.runtimeIssues??[])].join(' '));continue;
          }
          item={selection,fingerprint:data.fingerprint,sequenceName:data.sequenceName,clipPlan:data.clipPlan};path=data.suggestedOut;
        }
        if(rows.some(r=>r.fingerprint===item.fingerprint))continue;
        if(rows.length>=200){problems.push('一个队列最多可加入 200 条序列。');break;}
        rows.push({...item,label:option.label});
        if(!queueOut.value&&path)queueOut.value=path.slice(0,path.lastIndexOf('/')+1)+'batch-'+crypto.randomUUID().slice(0,12);
      }
    }catch(error){if(!life.signal.aborted)problems.push(error.message);}
    finally{
      adding=false;
      if(!life.signal.aborted){
        id.selectedIndex=-1;resetSequence();showQueue();
        if(problems.length)detail('以下序列尚未加入，可逐条选中补充材料',problems,'dataset-issues');
        setStatus((preset?'预设已选 ':'处理队列共 ')+rows.length+' 条。');
      }
    }
  }
  add.onclick=addSelected;
  function setStatus(message){status.textContent=message;}
  function resetReport(){
    clearTimeout(timer);inspecting?.abort();inspecting=null;approved=null;suggestion=null;
    report.replaceChildren();queueState();
  }
  function resetSequence(){
    resetReport();candidate.replaceChildren();find('[data-candidate-label]').hidden=true;
    mode.value='';start.value='0';find('[data-plan]').hidden=true;find('[data-start-label]').hidden=true;
    if(lastAuto&&out.value===lastAuto)out.value=out.defaultValue;
    lastAuto='';
  }
  function showSequences(){
    const needle=find('[data-search]').value.trim().toLowerCase(), selected=new Set([...id.selectedOptions].map(o=>o.value));
    const items=catalog.filter(s=>(!group.value||s.group===group.value)&&(s.group+' '+s.label+' '+s.id).toLowerCase().includes(needle));
    const groups=new Map();
    id.replaceChildren();
    for(const item of items){
      let container=id;
      if(item.group){
        if(!groups.has(item.group)){
          const group=document.createElement('optgroup');group.label=item.group;id.append(group);groups.set(item.group,group);
        }
        container=groups.get(item.group);
      }
      container.append(new Option(item.label||item.id,item.id,false,selected.has(item.id)));
    }
    if(selected.size!==id.selectedOptions.length){resetSequence();if(id.selectedOptions.length===1)inspect();}
    if(!id.value){
      resetSequence();
      setStatus(items.length?'请选择一条序列以检查输入材料。':needle?'没有匹配的序列。':'该目录下暂时没有可列出的序列。');
    }
  }
  async function request(path,body,signal){
    const opts={cache:'no-store',signal};
    if(body)Object.assign(opts,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const res=await fetch('/api/datasets'+path,opts),data=await res.json().catch(()=>({}));
    if(!res.ok||data.error)throw Object.assign(Error(data.error||'无法访问服务器数据集（HTTP '+res.status+'）'),{roots:data.roots});
    return data;
  }
  function showRoots(roots,key){
    if(!Array.isArray(roots))return;
    root.replaceChildren(...roots.map(item=>new Option(item.label,item.id)));
    root.value=key;
  }
  async function loadCatalog(changed=false){
    loading?.abort();
    const pending=new AbortController(), key=root.value, previous=changed?'':id.value, prevGroup=changed?'':group.value;
    loading=pending;catalog=null;refresh.disabled=true;id.disabled=true;resetSequence();id.replaceChildren();
    group.replaceChildren();find('[data-group-label]').hidden=true;find('[data-search-label]').hidden=true;
    if(changed)find('[data-search]').value='';
    find('[data-root]').textContent='';setStatus('正在读取服务器目录…');
    try{
      const data=await request('?root='+encodeURIComponent(key),null,pending.signal);
      if(loading!==pending||life.signal.aborted)return;
      showRoots(data.roots,key);
      if(!Array.isArray(data.sequences))throw Error('服务器返回的序列列表格式有误。');
      catalog=data.sequences;find('[data-root]').textContent=data.root;
      const groups=[...new Set(catalog.map(item=>item.group).filter(Boolean))];
      group.replaceChildren(new Option('全部数据集',''),...groups.map(name=>new Option(name,name)));
      group.value=groups.includes(prevGroup)?prevGroup:'';
      find('[data-group-label]').hidden=groups.length<2;
      find('[data-search-label]').hidden=catalog.length<8;
      if(catalog.length<8)find('[data-search]').value='';
      showSequences();id.value=previous;
      if(id.value)inspect();
    }catch(error){
      if(loading!==pending||life.signal.aborted)return;
      showRoots(error.roots,key);setStatus(error.message);
    }finally{if(loading===pending){loading=null;refresh.disabled=false;id.disabled=false;}}
  }
  function detail(label,value,cls=''){
    const block=document.createElement('div');block.className=cls;
    const title=document.createElement('strong');title.textContent=label;block.append(title);
    if(Array.isArray(value)){
      const list=document.createElement('ul');
      for(const item of value){const li=document.createElement('li');li.textContent=item;list.append(li);}
      block.append(list);
    }else{
      const text=document.createElement('p');text.textContent=value;block.append(text);
    }
    report.append(block);
  }
  function selection(){
    const sel={id:id.value};
    if(root.value!=='yanghonglei')sel.sourceRoot=root.value;
    if(candidate.value)sel.candidate=candidate.value;
    if(!find('[data-plan]').hidden&&mode.value){
      sel.mode=mode.value;
      if(mode.value==='clip'){
        if(start.value===''||!Number.isFinite(start.valueAsNumber)||start.valueAsNumber<0)throw Error('请输入大于或等于 0 的起始秒数。');
        sel.start=start.valueAsNumber;
      }
    }
    return sel;
  }
  async function inspect(){
    resetReport();if(!graph||!id.value)return;
    let sel;try{sel=selection();}catch(error){setStatus(error.message);return;}
    const pending=new AbortController();inspecting=pending;setStatus('正在检查序列材料…');
    try{
      const data=await request('/inspect',{selection:sel,graph},pending.signal);
      if(inspecting!==pending||life.signal.aborted)return;
      candidate.replaceChildren(new Option('请选择输入视频',''));
      for(const item of data.candidates??[])candidate.append(new Option(item.label||item.id,item.id));
      candidate.value=sel.candidate??'';find('[data-candidate-label]').hidden=(data.candidates?.length??0)<2;
      find('[data-plan]').hidden=!data.needsPreparation;
      find('[data-start-label]').hidden=mode.value!=='clip';
      start.disabled=panel.hidden||!data.needsPreparation||mode.value!=='clip';
      find('[data-plan-note]').textContent=mode.value==='whole'?'确认后将按 30 fps 转换，每段最多 81 帧，各段独立提词和分割。':
        mode.value==='clip'?'确认后将从起始时间取最多 2.7 秒的 RGB 视频，按 30 fps 输入。':'整条序列会切成独立处理的短段，也可以只处理一个片段。';
      report.replaceChildren();
      if(data.source)detail('识别到的视频',data.source);
      if(data.info){
        const i=data.info;
        const dimensions=i.width&&i.height?i.width+' × '+i.height:'';
        const sec=Number.isFinite(i.duration)?Number(i.duration.toFixed(2))+' 秒':'';
        const metadata=[dimensions,i.fps?i.fps+' fps':'',i.frames?i.frames+' 帧':'',sec].filter(Boolean);
        if(metadata.length)detail('源视频规格',metadata.join(' · '));
      }
      if(data.summary)detail('输入方案',data.summary);
      else if(data.timingNote)detail('原始帧率依据',data.timingNote);
      if(data.issues?.length)detail('待补充或调整',data.issues,'dataset-issues');
      if(data.runtimeIssues?.length)detail('服务器运行条件',data.runtimeIssues,'dataset-issues');
      suggestion={path:data.suggestedOut};suggestOutput();
      if(data.ready===true&&data.fingerprint&&!data.runtimeIssues?.length&&!data.issues?.length){
        approved={selection:sel,fingerprint:data.fingerprint,sequenceName:data.sequenceName,clipPlan:data.clipPlan};
        setStatus('输入检查通过，可确认启动。');
      }else setStatus(data.ready?'输入已就绪，请先补齐服务器运行条件。':'请根据下方检查结果补全输入。');
      queueState();
    }catch(error){if(!pending.signal.aborted)setStatus(error.message);}
  }
  function suggestOutput(){
    if(!panel.hidden&&suggestion?.path&&(!out.value||out.value===out.defaultValue||out.value===lastAuto)){
      out.value=suggestion.path;lastAuto=out.value;
    }
  }
  function setActive(active){
    panel.hidden=!active;
    queueState();
    start.disabled=!active||find('[data-plan]').hidden||mode.value!=='clip';
    if(!active&&lastAuto&&out.value===lastAuto)out.value=out.defaultValue;
    if(active&&!catalog&&!loading)loadCatalog();
    else if(active)suggestOutput();
  }
  find('[data-search]').addEventListener('input',()=>{if(catalog)showSequences();});
  find('[data-search]').addEventListener('keydown',e=>{if(e.key==='Enter')e.preventDefault();});
  root.addEventListener('change',()=>loadCatalog(true));
  group.addEventListener('change',()=>{if(catalog)showSequences();});
  refresh.addEventListener('click',()=>loadCatalog());
  id.addEventListener('change',()=>{resetSequence();if(id.selectedOptions.length===1)inspect();
    else if(id.selectedOptions.length>1)setStatus('选中 '+id.selectedOptions.length+' 条。点击「'+(preset?'添加到预设':'加入处理队列')+'」将逐条检查输入。');
    queueState();});
  candidate.addEventListener('change',()=>{mode.value='';start.value='0';inspect();});
  mode.addEventListener('change',()=>{find('[data-start-label]').hidden=mode.value!=='clip';start.disabled=mode.value!=='clip';inspect();});
  start.addEventListener('input',()=>{resetReport();setStatus('正在检查起始时间…');timer=setTimeout(inspect,250);});
  start.addEventListener('change',inspect);
  form.closest('dialog').addEventListener('close',()=>{clearTimeout(timer);life.abort();loading?.abort();inspecting?.abort();},{once:true});
  return {
    setActive,
    setGraph(nextGraph){
      graph=nextGraph;
      resetReport();queueOut.value='';
      if(!panel.hidden&&id.selectedOptions.length===1&&graph)inspect();
      else if(!graph)setStatus('请先输入「找手」或「找物」，再检查序列。');
    },
    get taskLocked(){return adding||rows.length>0;},
    get ready(){return !adding&&(rows.length>0||Boolean(approved));},
    get queueLength(){return rows.length;},
    choices(){
      if(adding)throw Error('正在检查所选序列。');
      if(rows.length)return rows.map(({selection,fingerprint,sequenceName,clipPlan})=>({selection,fingerprint,sequenceName,clipPlan}));
      if(!approved||JSON.stringify(selection())!==JSON.stringify(approved.selection))throw Error('请完成所选序列的输入和运行条件检查。');
      return [approved];
    },
    payload(){
      const datasets=this.choices();
      if(rows.length){
        const path=queueOut.value.trim();
        if(!path)throw Error('请填写队列输出目录。');
        return {datasets,requestId:requestFor(path,datasets),out:path};
      }
      return {dataset:datasets[0],requestId:requestFor(out.value.trim(),datasets)};
    },
    recheck:inspect
  };
}
