import {types} from './model.js';
import {canProcessRecords} from './records.js';

export const summaryFields = [['input','输入'],['logic','处理逻辑'],['model','调用模型'],['output','输出']];

const nativeModels = {
  qwen: '使用 Qwen3.8-27B-FP8，在 amax 本地从视频帧提取和复核目标短语。',
  refine: '使用 Qwen3.8-27B-FP8，在 amax 本地改写低分目标的提示词。',
  segment: '使用 GenCeption 14B，在 amax 本地分割；目标短语由 UMT5-XXL 编码。',
  score: '通过 ABS 的评分 API 调用 gpt-6.2-sol。'
};

export function nodeSummary(node,graph,includeEdits=true){
  const edited=node.implementation;
  const summary=operatorSummary(edited?.kind==='records'?'python':node.type,node.config,graph);
  summary.model=nativeModels[node.type]??'未调用模型。';
  if(edited){
    if(edited.kind==='native')summary.logic='按此节点保存的服务器 Python 代码处理。可打开编辑器查看实现。';
    summary.model='自定义代码的模型用法待确认，可编辑此项填写。';
  }
  if(includeEdits){
    for(const [key] of summaryFields){
      if(typeof node.notes?.[key]==='string'&&node.notes[key].trim())summary[key]=node.notes[key];
    }
  }
  return summary;
}

export function operatorSummary(type, config = {}, graph) {
  const fields = types[type].fields;
  const values = Object.fromEntries(fields.map(f => [f.key, config?.[f.key] ?? f.default]));
  const records = graph && canProcessRecords(graph);
  const target = graph?.nodes.find(n => n.type === 'input')?.config?.task === 'objects'
    ? '被手操作的物体' : '与物体交互的手';

  switch (type) {
    case 'python':
      return {
        input: '上一步传入的 JSONL 记录列表。',
        logic: '调用当前版本的 Python 算子处理记录。',
        output: '算子通过 storage.write 写出的记录。'
      };
    case 'input':
      if (records) return {
        input: 'JSONL 文本，每行一个 JSON 对象。',
        logic: '跳过空行，逐行检查并读取记录。',
        output: '保留原有字段的记录列表。'
      };
      return {
        input: '视频清单，每行含序列 ID、视频路径和目标任务。',
        logic: '检查序列 ID 和视频，读取尺寸、帧数与帧率。',
        output: `带有视频信息（video_info）的${values.task === 'objects' ? '物体' : '手部'}任务记录。`
      };
    case 'qwen':
      return {
        input: 'RGB 视频和清单中指定的目标任务。',
        logic: `每秒抽取 ${values.sample_fps} 帧，让 Qwen 查找${target}并复核描述。启动时可以为本次运行切换目标。${values.sample_fps === 5 ? '' : '服务器固定为每秒 5 帧。'}`,
        output: '目标列表（targets），每个目标有一个 ID 和描述短语。'
      };
    case 'segment':
      return {
        input: 'RGB 视频和每个目标的描述，重试时使用改写后的描述。',
        logic: '按目标分别处理视频，在每帧标出属于该目标的像素。',
        output: '各目标的二值掩码（.npy）和预览视频（.mp4）。'
      };
    case 'score':
      return {
        input: 'RGB 视频、原始目标描述和对应的候选掩码。',
        logic: `每隔 ${values.every} 帧取样，根据局部图像和原描述评价掩码质量。`,
        output: '各目标的逐帧评分及评分文件路径（absolute）。'
      };
    case 'gate':
      return {
        input: '每个目标的候选掩码和逐帧 ABS 评分。',
        logic: `按首轮可评帧算均分，超过 ${values.threshold} 分通过，否则最多重试 ${values.maxRetries} 次。${values.maxRetries === 2 ? '' : '服务器固定最多 2 次。'}`,
        output: '逐目标给出通过、重试或待复核的结果，缺少可比评分也转待复核。'
      };
    case 'refine':
      return {
        input: '待重试目标的低分帧、掩码、评分和历轮描述。',
        logic: '对照原图和掩码，改写同一个手或物体的描述，沿用原描述评分。找物时还会提示 Qwen 保留物体类别和可靠的外观特征。',
        output: '新的目标描述（revisions）送回分割，重复的描述转待复核。'
      };
    case 'transform': {
      const operation = fields.find(f => f.key === 'operation').options.find(o => o.value === values.operation);
      return {
        input: `每条记录的「${values.sourceKey}」字段。`,
        logic: `对字段值执行「${operation?.label ?? values.operation}」。`,
        output: `将结果写入「${values.targetKey}」字段，保留其余字段。`
      };
    }
    case 'filter': {
      const conditions = {
        nonempty: '检查字段文本，保留去掉首尾空白后仍有内容的记录。',
        equals: `只保留字段文本等于「${values.value}」的记录。`,
        contains: `只保留字段文本包含「${values.value}」的记录。`
      };
      return {
        input: `逐条读取记录的「${values.key}」字段。`,
        logic: conditions[values.rule] ?? '当前过滤条件无效，执行时将报错。',
        output: '符合条件的原始记录，字段和顺序均保留。'
      };
    }
    case 'fusion':
      return {
        input: '同一序列的片段帧号及逐实例二值掩码。有质量门控时使用通过的最终掩码。',
        logic: '在相邻窗口的 8 帧共享区做一对一实例匹配。按轨迹 ID 拼接轮廓，有歧义的接缝留待复核。',
        output: 'whole_video_masks 内的全序列逐实例 NPY 掩码及 tracks.json。'
      };
    case 'output':
      if (records) return {
        input: '上一步传来的记录列表。',
        logic: '按当前顺序收集处理后的记录。',
        output: '可从运行监控页下载的 JSONL 文件。'
      };
      if (graph?.nodes.some(n => n.type === 'fusion')) return {
        input: '全视频掩码融合输出的实例轨迹及接缝状态。',
        logic: '保留段级产物和整段产物，待复核接缝显示在数据排队。',
        output: '全序列掩码和 tracks.json 位于输出目录的 whole_video_masks。'
      };
      if (graph?.nodes.some(n => n.type === 'gate')) return {
        input: '质量门控给出的逐目标状态和保留的候选掩码。',
        logic: '按序列汇总通过的最终掩码和待复核的候选。',
        output: '结果清单 final_masks.jsonl，记录各目标的状态和掩码路径。'
      };
      return {
        input: '各目标的分割记录，以及流程中已有的 ABS 评分。',
        logic: '按序列汇总各目标的掩码路径和已有结果。',
        output: '包含分割结果的 JSONL 清单，可据其中的路径查看掩码。'
      };
  }
}
