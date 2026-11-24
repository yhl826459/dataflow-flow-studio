import {icon} from './model.js';

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function number(value, unit='') {
  return Number.isFinite(value) ? value.toLocaleString('zh-CN') + unit : '不可用';
}

function metric(name, value, percent) {
  const line = element('div', 'gpu-metric');
  line.append(element('span', '', name), element('strong', '', value));
  if (Number.isFinite(percent)) {
    const meter = element('progress');
    meter.max = 100;
    meter.value = Math.min(100, Math.max(0, percent));
    meter.setAttribute('aria-label', name);
    line.append(meter);
  }
  return line;
}

function card(gpu) {
  const article = element('article', 'gpu-card');
  const head = element('div', 'gpu-card-head');
  head.append(element('span', 'gpu-index', 'GPU '+gpu.index), element('h3', '', gpu.name));
  const memory = Number.isFinite(gpu.memoryUsedMiB) && gpu.memoryTotalMiB > 0
    ? gpu.memoryUsedMiB / gpu.memoryTotalMiB * 100 : null;
  article.append(head, metric('GPU 利用率', number(gpu.utilization, '%'), gpu.utilization),
    metric('显存占用', number(gpu.memoryUsedMiB)+' / '+number(gpu.memoryTotalMiB, ' MiB'), memory),
    metric('温度', number(gpu.temperatureC, ' °C')));
  const processes = element('section', 'gpu-procs');
  processes.append(element('h4', '', '计算进程'));
  if (!gpu.processes?.length) {
    processes.append(element('p', '', gpu.processes ? '当前无计算进程' : '进程信息不可用'));
  } else {
    const table = element('table');
    const thead = table.createTHead().insertRow();
    for (const label of ['PID', '进程名称', '显存']) thead.append(element('th', '', label));
    const tbody = table.createTBody();
    for (const proc of gpu.processes) {
      const row = tbody.insertRow();
      const fullName = proc.name || '不可用';
      const name = element('td', 'gpu-process-name', fullName.split(/[\\/]/).pop() || fullName);
      name.title = fullName;
      row.append(element('td', '', String(proc.pid ?? '不可用')), name,
        element('td', '', number(proc.memoryMiB, ' MiB')));
    }
    processes.append(table);
  }
  article.append(processes);
  return article;
}

export function openGpuStatus() {
  const existing = document.querySelector('dialog.gpu-dialog[open]');
  if (existing) {
    existing.querySelector('[data-close]').focus();
    return;
  }
  const dialog = element('dialog', 'gpu-dialog');
  dialog.setAttribute('aria-labelledby', 'gpu-status-title');
  dialog.innerHTML = `
    <header class="dialoghead"><h2 id="gpu-status-title">${icon('cpu', 21)} 服务器 GPU</h2>
      <button class="iconbtn" type="button" data-close aria-label="关闭">${icon('close')}</button></header>
    <div class="gpu-content">
      <p class="gpu-warning" role="status" hidden></p>
      <div class="gpu-cards"></div>
    </div>
    <footer><span data-time>正在读取服务器状态…</span>
      <button class="btn" type="button" data-refresh>刷新</button></footer>`;
  document.body.append(dialog);
  const cards = dialog.querySelector('.gpu-cards');
  const warning = dialog.querySelector('.gpu-warning');
  const timestamp = dialog.querySelector('[data-time]');
  const button = dialog.querySelector('[data-refresh]');
  let request, timer, lastUpdate;
  async function update() {
    if (request || !dialog.open) return;
    clearTimeout(timer);
    request = new AbortController();
    const timeout = setTimeout(() => request?.abort(), 10000);
    button.disabled = true;
    try {
      const response = await fetch('/api/gpu', {signal:request.signal, cache:'no-store'});
      if (!response.ok) throw new Error('服务器查询失败（HTTP '+response.status+'）。');
      const data = await response.json();
      if (!data.available || !Array.isArray(data.gpus)) throw new Error(data.error || '服务器 GPU 信息不可用。');
      if (!dialog.open) return;
      cards.replaceChildren(...data.gpus.map(card));
      lastUpdate = new Date(data.updatedAt).toLocaleTimeString('zh-CN', {hour12:false});
      timestamp.textContent = '更新于 '+lastUpdate+' · 每 3 秒自动刷新';
      warning.textContent = data.processError || '';
      warning.hidden = !data.processError;
    } catch (error) {
      if (!dialog.open) return;
      const detail = error.name === 'AbortError' ? '读取超时。' :
        error instanceof TypeError ? '无法连接服务器。' : error.message;
      warning.textContent = detail;
      warning.hidden = false;
      timestamp.textContent = lastUpdate ? '数据已过期 · 上次更新 '+lastUpdate : '尚未取得数据';
    } finally {
      clearTimeout(timeout);
      request = null;
      button.disabled = false;
      if (dialog.open) timer = setTimeout(update, 3000);
    }
  }
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  button.onclick = update;
  dialog.addEventListener('close', () => {
    clearTimeout(timer);
    request?.abort();
    dialog.remove();
  }, {once:true});
  dialog.showModal();
  update();
}
