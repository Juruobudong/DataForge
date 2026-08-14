const STATUS = {
  queued: { label: '排队中', tone: 'amber' },
  running: { label: '处理中', tone: 'blue' },
  completed: { label: '已完成', tone: 'green' },
  completed_with_warnings: { label: '已完成（有警告）', tone: 'amber' },
  failed: { label: '失败', tone: 'red' },
  cancelled: { label: '已停止', tone: 'muted' },
}

const STAGE = {
  queued: '等待处理',
  processing: '知识生成',
  completed: '已完成',
  completed_with_warnings: '已完成（有警告）',
  failed: '失败',
  cancelled: '已停止',
}

export function presentStatus(status) {
  return STATUS[status] || { label: '未知状态', tone: 'muted' }
}

export function presentStage(stage) {
  return STAGE[stage] || '处理中'
}

export function shortTechnicalId(value, length = 15) {
  const id = String(value || '')
  return id.length > length ? `${id.slice(0, length)}…` : id
}

export function fallbackSinkIds(job) {
  return [...new Set(Object.values(job.sink_library_ids || job.output_library_ids || {}).filter(Boolean))]
}
