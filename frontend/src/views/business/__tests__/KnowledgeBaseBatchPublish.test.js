import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import KnowledgeBaseView from '../KnowledgeBaseView.vue'
import { api } from '../../../api/platform'

const router = vi.hoisted(() => ({ route: null, push: vi.fn() }))
vi.mock('vue-router', () => ({ useRoute: () => router.route, useRouter: () => ({ push: router.push }) }))
vi.mock('../../../api/platform', () => ({
  api: Object.fromEntries([
    'knowledgeLibraries', 'knowledgeTypes', 'knowledgeReviewSummary', 'publishKnowledgeVectors',
    'knowledgeLibraryDeleteCheck', 'deleteKnowledgeLibrary', 'knowledgeLibraryDeletionPreflight', 'requestKnowledgeLibraryDeletions',
  ].map(name => [name, vi.fn()])),
  createClientRequestId: vi.fn(() => 'publish-key'),
}))

const library = (id, { vector_state = 'not_published', knowledge_type = 'text' } = {}) => ({
  id, name: `知识库-${id}`, code: id, status: 'active', vector_state, knowledge_type,
  knowledge_item_count: 2, review_required: knowledge_type === 'text', review_counts: { pending: 0, approved: 2, rejected: 0, total: 2 }, updated_at: '2026-09-01',
})
const summary = (overrides = {}) => ({
  scope: 'all_approved', review_required: true, can_publish_after_auto_approval: true,
  auto_approve_pending_count: 2, auto_approval_snapshot_digest: 'a'.repeat(64), snapshot_digest: 'b'.repeat(64), ...overrides,
})
let wrapper
beforeEach(() => {
  vi.resetAllMocks(); router.route = reactive({ query: {} })
  api.knowledgeTypes.mockResolvedValue([])
  api.knowledgeLibraries.mockResolvedValue([
    library('text'), library('graph', { knowledge_type: 'graph' }), library('ready', { vector_state: 'ready' }), library('blocked'),
  ])
  api.knowledgeReviewSummary.mockImplementation(id => {
    if (id === 'graph') return Promise.resolve(summary({ scope: 'all_active', review_required: false, can_publish: true, auto_approve_pending_count: 0, snapshot_digest: 'c'.repeat(64) }))
    if (id === 'blocked') return Promise.resolve(summary({ can_publish_after_auto_approval: false, auto_publish_issues: [{ message: '缺少 Embedding Profile' }] }))
    return Promise.resolve(summary())
  })
  api.publishKnowledgeVectors.mockImplementation(id => id === 'graph' ? Promise.reject(new Error('Target temporarily unavailable')) : Promise.resolve([]))
  vi.stubGlobal('confirm', vi.fn(() => true)); vi.stubGlobal('alert', vi.fn())
})
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals() })

it('clears the active category when its card is clicked again', async () => {
  router.route = reactive({ query: { type: 'text', keyword: '保留的查询参数' } })
  wrapper = mount(KnowledgeBaseView); await flushPromises()

  await wrapper.get('.type-card').trigger('click')

  expect(router.push).toHaveBeenCalledWith({
    path: '/business/knowledge', query: { keyword: '保留的查询参数' },
  })
})

it('publishes every eligible non-ready library in the current filter and reports independent outcomes', async () => {
  wrapper = mount(KnowledgeBaseView); await flushPromises()
  expect(wrapper.get('.batch-publish-button').text()).toBe('一键审核并入库（3）')

  await wrapper.get('.batch-publish-button').trigger('click'); await flushPromises(); await flushPromises()

  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('2 个知识库'))
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('自动通过 2 条待审核知识'))
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('跳过 1 个 Vector Ready 知识库'))
  expect(api.knowledgeReviewSummary).toHaveBeenCalledTimes(3)
  expect(api.knowledgeReviewSummary).not.toHaveBeenCalledWith('ready')
  expect(api.publishKnowledgeVectors).toHaveBeenCalledWith('text', expect.objectContaining({
    scope: 'all_approved', expected_snapshot_digest: 'a'.repeat(64), approve_pending: true, idempotency_key: 'publish-key',
  }))
  expect(api.publishKnowledgeVectors).toHaveBeenCalledWith('graph', expect.objectContaining({
    scope: 'all_active', expected_snapshot_digest: 'c'.repeat(64), approve_pending: false,
  }))
  expect(wrapper.text()).toContain('已提交构建')
  expect(wrapper.text()).toContain('已跳过（Vector Ready）')
  expect(wrapper.text()).toContain('提交失败')
  expect(wrapper.text()).toContain('未提交')
  expect(wrapper.text()).toContain('缺少 Embedding Profile')
})

it('does not submit any library when the unified confirmation is cancelled', async () => {
  confirm.mockReturnValue(false)
  wrapper = mount(KnowledgeBaseView); await flushPromises()

  await wrapper.get('.batch-publish-button').trigger('click'); await flushPromises()

  expect(api.publishKnowledgeVectors).not.toHaveBeenCalled()
})

it('locks the batch action while preflight is still running', async () => {
  let resolveSummary
  const pendingSummary = new Promise(resolve => { resolveSummary = resolve })
  api.knowledgeReviewSummary.mockReturnValue(pendingSummary)
  wrapper = mount(KnowledgeBaseView); await flushPromises()

  await wrapper.get('.batch-publish-button').trigger('click'); await flushPromises()

  expect(wrapper.get('.batch-publish-button').attributes('disabled')).toBeDefined()
  expect(wrapper.get('.batch-publish-button').text()).toBe('正在提交…')
  resolveSummary(summary())
  await flushPromises(); await flushPromises()
})
