import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const view = readFileSync(new URL('./PipelineListView.vue', import.meta.url), 'utf8')

test('source preparation chunker saves immutable revisions from a dedicated drawer', () => {
  assert.match(view, /sourcePreparationChunker/)
  assert.match(view, /createSourcePreparationChunkerRevision/)
  assert.match(view, /base_revision: chunker\.value\.revision/)
  assert.match(view, /保存为新 Revision/)
  assert.doesNotMatch(view, /NodeInspector/)
})
