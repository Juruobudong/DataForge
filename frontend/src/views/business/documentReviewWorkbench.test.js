import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const router = readFileSync(new URL('../../router/index.js', import.meta.url), 'utf8')
const listing = readFileSync(new URL('./DocumentLibraryDetailView.vue', import.meta.url), 'utf8')
const workbench = readFileSync(new URL('./DocumentReviewWorkbenchView.vue', import.meta.url), 'utf8')
const preview = readFileSync(new URL('../../components/source-review/SourcePreviewPane.vue', import.meta.url), 'utf8')

test('document list routes every review entry to the immutable-version workbench', () => {
  assert.match(router, /documents\/:libraryId\/sources\/:sourceId\/versions\/:versionId\/review/)
  assert.match(listing, /openReviewWorkbench\(source\)/)
  assert.match(listing, /@click\.stop="openReviewWorkbench\(source\)"/)
  assert.doesNotMatch(listing, /review-workspace/)
})

test('workbench uses preview URL, polling, batch review and page anchors', () => {
  assert.match(preview, /sourcePreviewUrl/)
  assert.doesNotMatch(preview, /iframe[^>]+sourceDownloadUrl/)
  assert.match(workbench, /setInterval\(load, 2000\)/)
  assert.match(workbench, /batchReviewSourceChunks/)
  assert.match(workbench, /chunk\.anchor\?\.page/)
})
