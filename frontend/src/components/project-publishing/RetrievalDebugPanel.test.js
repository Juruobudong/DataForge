import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import RetrievalDebugPanel from './RetrievalDebugPanel.vue'
import RetrievalTaskSettings from './RetrievalTaskSettings.vue'
import ModelServicesView from '../../views/developer/ModelServicesView.vue'
import { api } from '../../api/platform'

vi.mock('../../api/platform', () => ({ api: Object.fromEntries([
  'retrievalDebugOptions', 'retrievalDebug', 'retrievalPublicTest', 'patchReleaseTask', 'rerankerServings',
  'servingCategories', 'modelServings', 'embeddingServings', 'createRerankerServing',
  'patchRerankerServing', 'testRerankerServing',
].map(name => [name, vi.fn()])) }))

const reranker = { id: 'r1', serving_code: 'bge_reranker_large', name: 'BGE Large', model_name: 'bge-reranker-large',
  category: 'reranker', provider_type: 'cohere-compatible-rerank', is_default: true, is_enabled: true,
  credential_configured: true, last_check_status: 'not_checked', timeout_seconds: 120, max_retries: 2,
  max_batch_size: 32, max_concurrency: 4 }
const options = () => ({ tasks: [{ task_code: 'lookup', task_name: 'Lookup', top_k: 10, final_top_k: 5,
  reranker_serving_code: 'bge_reranker_large', org_routes: [{ org_code: 'general' }],
  filter_fields: { source_knowledge_id: 'VARCHAR' } }],
versions: [
  { id: 'v2', version_no: 2, status: 'frozen', is_published: false },
  { id: 'v1', version_no: 1, status: 'frozen', is_published: true },
], rerankers: [reranker] })
const report = (status = 'completed') => ({ status, route_mode: 'draft', checksum: 'abc', version_no: null,
  latency_ms: 8, experimental: false, baseline: { top_k: 10 }, effective: { top_k: 10 }, stages: [
    { key: 'routing', status: 'completed', latency_ms: 1, data: { project: { name: 'Project' }, deployment: { name: 'Central' }, task_code: 'lookup', org_code: 'general', libraries: [] } },
    { key: 'embedding', status: 'completed', latency_ms: 1, data: { expected_dimension: 1024 } },
    { key: 'recall', status: 'completed', latency_ms: 2, data: { candidates: [], metric_type: 'COSINE' } },
    { key: 'reranker', status: status === 'failed' ? 'failed' : 'skipped', latency_ms: 2, data: { reason: '未启用重排' }, error: status === 'failed' ? 'Reranker timeout' : undefined },
    { key: 'final', status: status === 'failed' ? 'skipped' : 'completed', latency_ms: 0, data: status === 'failed' ? {} : { top_k: 5, count: 0, results: [] } },
    { key: 'context', status: status === 'failed' ? 'skipped' : 'completed', latency_ms: 1, data: { text: '' } },
    { key: 'evidence', status: status === 'failed' ? 'skipped' : 'completed', latency_ms: 1, data: { citations: [] } },
  ] })
const publicReport = () => ({ schema: 'dataforge.retrieval-result.v1', contract_version: 1, request_id: 'req-public', latency_ms: 9,
  route: { project_code: 'project-a', deployment_code: 'central', release_stage: 'test', task_code: 'lookup', org_code: 'general', route_version: 3, route_checksum: 'sum' },
  policy: { top_k: 10, final_top_k: 5, reranker_enabled: true },
  results: [{ rank: 1, citation_id: 'C1', content: 'Public content', data: {}, score: { kind: 'reranker', value: 0.9, direction: 'descending' }, knowledge_library_id: 'kl1', asset_version_no: 2, source_knowledge_id: 'item1', evidence: [{ source_version_id: 'sv1', source_chunk_id: 'sc1', source_name: '指南', evidence_text: 'Evidence text' }] }],
  context: { text: '[C1] Public content', truncated: false, total_characters: 19 } })
const envelope = (status = 200, trace = { ...report(), request_id: 'req-admin' }) => ({
  request: { method: 'POST', path: '/api/runtime/retrieval/v1/project-a/central/test/lookup/query', headers: { Authorization: 'Bearer <DATAFORGE_RETRIEVAL_TOKEN>' }, body: { org_code: 'general', query: 'question' } },
  response: { status_code: status, request_id: 'req-admin', body: status === 200 ? publicReport() : { error: { code: 'retrieval_unavailable', message: '不可用' }, request_id: 'req-admin' } },
  trace,
})

let wrapper
const button = text => wrapper.findAll('button').find(item => item.text() === text)
beforeEach(() => {
  vi.resetAllMocks()
  api.retrievalDebugOptions.mockResolvedValue(options())
  api.retrievalDebug.mockResolvedValue(report())
  api.retrievalPublicTest.mockResolvedValue(envelope())
  api.rerankerServings.mockResolvedValue([reranker])
  api.servingCategories.mockResolvedValue([{ key: 'llm', available: true }, { key: 'embedding', available: true }, { key: 'reranker', available: true }])
  api.modelServings.mockResolvedValue([]); api.embeddingServings.mockResolvedValue([])
})
afterEach(() => wrapper?.unmount())

async function mountPanel() {
  wrapper = mount(RetrievalDebugPanel, { props: { projectId: 'project1', releaseStage: 'test', projectCode: 'project-a', deploymentCode: 'central' } })
  await flushPromises()
}

describe('three-level retrieval validation', () => {
  it('defaults to draft retrieval effect and shows result layers', async () => {
    await mountPanel(); await wrapper.find('textarea').setValue('question')
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(api.retrievalDebug).toHaveBeenCalledWith('project1', expect.objectContaining({ route_mode: 'draft', query: 'question' }))
    expect(api.retrievalPublicTest).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Vector Search 与候选合并')
    expect(wrapper.text()).toContain('查看完整执行链路')
  })

  it('prioritizes readable final results and keeps retrieval candidates folded', async () => {
    const populated = report()
    populated.stages.find(item => item.key === 'recall').data.candidates = [
      { asset_version_id: 'asset1', source_knowledge_id: 'very-long-internal-source-id-123456789', content: '候选正文', vector_score: 0.82 },
    ]
    populated.stages.find(item => item.key === 'final').data.results = [
      { asset_version_id: 'asset1', source_knowledge_id: 'very-long-internal-source-id-123456789', citation_id: 'C1', content: '便于阅读的最终结果', vector_score: 0.82 },
    ]
    api.retrievalDebug.mockResolvedValue(populated)
    await mountPanel(); await wrapper.find('textarea').setValue('question')
    await wrapper.find('form').trigger('submit'); await flushPromises()
    const layout = wrapper.find('.effect-result-layout')
    expect(layout.element.firstElementChild.classList.contains('final-results')).toBe(true)
    expect(wrapper.find('.final-list .result-card').text()).toContain('便于阅读的最终结果')
    expect(wrapper.find('.final-list .result-card').text()).toContain('[C1]')
    expect(wrapper.find('.result-process .result-stage').attributes('open')).toBeUndefined()
    expect(wrapper.find('.result-source').text()).toContain('来源标识')
  })

  it('keeps temporary retrieval overrides out of saved task settings', async () => {
    await mountPanel(); await wrapper.find('textarea').setValue('question')
    await wrapper.find('input[type="checkbox"]').setValue(true)
    const numbers = wrapper.findAll('input[type="number"]'); await numbers[1].setValue(3)
    api.retrievalDebug.mockResolvedValue({ ...report(), experimental: true })
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(api.retrievalDebug.mock.lastCall[1].overrides.final_top_k).toBe(3)
    expect(api.patchReleaseTask).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('本次实验')
  })

  it('tests the published public contract and exposes folded raw values', async () => {
    await mountPanel(); await button('公共接口').trigger('click'); await flushPromises()
    await wrapper.find('textarea').setValue('question'); await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(api.retrievalDebugOptions.mock.lastCall[1]).toEqual(expect.objectContaining({ release_stage: 'test', route_mode: 'published' }))
    expect(api.retrievalPublicTest).toHaveBeenCalledWith('project1', { release_stage: 'test', task_code: 'lookup', org_code: 'general', query: 'question' })
    expect(wrapper.text()).toContain('HTTP 200')
    expect(wrapper.text()).toContain('Public content')
    expect(wrapper.text()).toContain('原始 Request / Response / cURL')
  })

  it('opens the exact public trace without executing retrieval again', async () => {
    await mountPanel(); await button('公共接口').trigger('click'); await flushPromises()
    await wrapper.find('textarea').setValue('question'); await wrapper.find('form').trigger('submit'); await flushPromises()
    const calls = api.retrievalPublicTest.mock.calls.length
    await button('查看本次链路').trigger('click'); await flushPromises()
    expect(api.retrievalPublicTest).toHaveBeenCalledTimes(calls)
    expect(api.retrievalDebug).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('请求 req-admin')
    expect(wrapper.text()).toContain('路由与授权')
  })

  it('keeps a failed public partial trace available', async () => {
    const failed = envelope(503, { ...report('failed'), request_id: 'req-failed' })
    api.retrievalPublicTest.mockRejectedValue({ message: 'HTTP 503', detail: '不可用', problem: failed })
    await mountPanel(); await button('公共接口').trigger('click'); await flushPromises()
    await wrapper.find('textarea').setValue('question'); await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('HTTP 503')
    await button('查看本次链路').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('Reranker timeout')
  })

  it('selects a historical effect version and invalidates late responses on context change', async () => {
    await mountPanel()
    await wrapper.findAll('select')[0].setValue('historical'); await flushPromises()
    expect(wrapper.findAll('select')[1].text()).toContain('V1')
    expect(wrapper.findAll('select')[1].text()).not.toContain('V2')
    await wrapper.findAll('select')[1].setValue(1); await flushPromises()
    await wrapper.find('textarea').setValue('question')
    let finish
    api.retrievalDebug.mockReturnValue(new Promise(resolve => { finish = resolve }))
    await wrapper.find('form').trigger('submit')
    await wrapper.setProps({ releaseStage: 'production' }); await flushPromises()
    finish({ ...report(), checksum: 'OLD-RESPONSE' }); await flushPromises()
    expect(api.retrievalDebug.mock.calls[0][1].version_no).toBe(1)
    expect(wrapper.text()).not.toContain('OLD-RESPONSE')
    expect(api.retrievalDebugOptions.mock.lastCall[1].release_stage).toBe('production')
  })
})

it('saves embedded task retrieval settings explicitly', async () => {
  wrapper = mount(RetrievalTaskSettings, { props: { projectId: 'project1', task: { id: 'dt1', task: { name: 'Lookup' }, top_k: 10, final_top_k: 5, reranker_serving_code: null, enabled: true } } })
  await flushPromises(); await wrapper.find('select').setValue('bge_reranker_large')
  await wrapper.find('form').trigger('submit'); await flushPromises()
  expect(api.patchReleaseTask).toHaveBeenCalledWith('project1', 'dt1', expect.objectContaining({ reranker_serving_code: 'bge_reranker_large', final_top_k: 5 }))
  expect(wrapper.text()).toContain('尚未发布')
})

it('edits Reranker using its own endpoint and exposes connection success', async () => {
  api.patchRerankerServing.mockResolvedValue(reranker)
  api.testRerankerServing.mockResolvedValue({ ...reranker, last_check_status: 'healthy' })
  wrapper = mount(ModelServicesView); await flushPromises()
  await button('Reranker').trigger('click'); await button('编辑').trigger('click')
  await wrapper.find('form').trigger('submit'); await flushPromises()
  expect(api.patchRerankerServing).toHaveBeenCalledWith('r1', expect.objectContaining({ provider_type: 'cohere-compatible-rerank', max_batch_size: 32, max_concurrency: 4 }))
  await button('测试').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('连接测试成功')
})
