export const BUILTIN_TEMPLATE_ORDER = [
  'standard-text',
  'standard-qa-question',
  'standard-qa-full',
  'standard-graph-triple',
  'standard-graph-semantic',
  'standard-multi',
]

export function normaliseTemplateOutputKey(value) {
  return value === 'graph' ? 'graph:triple' : value
}

export function templateOutputLabel(value, knowledgeTypes = []) {
  const key = normaliseTemplateOutputKey(value)
  if (key === 'graph:triple') return '三元组图谱知识'
  if (key === 'graph:semantic') return '语义图谱知识'
  return knowledgeTypes.find(item => item.code === key)?.name || key
}

export function templateOutputSummary(template, knowledgeTypes = []) {
  const outputKeys = [...new Set((template?.output_types || []).map(normaliseTemplateOutputKey))]
  if (outputKeys.length <= 1) return ''
  return `输出：${outputKeys.map(value => templateOutputLabel(value, knowledgeTypes)).join('、')}`
}

export function templateRevisionSummary(template) {
  const draft = template?.revision_status === 'draft' && template.revision != null ? `r${template.revision}` : '无'
  const published = template?.published_revision != null ? `r${template.published_revision}` : '未发布'
  return `最新草稿：${draft} · 已发布版本：${published}`
}

export function groupFlowTemplates(templates = []) {
  const order = new Map(BUILTIN_TEMPLATE_ORDER.map((code, index) => [code, index]))
  return {
    builtin: templates
      .filter(item => item.is_builtin)
      .sort((left, right) => (order.get(left.code) ?? Number.MAX_SAFE_INTEGER) - (order.get(right.code) ?? Number.MAX_SAFE_INTEGER)),
    custom: templates.filter(item => !item.is_builtin),
  }
}
