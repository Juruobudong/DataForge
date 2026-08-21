import test from 'node:test'
import assert from 'node:assert/strict'

import {
  compatibleProfilesForTask,
  qaEmbeddingMode,
  routingPublishReadiness,
} from './projectPublishingModel.js'

test('任务只显示当前知识类型绑定的兼容 Profile', () => {
  const types = [
    { status: 'active', code: 'qa', index_profiles: [
      { id: 'question', code: 'qa-question' },
      { id: 'full', code: 'qa-full' },
    ] },
    { status: 'active', code: 'text', index_profiles: [{ id: 'text', code: 'text-default' }] },
  ]
  assert.deepEqual(
    compatibleProfilesForTask({ knowledge_type: 'qa' }, types).map(item => item.id),
    ['question', 'full'],
  )
  assert.deepEqual(
    compatibleProfilesForTask({ knowledge_type: 'qa' }, types, true).map(item => item.id),
    ['question'],
  )
})

test('QA Profile 自动映射 embedding 模式', () => {
  assert.equal(qaEmbeddingMode({ code: 'qa-question' }), 'question')
  assert.equal(qaEmbeddingMode({ code: 'qa-full' }), 'full')
  assert.equal(qaEmbeddingMode({ code: 'text-default' }), null)
})

test('发布门禁要求任务、授权和当前阶段 Target', () => {
  assert.deepEqual(routingPublishReadiness([], [], '').problems, [
    '请先配置并启用 Deployment Task',
    '请先完成知识授权',
    '当前阶段尚未配置 Milvus Target',
  ])
  assert.equal(routingPublishReadiness(
    [{ enabled: true }],
    [{ enabled: true, knowledge_library_ids: ['library-1'] }],
    'http://milvus:19531',
  ).ready, true)
})
