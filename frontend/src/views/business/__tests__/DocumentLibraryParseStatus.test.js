import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import DocumentLibraryDetailView from '../DocumentLibraryDetailView.vue'
import { api } from '../../../api/platform'

const router = vi.hoisted(() => ({ route: null, push: vi.fn() }))
vi.mock('vue-router', () => ({ useRoute: () => router.route, useRouter: () => ({ push: router.push }) }))
vi.mock('../../../api/platform', () => ({ api: Object.fromEntries([
  'documentLibraries', 'documentTree', 'librarySources', 'documentTemplateBindings', 'flowTemplates',
  'sourceDownloadUrl', 'documentDeletionPreflight', 'requestDocumentDeletion', 'batchReviewParsedDocuments',
].map(name => [name, vi.fn()])) }))

const source = (id, parseStatus, reviewStatus = 'pending') => ({
  id, original_filename: `${id}.txt`, relative_path: `${id}.txt`, status: 'uploaded', updated_at: '2026-08-31',
  version: { id: `version-${id}`, parse_status: parseStatus, parsed_document: parseStatus === 'completed' ? {
    id: `parsed-${id}`, review_status: reviewStatus, content_digest: 'a'.repeat(64), anchor_map_digest: 'b'.repeat(64),
  } : null },
})
let wrapper
beforeEach(() => {
  vi.resetAllMocks(); router.route = reactive({ params: { libraryId: 'lib-a' } })
  api.documentLibraries.mockResolvedValue([{ id: 'lib-a', name: '产品资料' }])
  api.documentTree.mockResolvedValue({ children: [] })
  api.librarySources.mockResolvedValue({ items: [
    source('pending', 'pending'), source('running', 'running'), source('ready', 'completed'), source('approved', 'completed', 'approved'), source('failed', 'failed'),
  ], total: 5 })
  api.documentTemplateBindings.mockResolvedValue([]); api.flowTemplates.mockResolvedValue([])
  api.sourceDownloadUrl.mockReturnValue('/download')
  api.batchReviewParsedDocuments.mockResolvedValue({ approved: [{}], already_approved: [], skipped: [], failed: [], dispatches: [] })
  vi.stubGlobal('confirm', vi.fn(() => true))
})
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals() })

  it('renders parsing and ParsedDocument review states without FlowChunk review', async () => {
  wrapper = mount(DocumentLibraryDetailView); await flushPromises()
  expect(wrapper.text()).toContain('待解析')
  expect(wrapper.text()).toContain('解析中')
  expect(wrapper.text()).toContain('解析成功')
  expect(wrapper.text()).toContain('解析失败')
    expect(wrapper.text()).toContain('待审阅')
    expect(wrapper.text()).toContain('审阅通过所选文件')
    expect(wrapper.text()).not.toContain('Chunk')
})

it('opens the immutable SourceVersion ParsedDocument page', async () => {
  wrapper = mount(DocumentLibraryDetailView); await flushPromises()
  await wrapper.findAll('.source-row')[2].trigger('click')
  expect(router.push).toHaveBeenCalledWith('/business/documents/lib-a/sources/ready/versions/version-ready/parsed')
})

  it('approves only reviewable files from the selected current page', async () => {
  wrapper = mount(DocumentLibraryDetailView); await flushPromises()
  const all = wrapper.get('[aria-label="全选当前页文件"]')
  await all.setValue(true)
    expect(wrapper.findAll('.source-row input[type="checkbox"]').every(input => input.element.checked)).toBe(true)
    expect(wrapper.text()).toContain('已选当前页 5 个')
    await wrapper.get('.batch-review-button').trigger('click'); await flushPromises()
    expect(confirm).toHaveBeenCalledOnce()
    expect(api.batchReviewParsedDocuments).toHaveBeenCalledWith('lib-a', [{
      source_id: 'ready', source_version_id: 'version-ready', parsed_document_id: 'parsed-ready',
      expected_content_digest: 'a'.repeat(64), expected_anchor_map_digest: 'b'.repeat(64),
    }])
  })

it('one-click approves every reviewable file on the current page without confirmation', async () => {
  wrapper = mount(DocumentLibraryDetailView); await flushPromises()

  expect(wrapper.get('.batch-review-all-button').text()).toBe('一键全选审阅通过（1）')
  await wrapper.get('.batch-review-all-button').trigger('click'); await flushPromises()

  expect(confirm).not.toHaveBeenCalled()
  expect(api.batchReviewParsedDocuments).toHaveBeenCalledWith('lib-a', [{
    source_id: 'ready', source_version_id: 'version-ready', parsed_document_id: 'parsed-ready',
    expected_content_digest: 'a'.repeat(64), expected_anchor_map_digest: 'b'.repeat(64),
  }])
})

it('disables one-click review when the current page has no pending parsed documents', async () => {
  api.librarySources.mockResolvedValue({ items: [
    source('pending', 'pending'), source('approved', 'completed', 'approved'), source('failed', 'failed'),
  ], total: 3 })
  wrapper = mount(DocumentLibraryDetailView); await flushPromises()

  expect(wrapper.get('.batch-review-all-button').attributes('disabled')).toBeDefined()
  expect(wrapper.get('.batch-review-all-button').text()).toBe('一键全选审阅通过（0）')
})
