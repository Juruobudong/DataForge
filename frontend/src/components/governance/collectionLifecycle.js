export function defaultCollectionName(code) {
  const normalized = String(code || '').trim().replace(/-/g, '_').replace(/[^A-Za-z0-9_]/g, '_')
  return normalized ? `dataforge_${normalized}_knowledge` : ''
}

export function managedCollectionCanRequestDelete(collection) {
  return ['ready', 'delete_failed'].includes(collection?.status)
}
