import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { ApiRequestError } from '../../api/platform.js'

const view = readFileSync(new URL('./DataFlowDebugView.vue', import.meta.url), 'utf8')
const api = readFileSync(new URL('../../api/platform.js', import.meta.url), 'utf8')
const templates = readFileSync(new URL('./TemplateListView.vue', import.meta.url), 'utf8')

test('debug workspace exposes full-run preview and flow evolution without commit', () => {
  assert.match(view, /新建调试 Run/)
  assert.match(view, /运行整个流程/)
  assert.match(view, /Preview Only/)
  assert.match(view, /应用到当前草稿/)
  assert.match(view, /保存为自定义流程/)
  assert.match(view, /本次调试不会写入正式知识/)
  assert.doesNotMatch(view, /确认提交正式知识/)
})

test('debug APIs and template deep link are wired', () => {
  assert.match(api, /debug-runs\/options/)
  assert.match(api, /flow-materialization/)
  assert.match(api, /apply-to-draft/)
  assert.match(api, /save-as-flow/)
  assert.match(templates, /route\.query\.template_id/)
  assert.match(templates, /route\.query\.edit === '1'/)
})

test('structured API error keeps diagnostics', () => {
  const error = new ApiRequestError({ method: 'GET', url: '/api/test', status: 500, requestId: 'req-1', detail: 'boom' })
  assert.equal(error.method, 'GET')
  assert.equal(error.status, 500)
  assert.equal(error.requestId, 'req-1')
  assert.match(error.message, /GET \/api\/test · HTTP 500 · request_id req-1 · boom/)
})
