import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { checkEdgeCompatibility, nearestPort, portKey, SNAP_RADIUS } from './edge/edgeCompatibility.js'
import { beginEdgeInteraction, idleEdgeInteraction } from './edge/edgeInteraction.js'

const node = (id, input, output, params = {}, kind = 'operator') => ({
  id, data: { definition: { id, kind, params }, meta: {
    kind, nodeRole: kind === 'knowledge_sink' ? 'knowledge_output' : 'operator', name: id,
    inputs: input ? { input: { artifact_type: input, cardinality: 'one', binding: 'edge' } } : {},
    outputs: output ? { output: { artifact_type: output, cardinality: 'many', binding: 'edge' } } : {},
  } },
})
const sink = (id, outputKey) => {
  const [knowledgeType, graphMode] = outputKey.split(':')
  const value = node(id, `candidate:${outputKey}`, null, {}, 'knowledge_sink')
  value.data.definition = { id, kind: 'knowledge_sink', knowledge_type: knowledgeType, graph_mode: graphMode, output_key: outputKey }
  return value
}

test('candidate wildcard resolves from simulated text and graph sink context', () => {
  const textNodes = [node('source', 'source_chunk_set', 'candidate:*'), sink('sink', 'text')]
  const text = checkEdgeCompatibility({ flowContext: { schemaVersion: 3, outputTypes: ['text'] }, nodes: textNodes, edges: [], sourceNodeId: 'source', targetNodeId: 'sink' })
  assert.equal(text.allowed, true)
  assert.equal(text.resolvedSourceType, 'candidate:text')

  const graphNodes = [node('source', 'source_chunk_set', 'candidate:*', { knowledge_type: 'graph', graph_mode: 'semantic' }), sink('sink', 'graph:semantic')]
  const graph = checkEdgeCompatibility({ flowContext: { schemaVersion: 3, outputTypes: ['graph:semantic'] }, nodes: graphNodes, edges: [], sourceNodeId: 'source', targetNodeId: 'sink' })
  assert.equal(graph.allowed, true)
  assert.equal(graph.resolvedSourceType, 'candidate:graph:semantic')
})

test('compatibility distinguishes type, knowledge type, graph mode, multiplicity and cycle', () => {
  const typeNodes = [node('source', null, 'candidate:text'), node('target', 'source_chunk_set', 'candidate:text')]
  assert.equal(checkEdgeCompatibility({ nodes: typeNodes, edges: [], sourceNodeId: 'source', targetNodeId: 'target' }).reasonCode, 'PORT_TYPE_MISMATCH')
  const knowledgeNodes = [node('source', null, 'candidate:text'), sink('sink', 'qa')]
  assert.equal(checkEdgeCompatibility({ nodes: knowledgeNodes, edges: [], sourceNodeId: 'source', targetNodeId: 'sink' }).reasonCode, 'KNOWLEDGE_TYPE_MISMATCH')
  const graphNodes = [node('source', null, 'candidate:graph:semantic'), sink('sink', 'graph:triple')]
  assert.equal(checkEdgeCompatibility({ nodes: graphNodes, edges: [], sourceNodeId: 'source', targetNodeId: 'sink' }).reasonCode, 'GRAPH_MODE_MISMATCH')

  const singleNodes = [node('a', null, 'candidate:text'), node('b', null, 'candidate:text'), node('target', 'candidate:text', 'candidate:text')]
  const existing = [{ id: 'old', source: 'a', sourceHandle: 'output', target: 'target', targetHandle: 'input' }]
  assert.equal(checkEdgeCompatibility({ nodes: singleNodes, edges: existing, sourceNodeId: 'b', targetNodeId: 'target' }).reasonCode, 'INPUT_PORT_ALREADY_CONNECTED')
  assert.equal(checkEdgeCompatibility({ nodes: singleNodes, edges: existing, sourceNodeId: 'b', targetNodeId: 'target', originalEdgeId: 'old' }).allowed, true)

  const cycleNodes = [node('a', 'candidate:text', 'candidate:text'), node('b', 'candidate:text', 'candidate:text'), node('c', 'candidate:text', 'candidate:text')]
  const cycleEdges = [{ id: 'ab', source: 'a', target: 'b' }, { id: 'bc', source: 'b', target: 'c' }]
  assert.equal(checkEdgeCompatibility({ nodes: cycleNodes, edges: cycleEdges, sourceNodeId: 'c', targetNodeId: 'a' }).reasonCode, 'EDGE_WOULD_CREATE_CYCLE')
})

test('multi-output unresolved wildcard fails closed and compatibility map scans once', () => {
  const source = node('source', null, 'candidate:*')
  const ambiguousTarget = node('target', 'candidate:*', 'candidate:*')
  const ambiguous = checkEdgeCompatibility({ flowContext: { schemaVersion: 3, outputTypes: ['text', 'qa'] }, nodes: [source, ambiguousTarget], edges: [], sourceNodeId: 'source', targetNodeId: 'target' })
  assert.equal(ambiguous.reasonCode, 'OPERATOR_CONTRACT_MISMATCH')
  const state = beginEdgeInteraction({ mode: 'connecting', flowContext: { schemaVersion: 3, outputTypes: ['text'] }, nodes: [node('known', null, 'candidate:text'), sink('text', 'text')], edges: [], sourceNodeId: 'known', sourcePortId: 'output' })
  assert.equal(state.compatiblePorts.get(portKey('text', 'input', 'input')).allowed, true)
  assert.equal(idleEdgeInteraction().mode, 'idle')
})

test('nearest port uses the 28px radius and incompatible ports never become snap targets', () => {
  const compatibility = new Map([['valid', { allowed: true }], ['invalid', { allowed: false, reasonCode: 'PORT_TYPE_MISMATCH' }]])
  assert.equal(SNAP_RADIUS, 28)
  assert.equal(nearestPort({ x: 0, y: 0 }, [{ key: 'valid', x: 20, y: 0 }], compatibility)?.key, 'valid')
  assert.equal(nearestPort({ x: 0, y: 0 }, [{ key: 'valid', x: 29, y: 0 }], compatibility), null)
  assert.equal(nearestPort({ x: 0, y: 0 }, [{ key: 'invalid', x: 10, y: 0 }], compatibility)?.compatibility.allowed, false)
})

test('shared canvas wires native snapping, atomic reconnect, context delete and edge inspector', () => {
  const canvas = fs.readFileSync(new URL('./DataForgeFlowCanvas.vue', import.meta.url), 'utf8')
  const editor = fs.readFileSync(new URL('./advanced/AdvancedFlowEditor.vue', import.meta.url), 'utf8')
  assert.match(canvas, /:connection-radius="SNAP_RADIUS"/)
  assert.match(canvas, /@edge-update="edgeUpdate"/)
  assert.match(canvas, /@edge-context-menu="openEdgeMenu"/)
  assert.match(canvas, /Delete', 'Backspace/)
  assert.match(editor, /EdgeInspector/)
  assert.doesNotMatch(canvas, /edgeUpdate[\s\S]{0,400}autoLayout\(/)
})
