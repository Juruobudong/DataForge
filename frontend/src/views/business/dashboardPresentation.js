export function runtimeCards(overview = {}) {
  const runtime = overview.runtime || {}
  const documents = runtime.documents || {}
  const tasks = runtime.tasks || {}
  const vector = runtime.vector || {}
  const packages = runtime.packages || {}
  return [
    {
      key: 'documents',
      label: '文档库',
      value: `${documents.library_count || 0} 个文档库 · ${documents.file_count || 0} 个文件`,
      tone: 'blue',
    },
    {
      key: 'tasks',
      label: '待处理任务',
      value: `${tasks.active_count || 0} 个任务 · ${tasks.alert_count || 0} 个告警`,
      tone: tasks.alert_count ? 'red' : 'amber',
    },
    {
      key: 'vector',
      label: '向量就绪',
      value: `${vector.ready_count || 0} / ${vector.library_count || 0} 个知识库`,
      tone: vector.ready_count === vector.library_count ? 'green' : 'amber',
    },
    {
      key: 'packages',
      label: packages.label || (overview.instance_mode === 'local' ? '待处理导入任务' : '待导出发布包'),
      value: `${packages.pending_count || 0} 个`,
      tone: packages.alert_count ? 'red' : 'blue',
    },
  ]
}

const publicationLabels = {
  export: { frozen: '已冻结', building: '构建中', ready: '已就绪', failed: '失败' },
  import: { queued: '排队中', running: '导入中', waiting: '等待恢复', conflict: '待处理冲突', completed: '已完成', failed: '失败' },
}

export function publicationRows(publication = {}) {
  const labels = publicationLabels[publication.mode] || publicationLabels.export
  return Object.entries(labels).map(([status, label]) => ({
    status,
    label,
    count: publication.status_counts?.[status] || 0,
    tone: status === 'failed' ? 'red' : ['ready', 'completed'].includes(status) ? 'green' : 'amber',
  }))
}
