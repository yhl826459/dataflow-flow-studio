export function absPanel(run, esc, opened) {
  const scores = run?.absScores ?? [];
  if (!scores.length) return '';
  const rows = [];
  for (const group of scores) {
    for (const target of group.targets) {
      const part = group.part == null ? group.sequence : '第 '+group.part+' 段';
      const round = group.round === 0 ? '初始轮' : '第 '+group.round+' 次改词后';
      const key = JSON.stringify([run.id,group.sequence,group.round,target.target]);
      const mean = Number.isFinite(target.mean) ? target.mean.toFixed(2) : 'N/A';
      const detail = (target.samples ?? []).map(([frame,score]) =>
        '帧 '+((group.firstFrame ?? 0)+frame)+' · '+(score ?? 'N/A')).join('　');
      rows.push(`<details class="queue-clips" data-clip-key="${esc(key)}" data-clip-output="${esc(key)}" ${opened.has(key)?'open':''}>
        <summary>${esc(part)} · ${esc(round)} · ${esc(target.target)} · ${target.error?esc(target.error):'ABS 均分 '+mean}</summary>
        <p>${esc(detail)}</p></details>`);
    }
  }
  return `<section class="events"><div class="boxhead"><strong>ABS 评分</strong><span class="caption">展开查看逐帧分数</span></div>${rows.join('')}</section>`;
}
