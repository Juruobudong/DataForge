import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import FinalResultsPanel from '../FinalResultsPanel.vue'
import { api } from '../../../api/platform'

vi.mock('../../../api/platform', () => ({ api: { sinkPreviewCandidates: vi.fn() } }))
let wrapper
function run(id = 'r', keys = ['text', 'qa', 'graph:triple', 'graph:semantic', 'extension']) {
  return { id, status: 'completed', nodes: [], runtime_dag: { nodes: keys.map(key => ({ id: key, kind: 'knowledge_sink', output_key: key })), edges: [] },
    sink_previews: keys.map(key => ({ id: `${id}-${key}`, output_key: key, candidate_count: 51, diff: { ADD: 1, UNCHANGED: 50 } })) }
}
function response(body, offset = 0) { return { items: [{
  source_knowledge_id: 'knowledge-001', canonical_content: body,
  data_json: { question: '问题', answer: '答案', filename: 'a.pdf', chunk_index: 0 },
  source_anchor: 'a.pdf#chunk-0', source_version_ids: ['version-001'], source_chunk_id: 'chunk-001',
  source_chunk_revision_id: 'chunk-revision-001', source_review_snapshot_id: 'review-001',
  evidence_text: '原文证据', anchor_json: { page: 1, section: '诊疗建议', file: 'a.pdf', chunk_index: 0 },
}], total: 51, offset, limit: 50, has_more: offset === 0 } }
const button = text => wrapper.findAll('button').find(item => item.text().includes(text))
beforeEach(() => { api.sinkPreviewCandidates.mockImplementation(async (id, preview, offset) => response(`${preview}-${offset}`, offset)) })
afterEach(() => wrapper?.unmount())

it.each([[0, 1], [5, 1], [50, 1], [51, 2]])('shows total pages for %i results', async (total, totalPages) => {
  const result = response('最终正文')
  api.sinkPreviewCandidates.mockResolvedValue({ ...result, total, items: total ? result.items : [], has_more: total > 50 })
  wrapper = mount(FinalResultsPanel, { props: { run: run() } })
  await flushPromises()
  expect(wrapper.get('.pagination span').text()).toBe(`第 1 页 · 共 ${totalPages} 页 · 共 ${total} 条`)
  expect(button('上一页').element.disabled).toBe(true)
  expect(button('下一页').element.disabled).toBe(total <= 50)
  if (total === 0) expect(wrapper.text()).toContain('本次最终输出为 0 条')
})

it('renders full content/evidence and maintains independent pagination per output without poll refetch', async () => {
  api.sinkPreviewCandidates.mockImplementation(async (id, preview, offset) => {
    const result = response(`${preview}-${offset}`, offset)
    return preview.endsWith('-qa') ? { ...result, total: 5, has_more: false } : result
  })
  const detail = run()
  wrapper = mount(FinalResultsPanel, { props: { run: detail } })
  await flushPromises()
  expect(api.sinkPreviewCandidates).toHaveBeenLastCalledWith('r', 'r-text', 0, 50)
  expect(wrapper.text()).toContain('r-text-0')
  expect(wrapper.get('.pagination span').text()).toBe('第 1 页 · 共 2 页 · 共 51 条')
  await button('下一页').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('r-text-50')
  expect(wrapper.get('.pagination span').text()).toBe('第 2 页 · 共 2 页 · 共 51 条')
  expect(button('下一页').element.disabled).toBe(true)
  await button('查看详情').trigger('click')
  expect(wrapper.text()).toContain('原文证据')
  expect(wrapper.text()).toContain('knowledge-001')
  expect(wrapper.text()).toContain('第 1 个切片 · 第 1 页 · 章节：诊疗建议')
  expect(wrapper.text()).toContain('version-001')
  expect(wrapper.text()).toContain('chunk-revision-001')
  expect(wrapper.text()).toContain('review-001')
  await button('问答').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('问题')
  expect(api.sinkPreviewCandidates).toHaveBeenLastCalledWith('r', 'r-qa', 0, 50)
  expect(wrapper.get('.pagination span').text()).toBe('第 1 页 · 共 1 页 · 共 5 条')
  await button('文本').trigger('click'); await flushPromises()
  expect(api.sinkPreviewCandidates).toHaveBeenLastCalledWith('r', 'r-text', 50, 50)
  expect(wrapper.get('.pagination span').text()).toBe('第 2 页 · 共 2 页 · 共 51 条')
  const calls = api.sinkPreviewCandidates.mock.calls.length
  await wrapper.setProps({ run: structuredClone(detail) }); await flushPromises()
  expect(api.sinkPreviewCandidates).toHaveBeenCalledTimes(calls)
  expect(wrapper.get('[aria-label="结果变更预览"]').text()).toContain('不变 50')
  expect(wrapper.text()).toContain('最终候选 51 条')
  await button('上一页').trigger('click'); await flushPromises()
  expect(wrapper.get('.pagination span').text()).toBe('第 1 页 · 共 2 页 · 共 51 条')
})

it.each([
  ['文本知识流程', ['text']],
  ['问答知识流程', ['qa']],
  ['三元组图谱流程', ['graph:triple']],
  ['语义图谱流程', ['graph:semantic']],
  ['多产出知识流程', ['text', 'qa', 'graph:triple']],
])('%s exposes the common source and review detail', async (name, keys) => {
  wrapper = mount(FinalResultsPanel, { props: { run: run(name, keys) } })
  await flushPromises()
  await button('查看详情').trigger('click')
  const detail = wrapper.get('.result-detail').text()
  for (const value of ['知识标识', 'knowledge-001', '来源文件', 'a.pdf', '切片位置', '第 1 个切片',
    '来源版本', 'version-001', '切片标识', 'chunk-001', '切片修订', 'chunk-revision-001',
    '审核信息', '业务审核快照', '审核快照', 'review-001']) expect(detail).toContain(value)
})

it('describes builtin sample review truth without fabricating database review ids', async () => {
  const detail = run('sample', ['text'])
  detail.input_context = { input_source: 'builtin_sample', sample_code: 'reviewed-medical-v2', sample_version: '2' }
  api.sinkPreviewCandidates.mockResolvedValue({
    ...response('示例正文'), items: [{
      source_knowledge_id: 'sample-knowledge', canonical_content: '示例正文',
      data_json: { filename: 'DataForge 示例审核数据', chunk_index: 0 },
      source_version_ids: ['sample-version:reviewed-medical-v2:2'], source_chunk_id: 'sample-001',
      source_anchor: 'DataForge 示例审核数据#chunk-0', evidence_text: '示例正文',
      anchor_json: { page: 1, section: '综合评估', chunk_index: 0 },
    }], total: 1, has_more: false,
  })
  wrapper = mount(FinalResultsPanel, { props: { run: detail } }); await flushPromises()
  await button('查看详情').trigger('click')
  expect(wrapper.text()).toContain('内置审核示例 · v2 · 已审核')
  expect(wrapper.text()).toContain('不适用（内置审核示例）')
  expect(wrapper.text()).not.toContain('sample-review:')
})

it('ignores stale output/run responses and retries errors without showing old records', async () => {
  let finishOld
  api.sinkPreviewCandidates.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
  wrapper = mount(FinalResultsPanel, { props: { run: run() } }); await flushPromises()
  await button('问答').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('问题')
  finishOld(response('STALE')); await flushPromises()
  expect(wrapper.text()).not.toContain('STALE')
  api.sinkPreviewCandidates.mockRejectedValueOnce(new Error('暂时无法加载'))
  await wrapper.setProps({ run: run('new') }); await flushPromises()
  expect(wrapper.text()).toContain('暂时无法加载')
  expect(wrapper.text()).not.toContain('r-text-0')
  await button('重试加载').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('new-text-0')
})

it('renders triple and semantic details, extensions and untrusted content as text', async () => {
  const triples = { subject: '患者', predicate: '剂量', object: '5mg', data: { object_kind: 'literal', literal_datatype: 'dosage', literal_raw_value: '5mg', literal_normalized_value: 5, literal_unit: 'mg' } }
  const semantic = { source_entity: { name: '甲', type_label: '疾病', aliases: ['别名甲'] }, target_entity: { name: '乙', description: '目标描述' }, relation: { type_label: '关联', description: '关系描述内容', keywords: ['关键字'] } }
  api.sinkPreviewCandidates.mockImplementation(async (id, preview) => ({ ...response('全文'), items: [{ canonical_content: '<img src=x onerror=alert(1)>', data_json: preview.endsWith('triple') ? triples : preview.endsWith('semantic') ? semantic : { nested: { field: '扩展值' } } }] }))
  wrapper = mount(FinalResultsPanel, { props: { run: run() } }); await flushPromises()
  await button('三元组图谱').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('字面值')
  await button('查看详情').trigger('click')
  expect(wrapper.text()).toContain('5mg / 5 / mg')
  await button('语义图谱').trigger('click'); await flushPromises()
  await button('查看详情').trigger('click')
  expect(wrapper.text()).toContain('别名甲')
  expect(wrapper.text()).toContain('关系描述内容')
  await button('extension').trigger('click'); await flushPromises()
  await button('查看详情').trigger('click')
  expect(wrapper.text()).toContain('扩展值')
  expect(wrapper.find('img').exists()).toBe(false)
  expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
})

it('keeps absent branches and warns about failures even with an empty final preview', async () => {
  const detail = run('r', ['text', 'qa'])
  detail.sink_previews = [{ id: 'p', output_key: 'text', candidate_count: 0 }]
  detail.runtime_dag.nodes.unshift({ id: 'g', kind: 'operator' })
  detail.runtime_dag.edges = [{ source: 'g', target: 'text' }]
  detail.nodes = [{ node_id: 'g', status: 'failed', error: '生成失败' }]
  api.sinkPreviewCandidates.mockResolvedValue({ items: [], offset: 0, total: 0, limit: 50, has_more: false })
  wrapper = mount(FinalResultsPanel, { props: { run: detail } }); await flushPromises()
  expect(wrapper.text()).toContain('失败数量未记录')
  expect(wrapper.text()).not.toContain('成功零产出')
  await button('查看节点诊断').trigger('click')
  expect(wrapper.emitted('inspect-node')).toEqual([['g']])
  await button('问答').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('该输出尚无已暂存的最终结果')
})
