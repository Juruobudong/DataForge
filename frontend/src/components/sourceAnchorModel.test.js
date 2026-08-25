import assert from 'node:assert/strict'
import test from 'node:test'
import { anchorLabel, anchorNotice, docxBlockIds, pdfHighlights, pdfTargetPages } from './source-review/sourceAnchorModel.js'

test('PDF SourceAnchor keeps ordered cross-page highlights and a readable label', () => {
  const anchor = { anchor_version: 2, precision: 'block', positions: [
    { kind: 'pdf_bbox', page: 2, page_index: 1, block_id: 'b', bbox: [.1, .2, .3, .4] },
    { kind: 'pdf_bbox', page: 1, page_index: 0, block_id: 'a', bbox: [.2, .3, .4, .5] },
  ] }
  assert.deepEqual(pdfTargetPages(anchor), [1, 2])
  assert.equal(pdfHighlights(anchor, 2)[0].block_id, 'b')
  assert.equal(anchorLabel(anchor), '跨第1–2页')
})

test('DOCX blocks deduplicate and location fallbacks remain explicit', () => {
  const anchor = { anchor_version: 2, source_type: 'docx', precision: 'parent', positions: [
    { kind: 'docx_block', block_id: 'docx:3', block_index: 3 },
    { kind: 'docx_block', block_id: 'docx:3', block_index: 3 },
    { kind: 'docx_block', block_id: 'docx:5', block_index: 5 },
  ] }
  assert.deepEqual(docxBlockIds(anchor), ['docx:3', 'docx:5'])
  assert.equal(anchorLabel(anchor), 'DOCX 第4–6块')
  assert.match(anchorNotice(anchor), /父 Chunk/)
  assert.match(anchorNotice({ anchor_version: 1, precision: 'page', page: 3 }), /页级定位/)
  assert.match(anchorNotice({ precision: 'unavailable' }), /不可用/)
})
