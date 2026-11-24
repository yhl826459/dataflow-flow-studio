// The same module is the worker entry point, so a large graph never blocks Cancel.
if (typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope) {
  self.onmessage = async ({data}) => {
    try {
      const {reviewGraph} = await import('./review-logic.js');
      self.postMessage({result: reviewGraph(data)});
    } catch (error) {
      self.postMessage({error: error.message});
    }
  };
}

const preferenceKey = 'flow-studio.auto-review';
const active = job => job?.status === 'queued' || job?.status === 'running';

function checkStructure(graph, signal) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./graph-review.js', import.meta.url), {type:'module'});
    function finish(callback, value) {
      signal.removeEventListener('abort', abort);
      worker.terminate();
      callback(value);
    }
    function abort() { finish(reject, signal.reason); }
    signal.addEventListener('abort', abort, {once:true});
    worker.onmessage = ({data}) => data.error
      ? finish(reject, new Error(data.error)) : finish(resolve, data.result);
    worker.onerror = () => finish(reject, new Error('编排审查模块未能载入，请刷新页面后重试。'));
    try { worker.postMessage(graph); } catch (error) { finish(reject, error); }
  });
}

export function createGraphReviews({changed, directServer}) {
  const jobs = new Map();
  let enabled = true;
  try { enabled = localStorage.getItem(preferenceKey) !== 'off'; } catch {}

  function cancel(job) {
    if (!active(job)) return;
    clearTimeout(job.timer);
    job.status = 'cancelled';
    job.controller.abort();
  }

  function setEnabled(value, persist = true) {
    enabled = value;
    if (persist) {
      try { localStorage.setItem(preferenceKey, value ? 'on' : 'off'); } catch {}
    }
    if (!enabled) for (const job of jobs.values()) if (job.automatic) cancel(job);
    changed();
  }
  addEventListener('storage', event => {
    if (event.key === preferenceKey || event.key === null) setEnabled(event.newValue !== 'off', false);
  });

  async function run(job) {
    const {graph, controller:{signal}} = job;
    job.status = 'running';
    job.progress = '正在检查节点、连线、分支和执行模式…';
    changed();
    try {
      const result = await checkStructure(graph, signal);
      if (signal.aborted) return;
      job.findings = result.findings;
      job.modes = result.modes;
      const custom = graph.nodes.filter(node => node.implementation);
      if (custom.length && !directServer()) {
        job.coverage = '请连接 amax 后复查 ' + custom.length + ' 个自定义代码节点。';
        job.status = 'partial';
      } else {
        let checked = 0;
        for (const node of custom) {
          job.progress = '正在检查自定义代码 ' + (checked + 1) + '/' + custom.length + ' · ' + node.name;
          changed();
          const response = await fetch('/api/operators/validate', {
            method:'POST', headers:{'Content-Type':'application/json'}, signal,
            body:JSON.stringify({type:node.type, implementation:node.implementation})
          });
          const answer = await response.json();
          if (signal.aborted) return;
          if (response.status === 400 && answer.error) {
            job.findings.push({level:'error', scope:node.implementation.kind === 'native' ? 'live' : 'records',
              nodeId:node.id, message:answer.error});
          } else if (!response.ok || answer.ok !== true) {
            throw new Error(answer.error || '代码校验服务未返回有效结果（HTTP ' + response.status + '）。');
          }
          checked++;
        }
        job.coverage = custom.length ? '已检查 ' + checked + ' 个自定义代码节点。' : '';
        job.status = 'done';
      }
    } catch (error) {
      if (signal.aborted) return;
      job.coverage = (job.modes ? '代码检查未完成。' : '编排检查未完成。') + (error.message || '请重试。');
      job.status = 'partial';
    }
    changed();
  }

  return {
    get enabled() { return enabled; }, setEnabled,
    get(graph) { return jobs.get(graph.id); },
    start(graph, automatic) {
      if (automatic && !enabled) return;
      cancel(jobs.get(graph.id));
      const job = {graph, automatic, controller:new AbortController(), status:'queued', findings:[]};
      jobs.set(graph.id, job);
      if (automatic) {
        job.timer = setTimeout(() => run(job), 3000);
        changed();
      } else {
        void run(job);
      }
    },
    cancel(id) { cancel(jobs.get(id)); changed(); },
    reconcile(graphs) {
      // Synchronising the library can replace a graph even when its version number matches.
      const saved = new Map(graphs.map(g => [g.id, g]));
      for (const [id, job] of jobs) {
        const current = saved.get(id);
        if (current === job.graph) continue;
        if (current && JSON.stringify(current) === JSON.stringify(job.graph)) {
          job.graph = current;
        } else {
          cancel(job);
          jobs.delete(id);
        }
      }
    }
  };
}
