import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { ApiRequestError, createClientRequestId } from '../../api/platform.js'
import { debugRunPreflightIssue, NO_DEBUG_REVIEW_INPUTS } from './debugRunForm.js'
import { computed, ref } from 'vue'
import { deserializeRuntimeDag } from '../../components/flow/flowModel.js'
import { consoleNodeLabels, consoleNodePresentation } from './debugConsole.js'
import { dataflowOperators } from '../../components/flow/__tests__/flowFixtures.js'

const view = readFileSync(new URL('./DataFlowDebugView.vue', import.meta.url), 'utf8')
const api = readFileSync(new URL('../../api/platform.js', import.meta.url), 'utf8')
const templates = readFileSync(new URL('./TemplateListView.vue', import.meta.url), 'utf8')

test('console node names reuse Catalog metadata while preserving distinct runtime IDs', () => {
  const catalog = [{ code: 'prompt-generator', name: 'Prompt Generator', display_name_zh: '提示词生成器' }]
  const graph = deserializeRuntimeDag({ nodes: [
    { id: 'input-1', kind: 'operator', node_role: 'flow_input', ref: 'reviewed-source-chunk-input' },
    { id: 'generate-a', kind: 'operator', ref: 'prompt-generator' },
    { id: 'child::generate-b', kind: 'operator', ref: 'prompt-generator' },
    { id: 'sink-1', kind: 'knowledge_sink', knowledge_type: 'text' },
    { id: 'legacy-1', kind: 'operator', ref: 'unregistered-operator' },
  ] }, catalog)
  const labels = consoleNodeLabels(graph.nodes)
  assert.deepEqual(consoleNodePresentation('generate-a', labels), { label: '提示词生成器', technicalId: 'generate-a' })
  assert.deepEqual(consoleNodePresentation('child::generate-b', labels), { label: '提示词生成器', technicalId: 'child::generate-b' })
  assert.equal(consoleNodePresentation('input-1', labels).label, '已审核文档块')
  assert.equal(consoleNodePresentation('sink-1', labels).label, '知识输出')
  for (const id of ['legacy-1', 'missing-node']) {
    assert.deepEqual(consoleNodePresentation(id, labels), { label: id, technicalId: '' })
  }
  for (const id of [null, undefined, '']) {
    assert.deepEqual(consoleNodePresentation(id, labels), { label: '流程运行', technicalId: '' })
  }
})

test('DataFlow console labels show both languages from the frozen operator spec', () => {
  const operator = dataflowOperators[0]
  const graph = deserializeRuntimeDag({ nodes: [{ id: 'qa-run-node', kind: 'operator', ref: operator.code, operator_version: operator.version, operator_spec: operator }] }, [])
  assert.deepEqual(consoleNodePresentation('qa-run-node', consoleNodeLabels(graph.nodes)), {
    label: '文本转问答生成器 / Text2QAGenerator', technicalId: 'qa-run-node',
  })
})

test('console names follow the current Run and polled events without changing raw logs', () => {
  const runtimeNodes = ref([{ id: 'node-1', data: { meta: { name: '提示词生成器' } } }])
  const events = ref([{ cursor: 1, node_id: 'node-1', type: 'node.completed', message: '节点 node-1 completed' }])
  const labels = computed(() => consoleNodeLabels(runtimeNodes.value))
  const rows = computed(() => events.value.map(event => ({ ...event, nodePresentation: consoleNodePresentation(event.node_id, labels.value) })))
  assert.equal(rows.value[0].nodePresentation.label, '提示词生成器')
  events.value.push({ cursor: 2, node_id: 'node-1', type: 'node.log', message: 'next page' })
  assert.equal(rows.value[1].nodePresentation.label, '提示词生成器')
  runtimeNodes.value = []
  assert.equal(rows.value[0].nodePresentation.label, 'node-1')
  runtimeNodes.value = [{ id: 'node-1', data: { meta: { name: '来源绑定器' } } }]
  assert.equal(rows.value[0].nodePresentation.label, '来源绑定器')
  assert.equal(rows.value[0].type, 'node.completed')
  assert.equal(rows.value[0].message, '节点 node-1 completed')
  assert.equal(events.value[0].nodePresentation, undefined)
  assert.match(view, /v-for="event in consoleEvents"/)
  assert.match(view, /consoleNodeLabels\(runtimeNodes\.value\)/)
  assert.match(view, /\.console-node-name,\.console-node-id\{display:block;overflow-wrap:anywhere\}/)
})

test('debug workspace exposes full-run preview and flow evolution without commit', () => {
  assert.match(view, /准备运行/)
  assert.match(view, /开始运行/)
  assert.match(view, /内置示例数据/)
  assert.match(view, /虚拟空库 Diff/)
  assert.match(view, /Preview Only/)
  assert.match(view, /应用到当前草稿/)
  assert.match(view, /保存为自定义流程/)
  assert.match(view, /本次调试不会写入正式知识/)
  assert.doesNotMatch(view, /确认提交正式知识/)
})

test('debug preflight stays local until review input and every sink are ready', () => {
  const options = {
    review_inputs: [{ source_review_snapshot_id: 'review-1' }],
    sink_requirements: [{ output_key: 'text' }, { output_key: 'qa' }],
  }
  assert.equal(debugRunPreflightIssue({ ...options, review_inputs: [] }, [], {}), NO_DEBUG_REVIEW_INPUTS)
  assert.match(debugRunPreflightIssue(options, [], {}), /至少选择一份/)
  assert.match(debugRunPreflightIssue(options, ['review-1'], { text: 'kl-text' }), /qa/)
  assert.equal(debugRunPreflightIssue(options, ['review-1'], { text: 'kl-text', qa: 'kl-qa' }), '')
  assert.equal(debugRunPreflightIssue({ ...options, review_inputs: [] }, [], {}, 'builtin_sample'), '')
  assert.match(view, /!canRunPreflight/)
  assert.match(view, /if \(preflightIssue\.value\)/)
  assert.match(view, /前往文档库/)
})

test('debug APIs and template deep link are wired', () => {
  assert.match(api, /debug-runs\/options/)
  assert.match(api, /flow-materialization/)
  assert.match(api, /apply-to-draft/)
  assert.match(api, /save-as-flow/)
  assert.match(templates, /route\.query\.template_id/)
  assert.match(templates, /route\.query\.edit === '1'/)
})

test('runtime DAG exposes view-only automatic layout when a graph is available', () => {
  const handler = view.match(/function autoLayoutRuntimeDag\(\) \{([^}]*)\}/)?.[1] || ''
  assert.match(view, /const runtimeCanvas = ref\(null\)/)
  assert.match(view, /function autoLayoutRuntimeDag\(\) \{ runtimeCanvas\.value\?\.autoLayout\(\) \}/)
  assert.match(view, /v-if="runDetail && viewMode==='dag' && !dagError" :disabled="!runtimeNodes\.length" @click="autoLayoutRuntimeDag">自动布局/)
  assert.match(view, /ref="runtimeCanvas"[^>]*mode="runtime"/)
  assert.doesNotMatch(handler, /api\./)
})

test('client request IDs stay available without crypto.randomUUID', () => {
  const nativeId = '123e4567-e89b-42d3-a456-426614174000'
  assert.equal(createClientRequestId({ randomUUID: () => nativeId }), nativeId)

  const fallbackId = createClientRequestId({
    getRandomValues(bytes) {
      for (let index = 0; index < bytes.length; index += 1) bytes[index] = index
      return bytes
    },
  })
  assert.equal(fallbackId, '00010203-0405-4607-8809-0a0b0c0d0e0f')
  assert.doesNotMatch(view, /\bcrypto\.randomUUID/)
  assert.equal((view.match(/createClientRequestId\(\)/g) || []).length, 4)
})

test('structured API error keeps diagnostics', () => {
  const error = new ApiRequestError({ method: 'GET', url: '/api/test', status: 500, requestId: 'req-1', detail: 'boom' })
  assert.equal(error.method, 'GET')
  assert.equal(error.status, 500)
  assert.equal(error.requestId, 'req-1')
  assert.match(error.message, /GET \/api\/test · HTTP 500 · request_id req-1 · boom/)
  const problem = { code: 'EDGE_WOULD_CREATE_CYCLE', message: '该连接会形成循环依赖', details: { source_node_id: 'a', target_node_id: 'b' } }
  const structured = new ApiRequestError({ method: 'PUT', url: '/api/flow', status: 422, requestId: 'req-2', detail: problem })
  assert.deepEqual(structured.problem, problem)
  assert.equal(structured.detail, problem.message)
})
