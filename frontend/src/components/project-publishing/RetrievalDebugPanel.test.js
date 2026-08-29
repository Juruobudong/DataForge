import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import RetrievalDebugPanel from './RetrievalDebugPanel.vue'
import RetrievalTaskSettings from './RetrievalTaskSettings.vue'
import ModelServicesView from '../../views/developer/ModelServicesView.vue'
import { api } from '../../api/platform'

vi.mock('../../api/platform', () => ({ api: Object.fromEntries([
  'retrievalDebugOptions', 'retrievalDebug', 'retrievalPublicTest', 'patchDeploymentTask', 'rerankerServings',
  'servingCategories', 'modelServings', 'embeddingServings', 'createRerankerServing',
  'patchRerankerServing', 'testRerankerServing',
].map(name => [name, vi.fn()])) }))
const reranker = { id: 'r1', serving_code: 'bge_reranker_large', name: 'BGE Large', model_name: 'bge-reranker-large',
  category: 'reranker', provider_type: 'cohere-compatible-rerank', is_default: true, is_enabled: true, credential_configured: true,
  last_check_status: 'not_checked', timeout_seconds: 120, max_retries: 2, max_batch_size: 32, max_concurrency: 4 }
const options = () => ({ tasks: [{ task_code: 'lookup', task_name: 'Lookup', top_k: 10, final_top_k: 5,
  reranker_serving_code: 'bge_reranker_large', org_routes: [{ org_code: 'general' }], filter_fields: { source_knowledge_id: 'VARCHAR' } }],
  versions: [{ id: 'v1', version_no: 1, status: 'published' }], rerankers: [reranker] })
const report = (status = 'completed') => ({ status, route_mode: 'draft', checksum: 'abc', version_no: null,
  latency_ms: 8, experimental: false, baseline: { top_k: 10 }, effective: { top_k: 10 }, stages: [
    { key: 'recall', status: 'completed', latency_ms: 2, data: { candidates: [] } },
    { key: 'reranker', status: status === 'failed' ? 'failed' : 'skipped', latency_ms: 2, data: { reason: '未启用重排' }, error: status === 'failed' ? 'Reranker timeout' : undefined },
    { key: 'final', status: status === 'failed' ? 'skipped' : 'completed', latency_ms: 0, data: status === 'failed' ? {} : { top_k: 5, count: 0, results: [] } },
  ] })
const publicReport = () => ({ schema: 'dataforge.retrieval-result.v1', contract_version: 1, request_id: 'req', latency_ms: 9,
  route: { project_code: 'project-a', deployment_code: 'central', release_stage: 'test', task_code: 'lookup', org_code: 'general', route_version: 3, route_checksum: 'sum' },
  policy: { top_k: 10, final_top_k: 5, reranker_enabled: true },
  results: [{ rank: 1, citation_id: 'C1', content: 'Public content', data: {}, score: { kind: 'reranker', value: 0.9, direction: 'descending' }, knowledge_library_id: 'kl1', asset_version_no: 2, source_knowledge_id: 'item1', evidence: [{ source_version_id: 'sv1', source_chunk_id: 'sc1', source_name: '指南', evidence_text: 'Evidence text' }] }],
  context: { text: '[C1] Public content', truncated: false, total_characters: 19 } })
let wrapper
const button = text => wrapper.findAll('button').find(item => item.text() === text)
beforeEach(() => {
  vi.resetAllMocks()
  api.retrievalDebugOptions.mockResolvedValue(options())
  api.retrievalDebug.mockResolvedValue(report())
  api.retrievalPublicTest.mockResolvedValue(publicReport())
  api.rerankerServings.mockResolvedValue([reranker])
  api.servingCategories.mockResolvedValue([{ key: 'llm', available: true }, { key: 'embedding', available: true }, { key: 'reranker', available: true }])
  api.modelServings.mockResolvedValue([]); api.embeddingServings.mockResolvedValue([])
})
afterEach(() => wrapper?.unmount())
async function setup() {
  wrapper = mount(RetrievalDebugPanel, { props: { deploymentId: 'pd1', releaseStage: 'test', projectCode: 'project-a', deploymentCode: 'central' } })
  await flushPromises(); await button('技术链路调试').trigger('click'); await flushPromises()
  await wrapper.find('textarea').setValue('question')
}

async function setupPublic() {
  wrapper = mount(RetrievalDebugPanel, { props: { deploymentId: 'pd1', releaseStage: 'test', projectCode: 'project-a', deploymentCode: 'central' } })
  await flushPromises(); await wrapper.find('textarea').setValue('question')
}

describe('public retrieval console', () => {
  it('is the default mode and executes the published public DTO without a browser token', async () => {
    await setupPublic(); await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(api.retrievalPublicTest).toHaveBeenCalledWith('pd1', { release_stage: 'test', task_code: 'lookup', org_code: 'general', query: 'question' })
    expect(api.retrievalDebug).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('/api/runtime/retrieval/v1/project-a/central/test/lookup/query')
    expect(wrapper.text()).toContain('Public content')
    expect(wrapper.text()).not.toContain('Milvus')
  })

  it('copies a curl example with a placeholder token only', async () => {
    const writeText = vi.fn().mockResolvedValue()
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    await setupPublic(); await button('复制 curl').trigger('click'); await flushPromises()
    expect(writeText.mock.calls[0][0]).toContain('<DATAFORGE_RETRIEVAL_TOKEN>')
    expect(writeText.mock.calls[0][0]).not.toContain('retrieval-token')
    expect(wrapper.text()).toContain('Token 保持占位符')
  })

  it('discards a public response after switching release stage', async () => {
    let finish
    api.retrievalPublicTest.mockReturnValue(new Promise(resolve => { finish = resolve }))
    await setupPublic(); await wrapper.find('form').trigger('submit')
    const stageSelect = wrapper.findAll('select')[0]
    await stageSelect.setValue('production'); await flushPromises()
    finish({ ...publicReport(), request_id: 'OLD-PUBLIC' }); await flushPromises()
    expect(wrapper.text()).not.toContain('OLD-PUBLIC')
    expect(api.retrievalDebugOptions.mock.lastCall[1]).toEqual(expect.objectContaining({ release_stage: 'production', route_mode: 'published' }))
  })
})

describe('retrieval diagnostics', () => {
  it('runs selected version and shows empty results, not a chat answer', async () => {
    await setup(); await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(api.retrievalDebug).toHaveBeenCalledWith('pd1', expect.objectContaining({ query: 'question', route_mode: 'draft', task_code: 'lookup', org_code: 'general', release_stage: 'test' }))
    expect(api.retrievalDebug.mock.calls[0][1]).not.toHaveProperty('overrides')
    expect(wrapper.text()).toContain('没有匹配结果')
    expect(wrapper.text()).toContain('技术模式用于定位')
  })
  it('marks temporary overrides without saving task configuration', async () => {
    await setup(); await wrapper.find('input[type="checkbox"]').setValue(true)
    const numberInputs = wrapper.findAll('input[type="number"]')
    await numberInputs[1].setValue(3)
    api.retrievalDebug.mockResolvedValue({ ...report(), experimental: true })
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(api.retrievalDebug.mock.calls[0][1].overrides.final_top_k).toBe(3)
    expect(api.patchDeploymentTask).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('本次实验')
  })
  it('discards an old response after switching stage', async () => {
    let finish
    api.retrievalDebug.mockReturnValue(new Promise(resolve => { finish = resolve }))
    await setup(); await wrapper.find('form').trigger('submit')
    await wrapper.setProps({ releaseStage: 'production' }); await flushPromises()
    finish({ ...report(), checksum: 'OLD-RESPONSE' }); await flushPromises()
    expect(wrapper.text()).not.toContain('OLD-RESPONSE')
    expect(api.retrievalDebugOptions.mock.lastCall[1].release_stage).toBe('production')
  })
  it('selects a historical version and clears old results when task context changes', async () => {
    await setup()
    await wrapper.findAll('select')[0].setValue('historical'); await flushPromises()
    await wrapper.findAll('select')[1].setValue(1); await flushPromises()
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(api.retrievalDebug.mock.lastCall[1].version_no).toBe(1)
    await wrapper.find('textarea').setValue('changed question')
    expect(wrapper.text()).not.toContain('版本配置验证')
  })
  it('shows a reranker failure without successful final results', async () => {
    api.retrievalDebug.mockResolvedValue(report('failed'))
    await setup(); await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('Reranker timeout')
    expect(wrapper.text()).not.toContain('TopK =')
  })
})

it('saves task retrieval settings explicitly and keeps reranker separate', async () => {
  wrapper = mount(RetrievalTaskSettings, { props: { deploymentId: 'pd1', tasks: [{ id: 'dt1', task: { name: 'Lookup' }, top_k: 10, final_top_k: 5, reranker_serving_code: null, enabled: true }] } })
  await flushPromises(); await wrapper.find('select').setValue('dt1')
  await wrapper.findAll('select')[1].setValue('bge_reranker_large')
  await wrapper.find('form').trigger('submit'); await flushPromises()
  expect(api.patchDeploymentTask).toHaveBeenCalledWith('pd1', 'dt1', expect.objectContaining({ reranker_serving_code: 'bge_reranker_large', final_top_k: 5 }))
  expect(wrapper.text()).toContain('尚未发布')
})

it('edits Reranker using its own endpoint and exposes connection success', async () => {
  api.patchRerankerServing.mockResolvedValue(reranker)
  api.testRerankerServing.mockResolvedValue({ ...reranker, last_check_status: 'healthy' })
  wrapper = mount(ModelServicesView); await flushPromises()
  await button('Reranker').trigger('click'); await button('编辑').trigger('click')
  await wrapper.find('form').trigger('submit'); await flushPromises()
  expect(api.patchRerankerServing).toHaveBeenCalledWith('r1', expect.objectContaining({ provider_type: 'cohere-compatible-rerank', max_batch_size: 32, max_concurrency: 4 }))
  expect(api.patchRerankerServing.mock.lastCall[1]).not.toHaveProperty('category')
  expect(api.patchRerankerServing.mock.lastCall[1]).not.toHaveProperty('dimension')
  await button('测试').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('连接测试成功')
})
