export const QA_PAGE_SIZE = 50

export function normalizeQaStatus(value) {
  return ['active', 'inactive', 'all'].includes(value) ? value : 'active'
}

export function normalizeQaPage(value) {
  const page = Number.parseInt(String(value || ''), 10)
  return Number.isFinite(page) && page > 0 ? page : 1
}

export function normalizeQaFilters(query = {}) {
  return {
    q: String(query.q || '').trim(),
    status: normalizeQaStatus(query.status),
    page: normalizeQaPage(query.page),
  }
}

export function resetQaFilters(current = {}, changes = {}) {
  return normalizeQaFilters({ ...current, ...changes, page: 1 })
}

export function qaRouteQuery(filters = {}) {
  const normalized = normalizeQaFilters(filters)
  return {
    ...(normalized.q ? { q: normalized.q } : {}),
    status: normalized.status,
    page: String(normalized.page),
  }
}

export function qaApiQuery(filters = {}) {
  const normalized = normalizeQaFilters(filters)
  return {
    q: normalized.q,
    status: normalized.status,
    page: normalized.page,
    page_size: QA_PAGE_SIZE,
  }
}

export function qaPageCount(total, pageSize = QA_PAGE_SIZE) {
  return Math.max(1, Math.ceil(Math.max(0, Number(total) || 0) / pageSize))
}

export function qaStatusLabel(status) {
  return status === 'inactive' ? '已失效' : status === 'active' ? '有效' : status
}
