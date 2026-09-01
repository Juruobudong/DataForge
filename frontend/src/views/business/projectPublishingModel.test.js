import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

import {
  availableOrgCodePresets,
  compatibleProfilesForTask,
  defaultKnowledgeType,
  movePriority,
  newOrgScopeDefaults,
  normalizeDefaultReleaseStage,
  orgRoutesForTask,
  preferredDeployment,
  reorderPriority,
  resolveOrgCodePreset,
  routingPublishReadiness,
  routingValidationView,
  sortKnowledgeTypes,
  sortProjectChoices,
} from './projectPublishingModel.js'

test('任务只显示当前知识类型绑定的兼容 Profile', () => {
  const types = [
    { status: 'active', code: 'qa-question', index_profiles: [{ id: 'question', code: 'qa-question' }] },
    { status: 'active', code: 'qa-full', index_profiles: [{ id: 'full', code: 'qa-full' }] },
    { status: 'active', code: 'text', index_profiles: [{ id: 'text', code: 'text-default' }] },
  ]
  assert.deepEqual(
    compatibleProfilesForTask({ knowledge_type: 'qa-question' }, types).map(item => item.id),
    ['question'],
  )
  assert.deepEqual(
    compatibleProfilesForTask({ knowledge_type: 'qa-full' }, types).map(item => item.id),
    ['full'],
  )
})

test('任务知识类型下拉默认 qa-question，扩展与未知类型追加在后', () => {
  const types = [
    { code: 'graph', kind: 'builtin' },
    { code: 'zz-case', kind: 'extension' },
    { code: 'qa-full', kind: 'builtin' },
    { code: 'qa-question', kind: 'builtin' },
    { code: 'aa-case', kind: 'extension' },
    { code: 'text', kind: 'builtin' },
    { code: 'legacy', kind: 'builtin' },
  ]
  assert.deepEqual(sortKnowledgeTypes(types).map(item => item.code), [
    'qa-question', 'qa-full', 'text', 'graph', 'aa-case', 'zz-case', 'legacy',
  ])
  assert.deepEqual(sortKnowledgeTypes(), [])
})

test('新增业务任务默认知识类型为问答知识，缺失时回退首项', () => {
  assert.equal(defaultKnowledgeType([{ code: 'graph' }, { code: 'qa-question' }, { code: 'text' }]), 'qa-question')
  assert.equal(defaultKnowledgeType([{ code: 'graph' }, { code: 'text' }]), 'text')
  assert.equal(defaultKnowledgeType([]), '')
})

test('项目选择器固定将 qa_agent 放在首位，其余项目保持 API 顺序', () => {
  const projects = [
    { id: 'kg', code: 'kg-for-consultation', name: 'kg_for_consultation' },
    { id: 'qa', code: 'qa-agent', name: 'qa_agent' },
    { id: 'other', code: 'other', name: '其他项目' },
  ]
  assert.deepEqual(sortProjectChoices(projects).map(item => item.id), ['qa', 'kg', 'other'])
  assert.deepEqual(sortProjectChoices([{ id: 'qa', name: 'qa_agent' }, projects[0]]).map(item => item.id), ['qa', 'kg'])
})

test('项目发布新增任务表单消费知识类型排序与问答默认', () => {
  const publishing = fs.readFileSync(new URL('./ProjectAuthorizationView.vue', import.meta.url), 'utf8')
  assert.match(publishing, /const activeKnowledgeTypes = computed\(\(\) => sortKnowledgeTypes\(/)
  assert.match(publishing, /newTaskKnowledgeType\.value \|\|= defaultKnowledgeType\(activeKnowledgeTypes\.value\)/)
  assert.match(publishing, /const projectChoices = computed\(\(\) => sortProjectChoices\(projects\.value\)\)/)
  assert.match(publishing, /projectId\.value \|\|= projectChoices\.value\[0\]\?\.id/)
  assert.match(publishing, /v-for="project in projectChoices"/)
})

test('机构 Deployment 的新建与编辑项目选择器复用项目发布排序', () => {
  const deployment = fs.readFileSync(new URL('./InstitutionDeploymentView.vue', import.meta.url), 'utf8')
  assert.match(deployment, /import \{ sortProjectChoices \} from '\.\/projectPublishingModel'/)
  assert.match(deployment, /const projectChoices = computed\(\(\) => sortProjectChoices\(projects\.value\)\)/)
  assert.equal((deployment.match(/v-for="project in projectChoices"/g) || []).length, 2)
  assert.doesNotMatch(deployment, /v-for="project in projects"/)
})

test('默认发布环境只接受生产值，其余安全回退测试环境', () => {
  assert.equal(normalizeDefaultReleaseStage('production'), 'production')
  assert.equal(normalizeDefaultReleaseStage('test'), 'test')
  assert.equal(normalizeDefaultReleaseStage(undefined), 'test')
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

test('知识范围拖拽支持目标前后插入且忽略无效重排', () => {
  assert.deepEqual(reorderPriority(['a', 'b', 'c'], 'c', 'a'), ['c', 'a', 'b'])
  assert.deepEqual(reorderPriority(['a', 'b', 'c'], 'a', 'b', true), ['b', 'a', 'c'])
  assert.deepEqual(reorderPriority(['a', 'b', 'c'], 'a', 'a', true), ['a', 'b', 'c'])
  assert.deepEqual(reorderPriority(['a', 'b', 'c'], 'missing', 'b'), ['a', 'b', 'c'])
})

test('知识范围按 Task 与 org_code 组合隔离', () => {
  const routes = [
    { id: 'a-1', project_release_task_id: 'task-a', org_code: 'ORG-1' },
    { id: 'a-2', project_release_task_id: 'task-a', org_code: 'ORG-2' },
    { id: 'b-1', project_release_task_id: 'task-b', org_code: 'ORG-1' },
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

test('项目发布工作台使用固定上下文和四个一级入口', () => {
  const publishing = fs.readFileSync(new URL('./ProjectAuthorizationView.vue', import.meta.url), 'utf8')
  assert.match(publishing, /class="panel publishing-context"/)
  assert.match(publishing, />配置<\/button>/)
  assert.match(publishing, />验证<\/button>/)
  assert.match(publishing, />发布<\/button>/)
  assert.match(publishing, />版本记录<\/button>/)
  assert.doesNotMatch(publishing, /tab==='target'/)
  assert.doesNotMatch(publishing, /tab==='scope'/)
  assert.match(publishing, /<RetrievalTaskSettings[^>]+:task="task"/)
  assert.match(publishing, /<KnowledgeScopePanel[^>]+:libraries="availableLibraries"/)
  assert.match(publishing, /@reorder="reorderLibrary"/)
  assert.match(publishing, /function reorderLibrary\(\{ id, targetId, after \}\)/)
  assert.match(publishing, /reorderPriority\(chosen\.value, id, targetId, after\)/)
  assert.match(publishing, /@validate="preflight"/)
  assert.match(publishing, /selectedStage\.value = configuredDefaultStage\(\)/)
  assert.match(publishing, /instance\.value\?\.default_release_stage/)
})

test('项目发布任务删除要求确认、避免重复提交并在成功后刷新草稿', () => {
  const publishing = fs.readFileSync(new URL('./ProjectAuthorizationView.vue', import.meta.url), 'utf8')
  const api = fs.readFileSync(new URL('../../api/platform.js', import.meta.url), 'utf8')
  assert.match(api, /deleteDeploymentTask:.*method: 'DELETE'/)
  assert.match(publishing, /async function deleteDeploymentTask\(task\)/)
  assert.match(publishing, /确认删除检索任务/)
  assert.match(publishing, /已发布和历史版本不受影响；下次发布后正式检索才会生效/)
  assert.match(publishing, /if \(deletingTaskId\.value\) return/)
  assert.match(publishing, /:disabled="!!deletingTaskId"/)
  assert.match(publishing, /await load\(\); await loadDeployment\(\)/)
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
