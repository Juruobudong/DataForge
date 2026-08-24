export const expensiveComponents = new Set(['mineru', 'llm', 'embedding'])
export const componentTone = status => ({ healthy: 'green', degraded: 'amber', unavailable: 'red' })[status] || 'blue'
export function componentAge(item = {}) {
  if (item.age_seconds == null) return '尚未检查'
  if (item.stale) return `结果已过期 · ${Math.round(item.age_seconds / 60)} 分钟前`
  return item.age_seconds < 60 ? `${Math.round(item.age_seconds)} 秒前` : `${Math.round(item.age_seconds / 60)} 分钟前`
}
export const needsRealCallConfirmation = values => values.some(value => expensiveComponents.has(value))
