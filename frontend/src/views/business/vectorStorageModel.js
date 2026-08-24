export const VECTOR_STATUS_LABELS = {
  USING: '使用中', PENDING: '待采用', HISTORY: '历史版本', GC_ELIGIBLE: '可清理',
  INCONSISTENT: '数据异常', UNMANAGED: '未托管',
}

export const VECTOR_STATUS_CLASSES = {
  USING: 'green', PENDING: 'blue', HISTORY: '', GC_ELIGIBLE: 'amber',
  INCONSISTENT: 'red', UNMANAGED: 'amber',
}

export const KNOWLEDGE_TYPE_LABELS = {
  text: '文本', qa: '问答', 'graph:triple': '三元组图谱', 'graph:semantic': '语义图谱',
}

export function vectorStatusLabel(value) { return VECTOR_STATUS_LABELS[value] || value || '—' }
export function vectorStatusClass(value) { return VECTOR_STATUS_CLASSES[value] || '' }
export function knowledgeTypeLabel(value) { return KNOWLEDGE_TYPE_LABELS[value] || value || '未知' }
export function formatInventoryCount(value) {
  return value === null || value === undefined ? '—' : Number(value).toLocaleString('zh-CN')
}
export function sortCollections(values = []) {
  return [...values].sort((left, right) => Number(right.managed) - Number(left.managed)
    || String(left.collection_name).localeCompare(String(right.collection_name), 'zh-CN'))
}
export function sortPartitions(values = []) {
  return [...values].sort((left, right) => {
    const leftName = left.knowledge_library_name || '\uffff'
    const rightName = right.knowledge_library_name || '\uffff'
    return leftName.localeCompare(rightName, 'zh-CN')
      || Number(right.asset_version_no || -1) - Number(left.asset_version_no || -1)
      || String(left.partition_name).localeCompare(String(right.partition_name), 'zh-CN')
  })
}
export function countDifference(partition = {}) {
  if (partition.actual_count === null || partition.actual_count === undefined
      || partition.expected_count === null || partition.expected_count === undefined) return null
  return Number(partition.actual_count) - Number(partition.expected_count)
}
export function routingReferenceSummary(partition = {}) {
  const refs = partition.routing_refs || []
  return {
    count: refs.length,
    projects: [...new Set(refs.map(item => item.project_name || item.project_code).filter(Boolean))],
  }
}
