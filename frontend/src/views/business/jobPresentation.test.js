import test from 'node:test'
import assert from 'node:assert/strict'
import { fallbackSinkIds, presentStage, presentStatus, shortTechnicalId } from './jobPresentation.js'

test('job presentation localizes known states and safely handles unknown values', () => {
  assert.deepEqual(presentStatus('completed'), { label: '已完成', tone: 'green' })
  assert.deepEqual(presentStatus('other'), { label: '未知状态', tone: 'muted' })
  assert.equal(presentStage('processing'), '知识生成')
  assert.equal(presentStage('other'), '处理中')
})

test('job presentation keeps IDs copyable while rendering a compact fallback', () => {
  assert.equal(shortTechnicalId('kj_123456789012345678'), 'kj_123456789012…')
  assert.deepEqual(fallbackSinkIds({ sink_library_ids: { text: 'kl_a', qa: 'kl_a', graph: 'kl_b' } }), ['kl_a', 'kl_b'])
})
