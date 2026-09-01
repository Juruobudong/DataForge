import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const view = readFileSync(new URL('./PipelineListView.vue', import.meta.url), 'utf8')

test('preprocessing workspace separates parse, Flow chunking and knowledge generation', () => {
  assert.match(view, /ParseJob → 不可变 ParsedDocument/)
  assert.match(view, /document-input → document-chunker → execution_gate/)
  assert.match(view, /FlowChunkReviewSnapshot/)
  assert.match(view, /TABULAR_CHUNKING_UNSUPPORTED/)
  assert.doesNotMatch(view, /sourcePreparationChunker|previewSourcePreparation/)
})
