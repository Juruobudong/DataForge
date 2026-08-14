import test from 'node:test'
import assert from 'node:assert/strict'
import { graphUiState, layoutGraph } from './graphBrowserModel.js'

test('graph UI state distinguishes loading, empty and ready data', () => {
  assert.equal(graphUiState(null), 'loading')
  assert.equal(graphUiState({ entity_count: 0, nodes: [] }), 'empty')
  assert.equal(graphUiState({ entity_count: 2, nodes: [{ id: 'a' }] }), 'ready')
})

test('graph layout preserves all graph identifiers and edges', () => {
  const result = layoutGraph({
    nodes: [{ id: 'a', name: '甲' }, { id: 'b', name: '乙' }],
    edges: [{ id: 'r', source: 'a', target: 'b', predicate: '关联' }],
  })
  assert.deepEqual(result.nodes.map(item => item.id), ['a', 'b'])
  assert.deepEqual(result.edges.map(item => item.id), ['r'])
  assert.notDeepEqual(result.nodes[0].position, result.nodes[1].position)
})
