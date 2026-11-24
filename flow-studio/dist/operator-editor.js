// Source and Agent replies are text until the user explicitly saves a revision.
export function openOperatorEditor({modal, spec, kind, scope, modelUsage, onSave}) {
  const editingNode = scope === 'node';
  const derived = scope === 'builtin';
  const records = kind === 'records';
  const title = editingNode ? '编辑节点代码' : scope === 'new' ? '设计 Python 算子' : '编辑算子代码';
  const saveLabel = editingNode ? '保存到当前节点' : derived ? '更新库中此算子' : '保存到算子库';
  const scopeNote = editingNode ? '保存后，此节点使用新代码。' :
    derived ? '更新后，从这张库卡拖入的新节点会使用新代码。已有节点保留原版本。' :
    '拖入画布时会复制当时的代码。可以点已有节点单独编辑它的版本。';
  const body = `<section class="code-workbench">
    <div class="code-meta"><label class="label">算子名称<input class="field" name="opName" maxlength="80" required></label>
    <div class="code-kind">${records ? 'JSONL 记录算子' : '手部分割 · 服务器算子'}</div></div>
    <label class="label">运行时调用模型<input class="field" name="opModel" maxlength="2000" placeholder="填写模型名及调用方式，无模型请填「未调用模型」" required></label>
    ${derived?'<label class="label">保存方式<select class="field" name="opSaveMode"><option value="update">更新库中此算子</option><option value="copy">另存为新算子</option></select></label>':''}
    <p class="help">${scopeNote}</p>
    <div class="code-layout"><section class="code-main">
    <label class="label" for="op-source">Python 源码</label><div id="op-origin" class="code-origin"></div>
    <textarea id="op-source" class="code-area" rows="15" spellcheck="false" autocapitalize="off" autocomplete="off" required></textarea>
    <div class="code-tools"><button type="button" class="btn" data-code="validate">检查代码</button>
    <button type="button" class="linkbtn" data-code="restore">载入内置源码</button><output id="code-check" aria-live="polite"></output></div>
    ${records ? `<details class="code-sample"><summary>用样例试跑</summary>
      <label class="label">输入 JSONL<textarea class="field" name="opExample" rows="2" spellcheck="false">{"text":"  DataFlow  "}</textarea></label>
      <button type="button" class="btn" data-code="test">运行样例</button>
      <pre class="code-output" id="op-result" aria-live="polite" hidden></pre></details>` : ''}
    </section><section class="code-agent" aria-labelledby="op-agent-heading">
    <h3 id="op-agent-heading">Agent 设计</h3><p id="op-agent-status" class="help" aria-live="polite">正在检查服务…</p>
    <details id="op-agent-setup" hidden><summary>在 amax 启用 ABS 连接</summary>
      <p class="help">在已加载 ABS 密钥的服务器终端运行以下命令，再重新打开编辑器。</p>
      <pre class="code-output"></pre></details>
    <label class="label">设计需求<textarea class="field" name="opPrompt" rows="4" maxlength="4000" placeholder="${records?'例如：读取 text，去除首尾空格后写入 clean_text，保留原字段。':'说明要改哪段逻辑、读取什么输入，以及下游需要什么输出。'}"></textarea></label>
    <button type="button" class="btn" data-code="generate" disabled>生成代码草案</button>
    <p id="op-agent-progress" class="help" aria-live="polite"></p>
    <div id="op-candidate" hidden><label class="label">Agent 草案<textarea class="code-area" rows="8" readonly spellcheck="false"></textarea></label>
    <button type="button" class="btn" data-code="adopt">用草案替换编辑区</button></div>
    </section></div></section>`;

  let baseHash=spec.implementation?.baseHash, baseline, ready=false, agentReady=false, generating=false;
  const controller=new AbortController();
  modal(title, body, saveLabel, async () => {
    if(!ready) throw Error('请先载入算子源码。');
    const name=form.elements.opName.value.trim();
    if(!name) throw Error('请输入算子名称。');
    const model=form.elements.opModel.value.trim();
    if(!model) throw Error('请填写运行时调用的模型，或填写「未调用模型」。');
    const code=source.value;
    const implementation={kind,baseHash,code};
    await request('validate',{type:spec.type,implementation});
    if(!isOpen()) throw Error('编辑器已关闭。');
    if(code!==source.value||name!==form.elements.opName.value.trim()||model!==form.elements.opModel.value.trim()) throw Error('校验期间内容已有改动，请再次保存。');
    onSave(name,implementation,form.elements.opSaveMode?.value,model);
  });
  const dlg=document.querySelector('#dialog'),form=dlg.querySelector('form');
  const find=selector=>form.querySelector(selector);
  const source=find('#op-source'), origin=find('#op-origin'), check=find('#code-check');
  const generate=find('[data-code="generate"]'), agentStatus=find('#op-agent-status'), progress=find('#op-agent-progress');
  const isOpen=()=>dlg.open&&form.isConnected;
  const owns=spec.implementation!=null;
  form.elements.opName.value=spec.name;
  form.elements.opModel.value=modelUsage.startsWith('自定义代码的模型用法待确认')?'':modelUsage;
  if(derived)form.elements.opSaveMode.addEventListener('change',()=>{
    const copy=form.elements.opSaveMode.value==='copy';
    find('[type="submit"]').textContent=copy?'另存为新算子':saveLabel;
    if(copy&&form.elements.opName.value===spec.name)form.elements.opName.value=spec.name+' · 副本';
    if(!copy&&form.elements.opName.value===spec.name+' · 副本')form.elements.opName.value=spec.name;
  });
  form.dataset.operatorEditor='true';
  source.value=owns?spec.implementation.code:'';
  source.readOnly=!owns;
  dlg.addEventListener('close',()=>controller.abort(),{once:true});
  async function request(endpoint, data) {
    const options=data===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)};
    const res=await fetch('/api/operators/'+endpoint,{...options,cache:'no-store',signal:controller.signal});
    const answer=await res.json().catch(()=>({}));
    if(!res.ok)throw Error(answer.error??'工作台接口未完成请求（HTTP '+res.status+'）。');
    return answer;
  }
  function buttons(){
    find('[type="submit"]').disabled=!ready;
    find('[data-code="validate"]').disabled=!ready;
    find('[data-code="restore"]').disabled=!ready;
    if(records)find('[data-code="test"]').disabled=!ready;
    generate.disabled=!agentReady||!ready||generating;
  }
  buttons();
  request('source?'+new URLSearchParams({type:spec.type,kind})).then(answer=>{
    if(!isOpen())return;
    if(typeof answer.code!=='string'||!answer.baseHash)throw Error('服务未返回源码和版本标识。');
    baseline=answer;
    if(!owns){baseHash=answer.baseHash;source.value=answer.code;}
    ready=true;source.readOnly=false;
    origin.textContent=answer.origin??'DataFlow';
    if(owns&&answer.baseHash!==baseHash)check.textContent='服务器基础源码已有更新，请检查当前版本兼容性。';
    buttons();
  }).catch(error=>{if(isOpen())origin.textContent='源码加载失败：'+error.message;});
  request('agent').then(answer=>{
    if(!isOpen())return;
    agentReady=answer.ready===true;
    agentStatus.textContent=answer.detail||(agentReady?'DataFlow Writer 已配置。':'服务器尚未配置 Agent。');
    if(answer.installed&&!agentReady&&answer.source==='abs'&&/^[A-Za-z_][A-Za-z_0-9]*$/.test(answer.key_env||'')){
      const setup=find('#op-agent-setup');
      setup.hidden=false;
      setup.querySelector('pre').textContent='systemctl --user import-environment '+answer.key_env+'\nsystemctl --user restart flow-studio.service';
    }
    buttons();
  }).catch(error=>{if(isOpen())agentStatus.textContent=error.message;});
  source.addEventListener('input',()=>{
    check.textContent='';
    if(records)find('#op-result').hidden=true;
  });
  form.addEventListener('click',async event=>{
    const btn=event.target.closest('[data-code]');
    if(!btn)return;
    const action=btn.dataset.code;
    if(action==='restore'){
      source.value=baseline.code;baseHash=baseline.baseHash;
      source.dispatchEvent(new Event('input'));check.textContent='已将内置源码载入编辑区。';
      source.focus();return;
    }
    if(action==='adopt'){
      source.value=find('#op-candidate textarea').value;
      source.dispatchEvent(new Event('input'));
      find('#op-candidate').hidden=true;
      progress.textContent='草案已放入编辑区，可继续修改并检查。';
      source.focus();
      return;
    }
    const code=source.value, implementation={kind,code,baseHash};
    const output=action==='generate'?progress:action==='validate'?check:find('#op-result');
    if(action==='generate'){
      if(!form.elements.opPrompt.value.trim()){form.elements.opPrompt.focus();progress.textContent='请填写设计需求。';return;}
      generating=true;
    }
    btn.disabled=true;output.hidden=false;
    output.textContent=action==='generate'?'Agent 正在生成草案…':action==='validate'?'正在检查…':'正在运行样例…';
    try{
      if(action==='generate'){
        const result=await request('design',{type:spec.type,implementation,prompt:form.elements.opPrompt.value.trim()});
        if(!isOpen())return;
        if(!result.code||typeof result.code!=='string')throw Error('Agent 没有返回代码。');
        find('#op-candidate textarea').value=result.code;find('#op-candidate').hidden=false;
        output.textContent='草案已就绪，审阅后可放入编辑区。';
      }else if(action==='validate'){
        await request('validate',{type:spec.type,implementation});
        if(isOpen()&&code===source.value)output.textContent='代码检查通过。';
      }else{
        const rows=form.elements.opExample.value.split(/\r?\n/).filter(line=>line.trim()).map((line,index)=>{
          try{return JSON.parse(line);}catch{throw Error('第 '+(index+1)+' 行 JSON 格式有误。');}
        });
        const result=await request('run',{code,records:rows,config:spec.config??{}});
        if(isOpen()&&code===source.value)output.textContent=result.records.length?result.records.map(row=>JSON.stringify(row)).join('\n'):'输出 0 条记录。';
      }
    }catch(error){if(isOpen())output.textContent=error.message;}
    finally{if(isOpen()){if(action==='generate')generating=false;btn.disabled=action==='generate'&&!agentReady;}}
  });
}
