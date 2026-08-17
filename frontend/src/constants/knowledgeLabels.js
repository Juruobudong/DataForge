// 统一中文 Label Registry：业务页面不得直接显示英文 code。
export const graphModeLabels = {
  triple: '三元组图谱',
  semantic: '语义图谱',
}

export const objectKindLabels = {
  entity: '实体',
  literal: '字面值',
}

export const literalDatatypeLabels = {
  number: '数值',
  range: '数值范围',
  percentage: '百分比',
  duration: '时长',
  temperature: '温度',
  dosage: '剂量',
  date: '日期',
  boolean: '布尔值',
  string: '文本值',
}

export function graphModeLabel(value) {
  return graphModeLabels[value] || value || ''
}

export function objectKindLabel(value) {
  return objectKindLabels[value] || value || ''
}

export function literalDatatypeLabel(value) {
  return literalDatatypeLabels[value] || value || ''
}
