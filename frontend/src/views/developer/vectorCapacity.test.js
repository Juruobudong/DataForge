import test from 'node:test'
import assert from 'node:assert/strict'
import { formatVectorCapacity } from './vectorCapacity.js'

test('vector capacity displays values when monitoring is available', () => {
  assert.equal(formatVectorCapacity({ available: true, entity_count: 12, capacity_limit: 100 }), '12 / 100')
})

test('vector capacity prefers the API reason for an unavailable profile', () => {
  assert.equal(
    formatVectorCapacity({ available: false, reason: '旧外部 Profile，不参与容量监控' }),
    '旧外部 Profile，不参与容量监控',
  )
})

test('vector capacity keeps the existing fallback when no report exists', () => {
  assert.equal(formatVectorCapacity(undefined), 'Milvus 未配置')
})
