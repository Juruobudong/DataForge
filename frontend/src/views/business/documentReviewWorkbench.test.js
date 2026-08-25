import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const router = readFileSync(new URL('../../router/index.js', import.meta.url), 'utf8')
const listing = readFileSync(new URL('./DocumentLibraryDetailView.vue', import.meta.url), 'utf8')
const workbench = readFileSync(new URL('./DocumentReviewWorkbenchView.vue', import.meta.url), 'utf8')
const preview = readFileSync(new URL('../../components/source-review/SourcePreviewPane.vue', import.meta.url), 'utf8')
const pdfPreview = readFileSync(new URL('../../components/source-review/PdfSourcePreview.vue', import.meta.url), 'utf8')
const docxPreview = readFileSync(new URL('../../components/source-review/DocxSourcePreview.vue', import.meta.url), 'utf8')
const chunkCard = readFileSync(new URL('../../components/source-review/ChunkCard.vue', import.meta.url), 'utf8')

test('document list routes every review entry to the immutable-version workbench', () => {
  assert.match(router, /documents\/:libraryId\/sources\/:sourceId\/versions\/:versionId\/review/)
  assert.match(listing, /openReviewWorkbench\(source\)/)
  assert.match(listing, /@click\.stop="openReviewWorkbench\(source\)"/)
  assert.doesNotMatch(listing, /review-workspace/)
})

test('workbench uses SourceAnchor preview, polling and independent focus/batch state', () => {
  assert.match(preview, /sourcePreviewUrl/)
  assert.doesNotMatch(preview, /<iframe/)
  assert.match(workbench, /setInterval\(load, 2000\)/)
  assert.match(workbench, /batchReviewSourceChunks/)
  assert.match(workbench, /selectedSourceAnchor/)
  assert.match(workbench, /:focused="focusedChunkId === chunk\.id"/)
  assert.match(workbench, /:checked="selectedIds\.includes\(chunk\.id\)"/)
  assert.match(chunkCard, /keydown\.enter\.space/)
})

test('PDF.js and DOCX evidence viewers expose multi-position highlights and explicit fallback states', () => {
  assert.match(pdfPreview, /import\('pdfjs-dist'\)/)
  assert.match(pdfPreview, /source-highlight/)
  assert.match(pdfPreview, /scrollIntoView/)
  assert.match(docxPreview, /data-source-block/)
  assert.match(docxPreview, /highlighted/)
  assert.match(preview, /PdfSourcePreview/)
  assert.match(preview, /DocxSourcePreview/)
})
