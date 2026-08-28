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
function response(body, offset = 0) { return { items: [{ canonical_content: body, data_json: { question: '问题', answer: '答案' }, source_anchor: 'a.pdf#chunk-1', evidence_text: '原文证据', anchor_json: { page: 1 } }], total: 51, offset, limit: 50, has_more: offset === 0 } }
const button = text => wrapper.findAll('button').find(item => item.text().includes(text))
beforeEach(() => { api.sinkPreviewCandidates.mockImplementation(async (id, preview, offset) => response(`${preview}-${offset}`, offset)) })
afterEach(() => wrapper?.unmount())

it('renders full content/evidence and maintains independent pagination per output without poll refetch', async () => {
  const detail = run()
  wrapper = mount(FinalResultsPanel, { props: { run: detail } })
  await flushPromises()
  expect(api.sinkPreviewCandidates).toHaveBeenLastCalledWith('r', 'r-text', 0, 50)
  expect(wrapper.text()).toContain('r-text-0')
  await button('下一页').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('r-text-50')
  await button('查看详情').trigger('click')
  expect(wrapper.text()).toContain('原文证据')
  expect(wrapper.text()).toContain('a.pdf#chunk-1')
  await button('问答').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('问题')
  expect(api.sinkPreviewCandidates).toHaveBeenLastCalledWith('r', 'r-qa', 0, 50)
  await button('文本').trigger('click'); await flushPromises()
  expect(api.sinkPreviewCandidates).toHaveBeenLastCalledWith('r', 'r-text', 50, 50)
  const calls = api.sinkPreviewCandidates.mock.calls.length
  await wrapper.setProps({ run: structuredClone(detail) }); await flushPromises()
  expect(api.sinkPreviewCandidates).toHaveBeenCalledTimes(calls)
  expect(wrapper.get('[aria-label="结果变更预览"]').text()).toContain('不变 50')
  expect(wrapper.text()).toContain('最终候选 51 条')
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
