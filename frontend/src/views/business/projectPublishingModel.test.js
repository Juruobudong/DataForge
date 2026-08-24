import test from 'node:test'
import assert from 'node:assert/strict'

import {
  compatibleProfilesForTask,
  qaEmbeddingMode,
  routingPublishReadiness,
  routingValidationView,
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

test('Routing 校验分项保留 blocked、Expected/Observed 与 deferred 语义', () => {
  const result = routingValidationView({
    valid: false,
    blocked: 1,
    target_validation: {
      mode: 'deferred_to_local',
      reason: '中心不连接机构现场 Milvus',
    },
    checks: [{
      code: 'COLLECTION.DIMENSION_MISMATCH', status: 'blocked',
      subject: { collection_name: 'dataforge_qa_full', partition_name: 'kl_faq__v3' },
      expected: 768, observed: 1024, message: '维度不一致',
    }],
  })
  assert.equal(result.available, true)
  assert.equal(result.valid, false)
  assert.equal(result.blocked, 1)
  assert.equal(result.deferred, true)
  assert.equal(result.targetReason, '中心不连接机构现场 Milvus')
  assert.equal(result.checks[0].expected, 768)
  assert.equal(result.checks[0].observed, 1024)
})
