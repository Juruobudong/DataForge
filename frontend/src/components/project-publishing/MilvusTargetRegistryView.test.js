import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MilvusTargetRegistryView from '../../views/business/MilvusTargetRegistryView.vue'
import { api } from '../../api/platform'

vi.mock('../../api/platform', () => ({ api: Object.fromEntries([
  'instance', 'milvusTargets', 'sharedDeployments', 'createMilvusTarget', 'patchMilvusTarget',
  'verifyMilvusTarget', 'checkMilvusTargetHealth', 'checkMilvusTargetCollections', 'putAuthoringMilvusTarget',
].map(name => [name, vi.fn()])) }))

const target = (overrides = {}) => ({
  id: 'milvus_dataforge_central_test', name: 'DataForge 中心测试 Milvus',
  milvus_url: 'http://milvus-central-test:19531', token_configured: false,
  verification_status: 'verified', current_revision_id: 'mtrev-test', candidate_revision_id: null,
  health_status: 'healthy', health_checked_at: '2026-08-30T10:00:00+00:00',
  health_latency_ms: 5, health_error: null, ...overrides,
})

beforeEach(() => {
  vi.resetAllMocks()
  api.instance.mockResolvedValue({ instance_mode: 'central', authoring_milvus_target: null })
  api.milvusTargets.mockResolvedValue([target()])
  api.sharedDeployments.mockResolvedValue([])
  api.checkMilvusTargetHealth.mockResolvedValue(target({
    health_status: 'unavailable', health_error: 'connection refused',
  }))
  api.checkMilvusTargetCollections.mockResolvedValue({
    target_id: 'milvus_dataforge_central_test', status: 'available',
    collection_count: 3, dataforge_collection_count: 2,
    dataforge_collections: ['dataforge_qa_question', 'dataforge_text_knowledge'], error: null,
  })
})

describe('Milvus registry live health', () => {
  it('labels the authoring role as the default knowledge write target, not an environment', async () => {
    api.instance.mockResolvedValue({ instance_mode: 'central', authoring_milvus_target: target() })
    const wrapper = mount(MilvusTargetRegistryView)
    await flushPromises()
    expect(wrapper.text()).toContain('默认知识写入目标：DataForge 中心测试 Milvus')
    expect(wrapper.text()).not.toContain('知识生产服务')
    wrapper.unmount()
  })

  it('reads startup health without triggering a check when mounted', async () => {
    const wrapper = mount(MilvusTargetRegistryView)
    await flushPromises()
    expect(wrapper.text()).toContain('配置与健康')
    expect(wrapper.text()).toContain('当前可用')
    expect(wrapper.text()).toContain('5 ms')
    expect(api.checkMilvusTargetHealth).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('checks only the current revision when the user requests it', async () => {
    const wrapper = mount(MilvusTargetRegistryView)
    await flushPromises()
    const check = wrapper.findAll('button').find(button => button.text() === '检查当前连接')
    await check.trigger('click')
    await flushPromises()
    expect(api.checkMilvusTargetHealth).toHaveBeenCalledWith('milvus_dataforge_central_test')
    expect(wrapper.text()).toContain('当前连接不可达，现有配置与绑定保持不变')
    wrapper.unmount()
  })

  it('checks Collection names only after the user explicitly requests it', async () => {
    const wrapper = mount(MilvusTargetRegistryView)
    await flushPromises()
    expect(api.checkMilvusTargetCollections).not.toHaveBeenCalled()
    const check = wrapper.findAll('button').find(button => button.text() === '检查 Collection')
    await check.trigger('click')
    await flushPromises()
    expect(api.checkMilvusTargetCollections).toHaveBeenCalledWith('milvus_dataforge_central_test')
    expect(wrapper.text()).toContain('共 3 个')
    expect(wrapper.text()).toContain('DataForge 2 个')
    expect(wrapper.text()).toContain('dataforge_qa_question、dataforge_text_knowledge')
    wrapper.unmount()
  })
})
