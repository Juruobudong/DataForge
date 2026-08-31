import test from 'node:test'
import assert from 'node:assert/strict'
import { upstreamSignals } from './upstreamSignals.js'

test('signal options include only upstream nodes and namespace referenced subflow outputs', () => {
  const nodes = [{ id: 'before', kind: 'operator', ref: 'PromptedEvaluator' },
    { id: 'child', kind: 'subflow', ref: 'quality', subflow_revision_id: 'r1' },
    { id: 'filter', kind: 'operator', ref: 'GeneralFilter' },
    { id: 'after', kind: 'operator', ref: 'Text2QASampleEvaluator' }]
  const child = { code: 'quality', revision_id: 'r1', definition: { exit_node: 'mark', nodes: [
    { id: 'mark', kind: 'operator', ref: 'SemDeduplicateFilter' },
    { id: 'unrelated', kind: 'operator', ref: 'PromptedEvaluator' }], edges: [] } }
  const result = upstreamSignals(nodes, [['before', 'child'], ['child', 'filter'], ['filter', 'after']], 'filter', [], [child])
  assert.deepEqual(result.map(item => [item.id, item.operator]), [['before', 'PromptedEvaluator'], ['child::mark', 'SemDeduplicateFilter']])
  assert.equal(upstreamSignals(nodes, [], 'filter', [], [child]).length, 0)
})
