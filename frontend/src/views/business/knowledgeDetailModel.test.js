import assert from 'node:assert/strict'
import test from 'node:test'
import {
  QA_PAGE_SIZE, normalizeQaFilters, qaApiQuery, qaPageCount, qaRouteQuery, qaStatusLabel, resetQaFilters,
} from './knowledgeDetailModel.js'

test('QA filters default to active page one and normalize invalid URL state', () => {
  assert.deepEqual(normalizeQaFilters(), { q: '', status: 'active', page: 1 })
  assert.deepEqual(normalizeQaFilters({ q: '  光疗  ', status: 'unknown', page: '-2' }), {
    q: '光疗', status: 'active', page: 1,
  })
})

test('QA route and API query preserve search status and fixed page size', () => {
  const filters = { q: '胆红素', status: 'inactive', page: 3 }
  assert.deepEqual(qaRouteQuery(filters), { q: '胆红素', status: 'inactive', page: '3' })
  assert.deepEqual(qaApiQuery(filters), { q: '胆红素', status: 'inactive', page: 3, page_size: QA_PAGE_SIZE })
})

test('changing a QA filter resets pagination and labels statuses for display', () => {
  assert.deepEqual(resetQaFilters({ q: '', status: 'active', page: 4 }, { status: 'all' }), {
    q: '', status: 'all', page: 1,
  })
  assert.equal(qaPageCount(0), 1)
  assert.equal(qaPageCount(101), 3)
  assert.equal(qaStatusLabel('active'), '有效')
  assert.equal(qaStatusLabel('inactive'), '已失效')
})
