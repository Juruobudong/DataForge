import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const router = readFileSync(new URL('../../router/index.js', import.meta.url), 'utf8')
const listing = readFileSync(new URL('./DocumentLibraryDetailView.vue', import.meta.url), 'utf8')
const workbench = readFileSync(new URL('./DocumentReviewWorkbenchView.vue', import.meta.url), 'utf8')
const pdfPreview = readFileSync(new URL('../../components/source-review/PdfSourcePreview.vue', import.meta.url), 'utf8')

test('document library exposes ParseJob plus ParsedDocument review lifecycle', () => {
  assert.match(router, /documents\/:libraryId\/sources\/:sourceId\/versions\/:versionId\/parsed/)
  assert.match(listing, /待解析/)
  assert.match(listing, /解析中/)
  assert.match(listing, /解析成功/)
  assert.match(listing, /解析失败/)
  assert.match(listing, /openParsedDocument/)
  assert.match(listing, /审阅通过所选文件/)
  assert.match(listing, /一键全选审阅通过/)
  assert.match(listing, /approveAllPage/)
  assert.doesNotMatch(listing, /FlowChunk|preparation_status/)
})

test('ParsedDocument workbench edits and approves Markdown/Grid without FlowChunk review APIs', () => {
  assert.match(workbench, /parsedDocumentContent/)
  assert.match(workbench, /parsedDocumentAnchors/)
  assert.match(workbench, /retryParseJob/)
  assert.match(workbench, /setInterval\(load, 2000\)/)
  assert.match(workbench, /规范化 Markdown/)
  assert.match(workbench, /reviewParsedDocument/)
  assert.match(workbench, /通过审阅并运行/)
  assert.match(workbench, /Markdown 校订内容/)
  assert.match(workbench, /Table Grid/)
  assert.match(workbench, /row_index/)
  assert.match(workbench, /column_index/)
  assert.doesNotMatch(workbench, /sourceReview|reviewSourceChunk|rechunkSourceVersion|进入 FlowChunk 审核/)
})

test('PDF ParsedDocument preview keeps PDF.js page and bbox positioning', () => {
  assert.match(workbench, /PdfSourcePreview/)
  assert.match(pdfPreview, /import\('pdfjs-dist'\)/)
  assert.match(pdfPreview, /source-highlight/)
  assert.match(pdfPreview, /scrollTargetWithin/)
  assert.match(pdfPreview, /visiblePageNumber/)
  assert.doesNotMatch(pdfPreview, /<iframe/)
})

test('document replacement remains versioned and downloads stay in document management', () => {
  assert.match(listing, /SOURCE_VERSION_REACTIVATION_REQUIRED/)
  assert.match(listing, /reactivateSourceVersion/)
  assert.match(listing, /sourceDownloadUrl/)
  assert.match(listing, /role="status" aria-live="polite"/)
  assert.doesNotMatch(workbench, /sourceDownloadUrl|下载原文/)
})
