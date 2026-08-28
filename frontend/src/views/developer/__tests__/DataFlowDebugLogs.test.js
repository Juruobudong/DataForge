import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import DataFlowDebugView from '../DataFlowDebugView.vue'
import { api } from '../../../api/platform'

const route = vi.hoisted(() => ({ query: {} }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), useRoute: () => route }))
vi.mock('../../../api/platform', () => ({ createClientRequestId: () => 'request', api: Object.fromEntries([
  'flowTemplates', 'flowRuns', 'operatorCatalog', 'vectorIndexes', 'flowRunCapabilities', 'flowRun', 'flowRunEvents', 'debugRunOptions',
  'debugRunMaterialization', 'sinkPreviewCandidates', 'debugRunPreflight', 'createDebugRun', 'createDerivedRun',
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

const clickButton = async name => {
  await wrapper.findAll('button').find(button => button.text() === name).trigger('click')
  await flushPromises()
}
async function prepareRun({ history = true, options = { revision: { id: 'rev', status: 'draft' } } } = {}) {
  api.flowTemplates.mockResolvedValue([{ id: 'flow', name: '测试流程', revision_status: 'draft' }])
  api.flowRuns.mockResolvedValue(history ? [{ id: 'r', template_id: 'flow', debug_input_snapshot_id: 's' }] : [])
  api.debugRunOptions.mockResolvedValue(options)
  api.debugRunPreflight.mockResolvedValue({ input_count: 1, output_keys: ['text'] })
  api.createDebugRun.mockResolvedValue({ id: 'new' })
  api.flowRun.mockImplementation(async id => ({ ...initial, id }))
  wrapper = mount(DataFlowDebugView, { attachTo: document.body })
  await flushPromises()
  const scroll = vi.fn()
  wrapper.get('.console').element.scrollIntoView = scroll
  await clickButton('准备运行')
  return scroll
}

it('automatically preflights on preparation and only creates a Run after explicit start', async () => {
  const scroll = await prepareRun({ history: false })
  expect(api.debugRunPreflight).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    template_id: 'flow', revision_id: 'rev', input_source: 'builtin_sample',
  }))
  expect(wrapper.get('.drawer [role="status"]').text()).toContain('预检通过')
  expect(wrapper.findAll('button').find(button => button.text() === '开始运行').element.disabled).toBe(false)
  expect(api.createDebugRun).not.toHaveBeenCalled()
  expect(scroll).not.toHaveBeenCalled()
  await clickButton('开始运行')
  expect(api.createDebugRun).toHaveBeenCalledTimes(1)
  expect(api.debugRunPreflight).toHaveBeenCalledTimes(1)
})

it('shows automatic preflight progress, blocks start on failure, and allows manual retry', async () => {
  let rejectPreflight
  api.debugRunPreflight.mockImplementationOnce(() => new Promise((resolve, reject) => { rejectPreflight = reject }))
  await prepareRun()
  expect(wrapper.get('.drawer [role="status"]').text()).toBe('正在运行预检…')
  expect(wrapper.findAll('button').find(button => button.text() === '开始运行').element.disabled).toBe(true)
  await clickButton('开始运行')
  expect(api.createDebugRun).not.toHaveBeenCalled()
  rejectPreflight(new Error('自动预检失败')); await flushPromises()
  expect(wrapper.get('.drawer [role="alert"]').text()).toBe('自动预检失败')
  expect(wrapper.findAll('button').find(button => button.text() === '开始运行').element.disabled).toBe(true)
  await clickButton('运行预检')
  expect(wrapper.get('.drawer [role="status"]').text()).toContain('预检通过')
  expect(api.debugRunPreflight).toHaveBeenCalledTimes(2)
})

it('waits for all business input selections before automatically preflighting again', async () => {
  await prepareRun({ options: {
    revision: { id: 'rev', status: 'draft' },
    review_inputs: [{ source_review_snapshot_id: 'review', document_library_id: 'docs', filename: '审核文档' }],
    sink_requirements: [{ output_key: 'text' }],
    sink_options: { text: [{ id: 'library', name: '文本知识库' }] },
  } })
  await wrapper.get('input[value="source_review_snapshot"]').setValue(); await flushPromises()
  expect(wrapper.find('.drawer .success').exists()).toBe(false)
  expect(api.debugRunPreflight).toHaveBeenCalledTimes(1)
  await wrapper.get('.review-option input').setValue(true); await flushPromises()
  expect(api.debugRunPreflight).toHaveBeenCalledTimes(1)
  await wrapper.findAll('.drawer select')[1].setValue('library'); await flushPromises()
  expect(api.debugRunPreflight).toHaveBeenCalledTimes(2)
  expect(api.debugRunPreflight).toHaveBeenLastCalledWith(expect.objectContaining({
    input_source: 'source_review_snapshot', source_review_snapshot_ids: ['review'], sink_library_bindings: { text: 'library' },
  }))
  await wrapper.get('.review-option input').setValue(false); await flushPromises()
  expect(wrapper.find('.drawer .success').exists()).toBe(false)
  expect(wrapper.findAll('button').find(button => button.text() === '开始运行').element.disabled).toBe(true)
  expect(api.debugRunPreflight).toHaveBeenCalledTimes(2)
})

it.each(['success', 'error'])('ignores a stale preflight %s after changing sample', async outcome => {
  let resolveOld, rejectOld, resolveNew
  api.debugRunPreflight.mockImplementationOnce(() => new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject }))
  await prepareRun({ options: { revision: { id: 'rev' }, builtin_samples: [
    { code: 'one', name: '示例一' }, { code: 'two', name: '示例二' },
  ] } })
  api.debugRunPreflight.mockImplementationOnce(() => new Promise(resolve => { resolveNew = resolve }))
  await wrapper.get('.sample-card select').setValue('two'); await flushPromises()
  if (outcome === 'success') resolveOld({ input_count: 99, output_keys: ['过期结果'] })
  else rejectOld(new Error('过期错误'))
  await flushPromises()
  expect(wrapper.get('.drawer [role="status"]').text()).toBe('正在运行预检…')
  expect(wrapper.text()).not.toContain('过期')
  resolveNew({ input_count: 2, output_keys: ['text'] }); await flushPromises()
  expect(wrapper.get('.drawer .success').text()).toContain('2 个文档块')
  expect(api.debugRunPreflight).toHaveBeenLastCalledWith(expect.objectContaining({ sample_code: 'two' }))
})

it('only preflights the latest revision when options return out of order', async () => {
  let resolveOld
  api.debugRunOptions.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
  await prepareRun()
  expect(wrapper.get('.drawer [role="status"]').text()).toBe('正在加载运行配置…')
  expect(api.debugRunPreflight).not.toHaveBeenCalled()
  api.debugRunOptions.mockResolvedValueOnce({ revision: { id: 'published-rev', status: 'published' } })
  await wrapper.get('.drawer select').setValue('published'); await flushPromises()
  resolveOld({ revision: { id: 'old-draft', status: 'draft' } }); await flushPromises()
  expect(api.debugRunPreflight).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ revision_id: 'published-rev' }))
  expect(wrapper.get('.revision-summary').text()).toContain('published')
})

it.each(['options', 'preflight'])('ignores late %s after closing and reopening preparation', async phase => {
  let resolveOld
  const pendingApi = phase === 'options' ? api.debugRunOptions : api.debugRunPreflight
  pendingApi.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
  await prepareRun()
  await clickButton('关闭')
  await clickButton('准备运行')
  resolveOld(phase === 'options' ? { revision: { id: 'stale-rev' } } : { input_count: 99, output_keys: ['stale'] })
  await flushPromises()
  expect(wrapper.get('.drawer .success').text()).toContain('1 个文档块')
  expect(wrapper.text()).not.toContain('stale')
  expect(api.debugRunPreflight).toHaveBeenCalledTimes(phase === 'options' ? 1 : 2)
  expect(api.createDebugRun).not.toHaveBeenCalled()
})

it('preflights a flow deep link automatically but not a changed draft checksum', async () => {
  route.query = { template_id: 'flow', revision_kind: 'draft', draft_checksum: 'expected' }
  api.flowTemplates.mockResolvedValue([{ id: 'flow', name: '测试流程', revision_status: 'draft' }])
  api.flowRuns.mockResolvedValue([])
  api.debugRunOptions.mockResolvedValue({ revision: { id: 'rev' }, source_definition_checksum: 'expected' })
  api.debugRunPreflight.mockResolvedValue({ input_count: 1, output_keys: ['text'] })
  wrapper = mount(DataFlowDebugView); await flushPromises()
  expect(api.debugRunPreflight).toHaveBeenCalledTimes(1)
  expect(api.createDebugRun).not.toHaveBeenCalled()
  await clickButton('关闭')
  api.debugRunOptions.mockResolvedValueOnce({ revision: { id: 'changed' }, source_definition_checksum: 'changed' })
  await clickButton('准备运行')
  expect(wrapper.get('.drawer [role="alert"]').text()).toContain('草稿在离开画布后已变化')
  expect(api.debugRunPreflight).toHaveBeenCalledTimes(1)
  expect(wrapper.findAll('button').find(button => button.text() === '开始运行').element.disabled).toBe(true)
})

it('scrolls once after creating a Run, before events arrive, without moving on preparation, history or polling', async () => {
  const scroll = await prepareRun({ history: false })
  expect(wrapper.get('.console').element.parentElement).toBe(wrapper.get('.debug-page').element)
  await clickButton('运行预检')
  expect(scroll).not.toHaveBeenCalled()
  api.flowRuns.mockResolvedValue(['new', 'r'].map(id => ({ id, template_id: 'flow', debug_input_snapshot_id: 's' })))
  let resolveEvents
  api.flowRunEvents.mockImplementationOnce(() => new Promise(resolve => { resolveEvents = resolve }))
  await clickButton('开始运行')
  expect(wrapper.find('.drawer').exists()).toBe(false)
  expect(wrapper.get('.run-provenance').text()).toContain('Run new')
  expect(scroll).toHaveBeenCalledExactlyOnceWith({ behavior: 'smooth', block: 'end', inline: 'nearest' })
  resolveEvents({ items: [], next_cursor: 0 }); await flushPromises()
  api.flowRunEvents.mockResolvedValue({ items: [{ cursor: 1, type: 'run.completed' }], next_cursor: 1 })
  await poll(); await flushPromises()
  await clickButton('刷新')
  await wrapper.findAll('.run-card').at(-1).trigger('click'); await flushPromises()
  expect(scroll).toHaveBeenCalledTimes(1)
})

it.each(['运行此节点', '从此节点运行', '重新运行失败节点'])('scrolls once for %s and respects reduced motion', async name => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
  try {
    api.flowRunCapabilities.mockResolvedValue({ debug_replay_enabled: true })
    api.flowRun.mockImplementation(async id => ({ ...initial, id, nodes: [{ node_id: 'n', status: 'failed' }] }))
    api.createDerivedRun.mockResolvedValue({ id: 'derived' })
    wrapper = mount(DataFlowDebugView, { attachTo: document.body }); await flushPromises()
    const scroll = vi.fn()
    wrapper.get('.console').element.scrollIntoView = scroll
    const canvas = wrapper.findComponent({ name: 'DataForgeFlowCanvas' })
    canvas.vm.$emit('select-node', canvas.props('nodes')[0]); await flushPromises()
    await clickButton(name)
    expect(api.createDerivedRun).toHaveBeenCalledWith('r', expect.objectContaining({ mode: name === '运行此节点' ? 'node_only' : 'from_node' }))
    expect(wrapper.get('.run-provenance').text()).toContain('Run derived')
    expect(scroll).toHaveBeenCalledExactlyOnceWith({ behavior: 'instant', block: 'end', inline: 'nearest' })
  } finally { vi.unstubAllGlobals() }
})

it('does not scroll after preflight or Run creation errors', async () => {
  const scroll = await prepareRun()
  api.debugRunPreflight.mockRejectedValueOnce(new Error('预检失败'))
  await clickButton('运行预检')
  expect(wrapper.text()).toContain('预检失败')
  expect(api.createDebugRun).not.toHaveBeenCalled()
  await clickButton('运行预检')
  api.createDebugRun.mockRejectedValueOnce(new Error('创建失败'))
  await clickButton('开始运行')
  expect(wrapper.text()).toContain('创建失败')
  expect(wrapper.find('.drawer').exists()).toBe(true)
  expect(scroll).not.toHaveBeenCalled()
})

it.each(['creation', 'detail', 'unmount'])('ignores a late %s response when the user leaves the pending Run', async phase => {
  const scroll = await prepareRun()
  await clickButton('运行预检')
  let resolvePending
  if (phase === 'creation') api.createDebugRun.mockImplementationOnce(() => new Promise(resolve => { resolvePending = resolve }))
  else api.flowRun.mockImplementation(id => id === 'new' ? new Promise(resolve => { resolvePending = resolve }) : Promise.resolve({ ...initial, id }))
  await clickButton('开始运行')
  if (phase === 'unmount') wrapper.unmount()
  else {
    if (phase === 'creation') await clickButton('关闭')
    await wrapper.get('.run-card').trigger('click'); await flushPromises()
  }
  resolvePending(phase === 'creation' ? { id: 'new' } : { ...initial, id: 'new' })
  await flushPromises()
  expect(scroll).not.toHaveBeenCalled()
  if (phase !== 'unmount') expect(wrapper.get('.run-technical-values > b').text()).toBe('Run r')
})

it.each(['draft', 'published'])('collapses all %s Run metadata, preserves polling toggles, and resets for another Run or page entry', async revisionKind => {
  const draftChecksum = '367331c3c2d3594cbc4e4cbfbe98b2c2275393440a2f41e316a89cd9c4f6f570'
  const flowChecksum = '7f2929d3d102e3a0bdee6e45c4347da297be77f1500dac7e0892f4e1eb46e493'
  const detail = { ...initial, source_definition_checksum: draftChecksum, compiled_checksum: flowChecksum,
    execution_snapshot_id: 'snapshot-full-id', parent_flow_run_id: 'parent-full-id', revision_kind: revisionKind,
    node_count: 3, edge_count: 2, started_at: '2026-08-28T12:16:38Z', created_at: '2026-08-28T12:16:36Z' }
  api.flowRuns.mockResolvedValue([{ id: 'r', debug_input_snapshot_id: 's' }, { id: 'next', debug_input_snapshot_id: 's2' }])
  api.flowRun.mockImplementation(async id => ({ ...detail, id }))
  wrapper = mount(DataFlowDebugView, { attachTo: document.body }); await flushPromises()
  const details = wrapper.get('.run-technical-details')
  expect(details.element.tagName).toBe('DETAILS')
  expect(details.get('summary').text()).toBe('技术详情')
  expect(details.element.open).toBe(false)
  expect(details.get('.run-technical-values').isVisible()).toBe(false)
  expect(wrapper.get('.run-provenance').element.children).toHaveLength(1)
  expect(wrapper.get('.run-provenance').element.firstElementChild).toBe(details.element)
  const metadata = details.findAll('.run-technical-values > b, .run-technical-values > span, .run-technical-values > small')
  expect(metadata).toHaveLength(5)
  for (const field of metadata) expect(field.isVisible()).toBe(false)
  expect(metadata[0].text()).toBe('Run r')
  expect(metadata[1].text()).toBe(`来源：${revisionKind === 'draft' ? '当前草稿（启动时冻结）' : '已发布 Revision'} · r2`)
  expect(metadata[2].text()).toBe('3 节点 · 2 连线')
  expect(metadata[3].text()).toMatch(/启动时间：.+ · 入队：.+/)
  expect(metadata[4].text()).toBe('本次运行使用不可变执行快照；继续编辑草稿不会改变本次运行。')
  expect(wrapper.get('.dag-toolbar').text()).toContain('运行状态：running')
  expect(wrapper.get('.dag-toolbar small').isVisible()).toBe(true)
  expect(wrapper.get('.dag-toolbar').text()).not.toContain('snapshot-full-id')
  expect(wrapper.text()).not.toContain('查看本次运行 DAG')
  await details.get('summary').trigger('click'); await flushPromises()
  expect(details.element.open).toBe(true)
  expect(details.get('.run-technical-values').isVisible()).toBe(true)
  for (const field of metadata) expect(field.isVisible()).toBe(true)
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
  expect(wrapper.get('.run-technical-values > b').text()).toBe('Run next')
  await wrapper.get('.run-technical-details summary').trigger('click'); await flushPromises()
  wrapper.unmount()
  wrapper = mount(DataFlowDebugView, { attachTo: document.body }); await flushPromises()
  expect(wrapper.get('.run-technical-details').element.open).toBe(false)
})

it.each(['阶段视图', '最终结果'])('uses the existing DAG tab to return from %s to the same frozen Run', async view => {
  wrapper = mount(DataFlowDebugView, { attachTo: document.body }); await flushPromises()
  const canvas = wrapper.findComponent({ name: 'DataForgeFlowCanvas' })
  const nodes = canvas.props('nodes'), edges = canvas.props('edges')
  const runRequests = api.flowRun.mock.calls.length
  const dagTabs = wrapper.findAll('.dag-toolbar button').filter(button => button.text() === '执行 DAG')
  expect(dagTabs).toHaveLength(1)
  expect(wrapper.text()).not.toContain('查看本次运行 DAG')
  await clickButton(view)
  expect(canvas.isVisible()).toBe(false)
  await dagTabs[0].trigger('click'); await flushPromises()
  expect(canvas.isVisible()).toBe(true)
  expect(dagTabs[0].classes()).toContain('active')
  expect(canvas.props('nodes')).toBe(nodes)
  expect(canvas.props('edges')).toBe(edges)
  expect(api.flowRun).toHaveBeenCalledTimes(runRequests)
  expect(api.createDebugRun).not.toHaveBeenCalled()
  expect(api.debugRunPreflight).not.toHaveBeenCalled()
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
