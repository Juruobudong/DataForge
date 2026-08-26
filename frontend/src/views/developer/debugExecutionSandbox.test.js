import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { ApiRequestError, createClientRequestId } from '../../api/platform.js'
import { debugRunPreflightIssue, NO_DEBUG_REVIEW_INPUTS } from './debugRunForm.js'

const view = readFileSync(new URL('./DataFlowDebugView.vue', import.meta.url), 'utf8')
const api = readFileSync(new URL('../../api/platform.js', import.meta.url), 'utf8')
const templates = readFileSync(new URL('./TemplateListView.vue', import.meta.url), 'utf8')

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
})
