import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import DocumentLibraryDetailView from '../DocumentLibraryDetailView.vue'
import { api } from '../../../api/platform'

const router = vi.hoisted(() => ({ route: null, push: vi.fn() }))
vi.mock('vue-router', () => ({ useRoute: () => router.route, useRouter: () => ({ push: router.push }) }))
vi.mock('../../../api/platform', () => ({ api: Object.fromEntries([
  'documentLibraries', 'documentTree', 'librarySources', 'documentTemplateBindings', 'flowTemplates',
  'sourceDownloadUrl', 'approveDocumentSourcesBatch', 'documentDeletionPreflight', 'requestDocumentDeletion',
].map(name => [name, vi.fn()])) }))

const source = (id, overrides = {}) => ({
  id, original_filename: `${id}.txt`, relative_path: `${id}.txt`, status: 'uploaded', updated_at: '2026-08-31',
  version: { id: `version-${id}`, activation_no: 1, candidate_chunk_set_id: `chunks-${id}`,
    active_chunk_set_id: null, preparation_status: 'completed', review_status: 'pending', chunk_count: 2 }, ...overrides,
})
const successful = (id = 'guide') => ({ counts: { approved: 1, already_approved: 0, skipped: 0, failed: 0 },
  results: [{ source_id: id, source_version_id: `version-${id}`, status: 'approved', message: '审核通过' }] })
let wrapper
beforeEach(() => {
  vi.resetAllMocks()
  router.route = reactive({ params: { libraryId: 'lib-a' } })
  api.documentLibraries.mockResolvedValue([{ id: 'lib-a', name: '产品资料' }, { id: 'lib-b', name: '项目文档' }])
  api.documentTree.mockResolvedValue({ children: [] })
  api.librarySources.mockResolvedValue({ items: [source('guide'), source('policy')], total: 90 })
  api.documentTemplateBindings.mockResolvedValue([])
  api.flowTemplates.mockResolvedValue([])
  api.sourceDownloadUrl.mockReturnValue('/download')
  api.approveDocumentSourcesBatch.mockResolvedValue(successful())
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => wrapper?.unmount())
async function start() { wrapper = mount(DocumentLibraryDetailView); await flushPromises() }
const approve = () => wrapper.get('.batch-review-button')
const all = () => wrapper.get('[aria-label="全选当前页文件"]')
const selected = () => wrapper.findAll('.source-row input[type="checkbox"]')

it('reuses current-page selection, supports indeterminate and cancels without submission', async () => {
  await start()
  expect(approve().element.disabled).toBe(true)
  await selected()[0].setValue(true)
  expect(all().element.indeterminate).toBe(true)
  expect(approve().element.disabled).toBe(false)
  await all().setValue(true)
  expect(selected().every(input => input.element.checked)).toBe(true)
  await all().setValue(false)
  expect(selected().some(input => input.element.checked)).toBe(false)
  await selected()[1].setValue(true)
  window.confirm.mockReturnValue(false)
  await approve().trigger('click')
  expect(window.confirm.mock.calls[0][0]).toContain('所选 1 个文件')
  expect(window.confirm.mock.calls[0][0]).toContain('知识模板将自动运行')
  expect(api.approveDocumentSourcesBatch).not.toHaveBeenCalled()
  expect(router.push).not.toHaveBeenCalled()
})

it('submits only selected current-page versions, activation and review targets', async () => {
  await start()
  await selected()[1].setValue(true)
  await approve().trigger('click')
  await flushPromises()
  expect(api.approveDocumentSourcesBatch).toHaveBeenCalledWith('lib-a', [{
    source_id: 'policy', source_version_id: 'version-policy', activation_no: 1, chunk_set_id: 'chunks-policy',
  }])
  expect(api.documentDeletionPreflight).not.toHaveBeenCalled()
})

it('renders mixed per-file outcomes, refreshes bindings and clears selection', async () => {
  api.librarySources.mockResolvedValue({ items: ['guide', 'policy', 'waiting', 'broken'].map(id => source(id)), total: 90 })
  api.approveDocumentSourcesBatch.mockResolvedValue({
    counts: { approved: 1, already_approved: 1, skipped: 1, failed: 1 },
    results: [
      { source_id: 'guide', status: 'approved', message: '审核通过' },
      { source_id: 'policy', status: 'already_approved', message: '未重复调度' },
      { source_id: 'waiting', status: 'skipped', message: '解析与分块尚未完成' },
      { source_id: 'broken', status: 'failed', message: '审核提交失败，请刷新后重试' },
    ],
  })
  await start()
  await all().setValue(true)
  await approve().trigger('click')
  await flushPromises()
  expect(wrapper.get('[role="status"]').text()).toContain('通过 1，此前已通过 1，跳过 1，失败 1')
  expect(wrapper.findAll('.batch-review-results li')).toHaveLength(4)
  expect(wrapper.get('.batch-review-results').text()).toContain('waiting.txt')
  expect(wrapper.get('.batch-review-results').text()).toContain('解析与分块尚未完成')
  expect(api.librarySources).toHaveBeenCalledTimes(2)
  expect(api.documentTemplateBindings).toHaveBeenCalledTimes(2)
  expect(selected().every(input => !input.element.checked)).toBe(true)
  await wrapper.get('.batch-review-results button').trigger('click')
  expect(router.push).toHaveBeenCalledWith('/business/documents/lib-a/sources/guide/versions/version-guide/review')
})

it('blocks duplicate clicks, selection, refresh and review navigation while submitting', async () => {
  let complete
  api.approveDocumentSourcesBatch.mockReturnValue(new Promise(resolve => { complete = resolve }))
  await start()
  await selected()[0].setValue(true)
  await approve().trigger('click')
  await approve().trigger('click')
  expect(api.approveDocumentSourcesBatch).toHaveBeenCalledTimes(1)
  expect(wrapper.get('fieldset').element.disabled).toBe(true)
  expect(all().element.matches(':disabled')).toBe(true)
  expect(approve().text()).toBe('审核提交中…')
  await wrapper.get('.source-row').trigger('click')
  expect(router.push).not.toHaveBeenCalled()
  complete(successful())
  await flushPromises()
  expect(wrapper.get('fieldset').element.disabled).toBe(false)
})

it.each(['route', 'unmount'])('ignores a late batch response after %s', async mode => {
  let complete
  api.approveDocumentSourcesBatch.mockReturnValue(new Promise(resolve => { complete = resolve }))
  await start()
  await all().setValue(true)
  await approve().trigger('click')
  if (mode === 'route') {
    api.librarySources.mockResolvedValue({ items: [source('different')], total: 1 })
    router.route.params.libraryId = 'lib-b'
    await flushPromises()
  } else wrapper.unmount()
  const reads = api.librarySources.mock.calls.length
  complete(successful())
  await flushPromises()
  expect(api.librarySources).toHaveBeenCalledTimes(reads)
  if (mode === 'route') {
    expect(wrapper.text()).toContain('项目文档')
    expect(wrapper.find('.batch-review-results').exists()).toBe(false)
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(wrapper.get('.source-row').text()).toContain('different.txt')
  }
})

it('keeps uncertain request results explicit and does not auto-retry', async () => {
  api.approveDocumentSourcesBatch.mockRejectedValue(new Error('连接中断'))
  await start()
  await all().setValue(true)
  await approve().trigger('click')
  await flushPromises()
  expect(wrapper.get('.error').text()).toContain('结果未确认')
  expect(wrapper.find('.batch-review-results').exists()).toBe(false)
  expect(api.approveDocumentSourcesBatch).toHaveBeenCalledTimes(1)
  expect(wrapper.get('fieldset').element.disabled).toBe(false)
})

it('rejects old list responses when switching document libraries', async () => {
  let complete
  api.librarySources.mockReturnValueOnce(new Promise(resolve => { complete = resolve }))
  wrapper = mount(DocumentLibraryDetailView)
  await flushPromises()
  router.route.params.libraryId = 'lib-b'
  api.librarySources.mockResolvedValue({ items: [source('new-library')], total: 1 })
  await flushPromises()
  complete({ items: [source('old-library')], total: 1 })
  await flushPromises()
  expect(wrapper.get('.source-row').text()).toContain('new-library.txt')
  expect(wrapper.text()).not.toContain('old-library.txt')
})
