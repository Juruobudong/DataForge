import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import DataFlowDebugView from '../DataFlowDebugView.vue'
import { api } from '../../../api/platform'

const route = vi.hoisted(() => ({ query: {} }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), useRoute: () => route }))
vi.mock('../../../api/platform', () => ({ createClientRequestId: () => 'request', api: Object.fromEntries([
  'flowTemplates', 'flowRuns', 'operatorCatalog', 'vectorIndexes', 'flowRunCapabilities', 'flowRun', 'flowRunEvents', 'debugRunOptions',
  'debugRunMaterialization', 'sinkPreviewCandidates',
].map(name => [name, vi.fn()])) }))
vi.mock('../../../components/flow/DataForgeFlowCanvas.vue', () => ({ default: {
  name: 'DataForgeFlowCanvas', props: ['nodes', 'edges'], emits: ['select-node', 'update:nodes'], template: '<div class="test-canvas" />',
} }))

let wrapper, poll
const initial = { id: 'r', status: 'running', debug_input_snapshot_id: 's', nodes: [],
  revision_kind: 'draft', source_revision: 2, source_definition_checksum: 'saved-draft', compiled_checksum: 'frozen-flow',
  node_count: 1, edge_count: 0, created_at: '2026-08-28T06:00:00',
  runtime_dag: { nodes: [{ id: 'n', kind: 'operator', ref: 'text-knowledge-mapper', status: 'running' }], edges: [] } }
beforeEach(() => {
  route.query = {}
  vi.spyOn(window, 'setInterval').mockImplementation(fn => { poll = fn; return 1 })
  vi.spyOn(window, 'clearInterval').mockImplementation(() => {})
  api.flowTemplates.mockResolvedValue([])
  api.flowRuns.mockResolvedValue([{ id: 'r', debug_input_snapshot_id: 's' }])
  api.operatorCatalog.mockResolvedValue([])
  api.vectorIndexes.mockResolvedValue({})
  api.flowRunCapabilities.mockResolvedValue({})
  api.flowRun.mockResolvedValue(initial)
  api.flowRunEvents.mockResolvedValue({ items: [], next_cursor: 0 })
  api.debugRunMaterialization.mockResolvedValue(null)
  api.sinkPreviewCandidates.mockResolvedValue({ items: [{ canonical_content: '最终正文' }], total: 1, offset: 0, limit: 50, has_more: false })
})
afterEach(() => wrapper?.unmount())

it('collapses technical identifiers by default, preserves toggles during polling, and resets for another Run or page entry', async () => {
  const draftChecksum = '367331c3c2d3594cbc4e4cbfbe98b2c2275393440a2f41e316a89cd9c4f6f570'
  const flowChecksum = '7f2929d3d102e3a0bdee6e45c4347da297be77f1500dac7e0892f4e1eb46e493'
  const detail = { ...initial, source_definition_checksum: draftChecksum, compiled_checksum: flowChecksum,
    execution_snapshot_id: 'snapshot-full-id', parent_flow_run_id: 'parent-full-id' }
  api.flowRuns.mockResolvedValue([{ id: 'r', debug_input_snapshot_id: 's' }, { id: 'next', debug_input_snapshot_id: 's2' }])
  api.flowRun.mockImplementation(async id => ({ ...detail, id }))
  wrapper = mount(DataFlowDebugView, { attachTo: document.body }); await flushPromises()
  const details = wrapper.get('.run-technical-details')
  expect(details.element.tagName).toBe('DETAILS')
  expect(details.get('summary').text()).toBe('技术详情')
  expect(details.element.open).toBe(false)
  expect(details.get('.run-technical-values').isVisible()).toBe(false)
  expect(wrapper.get('.run-provenance > b').isVisible()).toBe(true)
  expect(wrapper.get('.run-provenance > b').text()).toBe('Run r')
  expect(wrapper.get('.dag-toolbar').text()).toContain('运行状态：running')
  expect(wrapper.get('.dag-toolbar').text()).not.toContain('snapshot-full-id')
  for (const field of wrapper.findAll('.run-provenance > span')) expect(field.isVisible()).toBe(true)
  expect(wrapper.get('.run-provenance > button').text()).toBe('查看本次运行 DAG')
  await details.get('summary').trigger('click'); await flushPromises()
  expect(details.element.open).toBe(true)
  expect(details.get('.run-technical-values').isVisible()).toBe(true)
  expect(details.findAll('code').map(code => code.text())).toEqual([
    `Draft checksum：${draftChecksum}`, `Flow checksum：${flowChecksum}`,
    '执行快照 ID：snapshot-full-id', '父 Run ID：parent-full-id；参数覆盖见节点记录。',
  ])
  api.flowRunEvents.mockResolvedValue({ items: [{ cursor: 1, type: 'node.completed' }], next_cursor: 1 })
  await poll(); await flushPromises()
  expect(wrapper.get('.run-technical-details').element).toBe(details.element)
  expect(details.element.open).toBe(true)
  await details.get('summary').trigger('click'); await flushPromises()
  await poll(); await flushPromises()
  expect(details.element.open).toBe(false)
  await details.get('summary').trigger('click'); await flushPromises()
  await wrapper.findAll('.run-card')[1].trigger('click'); await flushPromises()
  expect(wrapper.get('.run-technical-details').element.open).toBe(false)
  expect(wrapper.get('.run-provenance > b').text()).toBe('Run next')
  await wrapper.get('.run-technical-details summary').trigger('click'); await flushPromises()
  wrapper.unmount()
  wrapper = mount(DataFlowDebugView, { attachTo: document.body }); await flushPromises()
  expect(wrapper.get('.run-technical-details').element.open).toBe(false)
})

it('opens final results on the first preview, preserves manual DAG choice and reopens historical results', async () => {
  const detail = { ...initial, runtime_dag: { nodes: [...initial.runtime_dag.nodes, { id: 'sink', kind: 'knowledge_sink', output_key: 'text' }], edges: [{ source: 'n', target: 'sink' }] } }
  api.flowRun.mockResolvedValue(detail)
  wrapper = mount(DataFlowDebugView, { attachTo: document.body }); await flushPromises()
  const preview = { id: 'p', output_key: 'text', candidate_count: 1, diff: { ADD: 1 } }
  api.flowRun.mockResolvedValue({ ...detail, status: 'completed', sink_previews: [preview] })
  api.flowRunEvents.mockResolvedValue({ items: [{ cursor: 1, type: 'sink.preview_ready' }], next_cursor: 1 })
  await poll(); await flushPromises()
  expect(wrapper.get('[aria-label="最终结果"]').isVisible()).toBe(true)
  expect(wrapper.text()).toContain('最终正文')
  await wrapper.findAll('.view-switch button').find(button => button.text() === '执行 DAG').trigger('click')
  api.flowRunEvents.mockResolvedValue({ items: [{ cursor: 2, type: 'run.completed' }], next_cursor: 2 })
  await poll(); await flushPromises()
  expect(wrapper.get('[aria-label="最终结果"]').isVisible()).toBe(false)
  expect(wrapper.get('.test-canvas').isVisible()).toBe(true)
  expect(api.sinkPreviewCandidates).toHaveBeenCalledTimes(1)
  await wrapper.get('.run-card').trigger('click'); await flushPromises()
  expect(wrapper.get('[aria-label="最终结果"]').isVisible()).toBe(true)
})

it('ignores a late run detail after selecting another Run', async () => {
  let resolveOld
  api.flowRuns.mockResolvedValue([{ id: 'r', debug_input_snapshot_id: 's' }, { id: 'new', debug_input_snapshot_id: 's2' }])
  api.flowRun.mockImplementation(id => id === 'r' ? new Promise(resolve => { resolveOld = resolve }) : Promise.resolve({ ...initial, id: 'new', compiled_checksum: 'new-checksum' }))
  wrapper = mount(DataFlowDebugView); await flushPromises()
  await wrapper.findAll('.run-card')[1].trigger('click'); await flushPromises()
  resolveOld(initial); await flushPromises()
  expect(wrapper.get('[aria-label="本次运行快照"]').text()).toContain('new-checksum')
  expect(wrapper.get('[aria-label="本次运行快照"]').text()).not.toContain('frozen-flow')
})

it('switches to the selected template latest Run and clears results for an unrun template', async () => {
  api.flowTemplates.mockResolvedValue([{ id: 'one', name: '模板一' }, { id: 'two', name: '模板二' }, { id: 'empty', name: '未运行模板' }])
  api.flowRuns.mockResolvedValue([{ id: 'r', template_id: 'one', debug_input_snapshot_id: 's' }, { id: 'r2', template_id: 'two', debug_input_snapshot_id: 's2' }])
  api.flowRun.mockImplementation(async id => ({ ...initial, id, compiled_checksum: id }))
  wrapper = mount(DataFlowDebugView); await flushPromises()
  await wrapper.findAll('.template-card')[1].trigger('click'); await flushPromises()
  expect(api.flowRun).toHaveBeenLastCalledWith('r2')
  expect(wrapper.get('[aria-label="本次运行快照"]').text()).toContain('Run r2')
  await wrapper.findAll('.template-card')[2].trigger('click'); await flushPromises()
  expect(wrapper.find('[aria-label="本次运行快照"]').exists()).toBe(false)
  expect(wrapper.find('[aria-label="最终结果"]').exists()).toBe(false)
})

it('keeps historical final results accessible when optional flow materialization is unavailable', async () => {
  api.flowRun.mockResolvedValue({ ...initial, status: 'completed',
    runtime_dag: { nodes: [{ id: 'sink', kind: 'knowledge_sink', output_key: 'text' }], edges: [] },
    sink_previews: [{ id: 'p', output_key: 'text', candidate_count: 1, diff: {} }] })
  api.debugRunMaterialization.mockRejectedValue(new Error('源流程不可另存'))
  wrapper = mount(DataFlowDebugView); await flushPromises()
  expect(wrapper.get('[aria-label="最终结果"]').text()).toContain('最终正文')
  expect(wrapper.find('.dag-error').exists()).toBe(false)
  expect(wrapper.text()).toContain('不影响查看最终结果')
})

it('opens actual failure logs from result diagnostics even when the node completed, including repeated clicks', async () => {
  const reason = 'QA_OUTPUT_INVALID: 提问方向必须为 JSON 字符串数组'
  api.flowRun.mockResolvedValue({ ...initial, status: 'completed',
    nodes: [{ node_id: 'n', status: 'completed', operator_code: 'Text2QAGenerator', error_detail: {},
      metrics: { chunk_processing: [{ output_key: 'qa', attempted_chunks: 5, successful_chunks: 3, failed_chunks: 2 }] },
      logs: [{ stream: 'stderr', message: reason }] }],
    runtime_dag: { nodes: [{ id: 'n', kind: 'operator', ref: 'Text2QAGenerator' }, { id: 'sink', kind: 'knowledge_sink', output_key: 'qa' }], edges: [{ source: 'n', target: 'sink' }] },
    sink_previews: [{ id: 'p', output_key: 'qa', candidate_count: 3, diff: { ADD: 3 } }] })
  wrapper = mount(DataFlowDebugView); await flushPromises()
  expect(wrapper.get('.processing').text()).toContain(reason)
  const inspect = wrapper.findAll('.processing button').find(button => button.text() === '查看节点诊断')
  await inspect.trigger('click'); await flushPromises()
  expect(wrapper.get('.runtime-inspector nav button.active').text()).toBe('日志')
  expect(wrapper.get('.runtime-inspector [aria-label="失败原因"]').text()).toContain('成功 3 块 · 失败 2 块')
  expect(wrapper.get('[aria-label="算子日志"]').text()).toContain(reason)
  await wrapper.findAll('.runtime-inspector nav button').find(button => button.text() === '参数').trigger('click')
  await inspect.trigger('click'); await flushPromises()
  expect(wrapper.get('.runtime-inspector nav button.active').text()).toBe('日志')
})

it('blocks run preparation when the draft changed after leaving the canvas', async () => {
  route.query = { template_id: 'flow', revision_kind: 'draft', draft_checksum: 'canvas-checksum' }
  api.flowTemplates.mockResolvedValue([{ id: 'flow', name: '草稿', revision_status: 'draft' }])
  api.debugRunOptions.mockResolvedValue({ revision: { id: 'rev', status: 'draft' }, source_definition_checksum: 'changed-checksum' })
  wrapper = mount(DataFlowDebugView)
  await flushPromises()
  expect(wrapper.text()).toContain('草稿在离开画布后已变化')
  expect(wrapper.findAll('button').find(item => item.text() === '开始运行').attributes('disabled')).toBeDefined()
})

it('refreshes selected node logs on terminal events and preserves browser layout', async () => {
  wrapper = mount(DataFlowDebugView)
  await flushPromises()
  expect(wrapper.get('[aria-label="本次运行快照"]').text()).toContain('当前草稿（启动时冻结）')
  expect(wrapper.get('[aria-label="本次运行快照"]').text()).toContain('saved-draft')
  expect(wrapper.get('[aria-label="本次运行快照"]').text()).toContain('frozen-flow')
  expect(wrapper.get('[aria-label="本次运行快照"]').text()).toContain('1 节点 · 0 连线')
  const canvas = wrapper.findComponent({ name: 'DataForgeFlowCanvas' })
  const node = { ...canvas.props('nodes')[0], position: { x: 444, y: 333 } }
  canvas.vm.$emit('update:nodes', [node])
  canvas.vm.$emit('select-node', node)
  await flushPromises()
  await wrapper.findAll('.runtime-inspector nav button').find(button => button.text() === '日志').trigger('click')
  expect(wrapper.get('[aria-label="算子日志"]').text()).toContain('暂无算子日志')
  const log = { stream: 'stderr', message: 'package warning', truncated: true }
  api.flowRun.mockResolvedValue({ ...initial, status: 'completed', nodes: [{ node_id: 'n', status: 'completed', logs: [log] }] })
  api.flowRunEvents.mockResolvedValue({ items: [{ cursor: 1, type: 'node.operator_log', node_id: 'n', message: log.message, payload: log }], next_cursor: 1 })
  await poll(); await flushPromises()
  expect(wrapper.get('[aria-label="算子日志"]').text()).toContain('package warning')
  expect(wrapper.get('[aria-label="运行日志"]').text()).toContain('[stderr] [已截断] package warning')
  expect(canvas.props('nodes')[0].position).toEqual({ x: 444, y: 333 })
})
