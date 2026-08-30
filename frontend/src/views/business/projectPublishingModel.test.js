import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

import {
  availableOrgCodePresets,
  compatibleProfilesForTask,
  movePriority,
  newOrgScopeDefaults,
  orgRoutesForTask,
  preferredDeployment,
  qaEmbeddingMode,
  resolveOrgCodePreset,
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
    '请先配置并启用检索通道',
    '请先完成知识范围配置',
    '当前环境尚未配置 Milvus 服务',
  ])
  assert.equal(routingPublishReadiness(
    [{ enabled: true }],
    [{ enabled: true, knowledge_library_ids: ['library-1'] }],
    'http://milvus:19531',
  ).ready, true)
})

test('新项目优先选择 DataForge 中心，本地实例选择绑定目标', () => {
  const deployments = [
    { id: 'institution-binding', deployment_id: 'institution', scope: 'institution' },
    { id: 'central-binding', deployment_id: 'central', scope: 'central' },
  ]
  assert.equal(preferredDeployment(deployments).id, 'central-binding')
  assert.equal(preferredDeployment(deployments, 'institution').id, 'institution-binding')
})

test('知识范围优先级按上下移动后的显式顺序提交', () => {
  assert.deepEqual(movePriority(['c', 'a', 'b'], 'a', -1), ['a', 'c', 'b'])
  assert.deepEqual(movePriority(['c', 'a', 'b'], 'c', -1), ['c', 'a', 'b'])
})

test('知识范围按 Task 与 org_code 组合隔离', () => {
  const routes = [
    { id: 'a-1', project_deployment_task_id: 'task-a', org_code: 'ORG-1' },
    { id: 'a-2', project_deployment_task_id: 'task-a', org_code: 'ORG-2' },
    { id: 'b-1', project_deployment_task_id: 'task-b', org_code: 'ORG-1' },
  ]
  assert.deepEqual(orgRoutesForTask(routes, 'task-a').map(item => item.id), ['a-1', 'a-2'])
  assert.deepEqual(orgRoutesForTask(routes, 'task-b').map(item => item.id), ['b-1'])
})

test('新增知识范围只预填未占用的机构码且保持可独立修改', () => {
  const deployment = { scope: 'institution', institution_code: 'INST-A', institution_name: '机构 A' }
  assert.deepEqual(newOrgScopeDefaults(deployment), { orgCode: 'INST-A', orgName: '机构 A' })
  assert.deepEqual(newOrgScopeDefaults(deployment, [{ org_code: 'INST-A' }]), {
    orgCode: '', orgName: '机构 A',
  })
  assert.deepEqual(newOrgScopeDefaults({ scope: 'central' }), { orgCode: 'general', orgName: '' })
})

test('org_code 预配置保持顺序并只切换当前 Task 已有范围', () => {
  const presets = availableOrgCodePresets([
    { name: ' 厦门第一医院 ', org_code: ' KMDSRMYY ' },
    { name: '厦门市中医院', org_code: 'XMSZ' },
  ])
  assert.deepEqual(presets, [
    { name: '厦门第一医院', org_code: 'KMDSRMYY' },
    { name: '厦门市中医院', org_code: 'XMSZ' },
  ])
  assert.deepEqual(availableOrgCodePresets(null), [])
  assert.equal(resolveOrgCodePreset(presets, 'KMDSRMYY', [{ id: 'route-a', org_code: 'KMDSRMYY' }]).existingRoute.id, 'route-a')
  assert.equal(resolveOrgCodePreset(presets, 'KMDSRMYY', []).existingRoute, null)
  assert.equal(resolveOrgCodePreset(presets, 'UNKNOWN', []), null)
})

test('项目发布预设选择自动填充且手工改码回到自定义', () => {
  const publishing = fs.readFileSync(new URL('./ProjectAuthorizationView.vue', import.meta.url), 'utf8')
  assert.match(publishing, /预配置机构/)
  assert.match(publishing, /@change="applyOrgPreset"/)
  assert.match(publishing, /@input="markCustomOrgCode"/)
  assert.match(publishing, /resolvedPreset\.existingRoute/)
  assert.match(publishing, /orgName\.value = resolvedPreset\.preset\.name/)
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
