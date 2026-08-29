import assert from 'node:assert/strict'
import test from 'node:test'
import { anchorLabel, anchorNotice, docxBlockIds, pdfHighlights, pdfTargetPages } from './source-review/sourceAnchorModel.js'
import { scrollTargetWithin, visiblePageNumber } from './source-review/sourcePreviewScroll.js'

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

test('structured record anchors expose stable business-readable labels', () => {
  assert.equal(anchorLabel({ positions: [
    { kind: 'csv_record', block_id: 'csv:1', line_start: 2, line_end: 2, block_index: 1 },
    { kind: 'csv_record', block_id: 'csv:2', line_start: 3, line_end: 4, block_index: 2 },
  ] }), '第2–4行')
  assert.equal(anchorLabel({ positions: [
    { kind: 'xlsx_row', block_id: 'xlsx:0:2', sheet: '门诊', row: 2, block_index: 1 },
    { kind: 'xlsx_row', block_id: 'xlsx:0:3', sheet: '门诊', row: 3, block_index: 2 },
  ] }), '门诊 · 第2–3行')
  assert.equal(anchorLabel({ positions: [{ kind: 'json_record', block_id: 'json:0', json_pointer: '/0', block_index: 0 }] }), 'JSON /0')
  assert.equal(anchorLabel({ positions: [{ kind: 'text_range', block_id: 'txt:0', character_start: 10, character_end: 20, block_index: 0 }] }), '字符 10–20')
})

test('source preview scrolls only its own container and centers an exact highlight', () => {
  const calls = []
  const container = {
    scrollTop: 400, scrollHeight: 2000, clientHeight: 600,
    getBoundingClientRect: () => ({ top: 100, bottom: 700 }),
    scrollTo: options => calls.push(options),
  }
  const target = {
    getBoundingClientRect: () => ({ top: 900, bottom: 940, height: 40 }),
    scrollIntoView: () => assert.fail('target scrollIntoView must not be used'),
  }
  assert.equal(scrollTargetWithin(container, target), true)
  assert.deepEqual(calls, [{ top: 920, behavior: 'smooth' }])
})

test('page-level source positioning honors sticky offset and clamps both boundaries', () => {
  const calls = []
  const container = {
    scrollTop: 0, scrollHeight: 1800, clientHeight: 600,
    getBoundingClientRect: () => ({ top: 100, bottom: 700 }),
    scrollTo: options => calls.push(options),
  }
  scrollTargetWithin(container, { getBoundingClientRect: () => ({ top: 80, height: 800 }) }, { align: 'start', offset: 80 })
  scrollTargetWithin(container, { getBoundingClientRect: () => ({ top: 2200, height: 800 }) }, { align: 'start', offset: 80 })
  assert.deepEqual(calls.map(item => item.top), [0, 1200])
})

test('the latest source location request replaces the prior container target', () => {
  const calls = []
  const container = {
    scrollTop: 0, scrollHeight: 3000, clientHeight: 600,
    getBoundingClientRect: () => ({ top: 0, bottom: 600 }),
    scrollTo: options => calls.push(options),
  }
  scrollTargetWithin(container, { getBoundingClientRect: () => ({ top: 300, height: 40 }) })
  scrollTargetWithin(container, { getBoundingClientRect: () => ({ top: 1500, height: 40 }) })
  assert.equal(calls.at(-1).top, 1220)
})

test('visible PDF page follows the page occupying the viewport center', () => {
  const container = { clientHeight: 700, getBoundingClientRect: () => ({ top: 100, bottom: 800 }) }
  const pageElements = new Map([
    [1, { getBoundingClientRect: () => ({ top: -700, bottom: 50 }) }],
    [2, { getBoundingClientRect: () => ({ top: 70, bottom: 760 }) }],
    [3, { getBoundingClientRect: () => ({ top: 780, bottom: 1480 }) }],
  ])
  assert.equal(visiblePageNumber(container, pageElements, 1, 50), 2)
})
