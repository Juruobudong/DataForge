import assert from 'node:assert/strict'
import test from 'node:test'
import { canApproveDocument, documentProductionStage } from './documentReviewModel.js'

test('document review keeps preparation and review stages distinct', () => {
  assert.equal(documentProductionStage({ version: { preparation_status: 'running' } }), '解析与分块中')
  assert.equal(documentProductionStage({ version: { preparation_status: 'completed', review_status: 'pending' } }), '待审核')
  assert.equal(documentProductionStage({ version: { preparation_status: 'completed', review_status: 'approved' } }), '审核通过')
})

test('document review never offers approval before preparation or with rejected chunks', () => {
  assert.equal(canApproveDocument({ preparation_status: 'running', counts: { total: 2, rejected: 0 } }), false)
  assert.equal(canApproveDocument({ preparation_status: 'completed', counts: { total: 2, rejected: 1 } }), false)
  assert.equal(canApproveDocument({ preparation_status: 'completed', counts: { total: 2, rejected: 0 } }), true)
})
