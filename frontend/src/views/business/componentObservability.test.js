import test from 'node:test'
import assert from 'node:assert/strict'
import { componentAge, componentTone, needsRealCallConfirmation } from './componentObservability.js'

test('component observability presents status, stale age and real-call warning', () => {
  assert.equal(componentTone('healthy'), 'green')
  assert.equal(componentTone('unavailable'), 'red')
  assert.equal(componentAge({ age_seconds: 901, stale: true }), '结果已过期 · 15 分钟前')
  assert.equal(needsRealCallConfirmation(['mysql', 'disk']), false)
  assert.equal(needsRealCallConfirmation(['mysql', 'llm']), true)
})
