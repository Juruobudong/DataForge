import test from 'node:test'
import assert from 'node:assert/strict'
import { artifactMatches, connectionIssue, createsCycle, deserializeDefinition, makeCanvasNode, removeElements, resolveNodeMetadata, serializeDefinition, validateFlow } from './flowModel.js'
import { ref } from 'vue'
import { useFlowHistory } from './composables/useFlowHistory.js'

const catalog = [
  { code: 'source', name: 'Source', category: '文档', input_ports: { input: { artifact_type: 'source_file', cardinality: 'one' } }, output_ports: { output: { artifact_type: 'chunk_set', cardinality: 'many' } }, input_example: { input: [{ filename: 'guide.md' }] }, output_example: { output: [{ content: 'example' }] } },
  { code: 'split', name: 'Split', category: '生成', input_ports: { input: { artifact_type: 'chunk_set', cardinality: 'many' } }, output_ports: { qa: { artifact_type: 'candidate:qa' }, text: { artifact_type: 'candidate:text' } } },
]
const node = (id, ref, x = 0) => makeCanvasNode({ id, kind: 'operator', ref, params: {} }, { x, y: 0 }, catalog, [])
const sink = makeCanvasNode({ id: 'sink', kind: 'knowledge_sink', knowledge_type: 'qa', output_key: 'qa' }, { x: 400, y: 0 }, catalog, [])

test('artifact type supports exact and target wildcard matching', () => {
  assert.equal(artifactMatches('candidate:qa', 'candidate:qa'), true)
  assert.equal(artifactMatches('candidate:qa', 'candidate:*'), true)
  assert.equal(artifactMatches('candidate:text', 'candidate:qa'), false)
})

test('multi-port catalog metadata is preserved and subflow ports resolve from entry and exit', () => {
  const sourceMeta = resolveNodeMetadata({ kind: 'operator', ref: 'source' }, catalog)
  assert.deepEqual(sourceMeta.inputExample.input, [{ filename: 'guide.md' }])
  assert.deepEqual(Object.keys(resolveNodeMetadata({ kind: 'operator', ref: 'split' }, catalog).outputs), ['qa', 'text'])
  const subflow = { code: 'pipeline', name: 'Pipeline', revision: 2, definition: { entry_node: 'a', exit_node: 'b', nodes: [{ id: 'a', kind: 'operator', ref: 'source' }, { id: 'b', kind: 'operator', ref: 'split' }] } }
  const meta = resolveNodeMetadata({ kind: 'subflow', ref: 'pipeline' }, catalog, [subflow])
  assert.equal(meta.inputs.input.artifact_type, 'source_file')
  assert.equal(meta.outputs.qa.artifact_type, 'candidate:qa')
  assert.deepEqual(meta.inputExample.input, [{ filename: 'guide.md' }])
})

test('connection validation covers legal, illegal, duplicate and cardinality cases', () => {
  const nodes = [node('source', 'source'), node('split', 'split'), sink]
  const first = { source: 'source', sourceHandle: 'output', target: 'split', targetHandle: 'input' }
  assert.equal(connectionIssue(first, nodes, []), null)
  const qa = { source: 'split', sourceHandle: 'qa', target: 'sink', targetHandle: 'input' }
  assert.equal(connectionIssue(qa, nodes, []), null)
  assert.equal(connectionIssue({ ...qa, sourceHandle: 'text' }, nodes, []).code, 'TYPE_MISMATCH')
  assert.equal(connectionIssue(first, nodes, [{ id: 'e', ...first }]).code, 'DUPLICATE_EDGE')
  const alternate = node('source-2', 'source')
  const oneInput = makeCanvasNode({ id: 'one', kind: 'operator', ref: 'source' }, { x: 0, y: 0 }, [{ code: 'source', input_ports: { input: { artifact_type: 'chunk_set', cardinality: 'one' } }, output_ports: { output: { artifact_type: 'chunk_set' } } }])
  assert.equal(connectionIssue({ source: 'source-2', sourceHandle: 'output', target: 'one', targetHandle: 'input' }, [nodes[0], alternate, oneInput], [{ id: 'used', source: 'source', sourceHandle: 'output', target: 'one', targetHandle: 'input' }]).code, 'PORT_CARDINALITY')
})

test('source file input remains a root-only port', () => {
  const sourceFileProducer = makeCanvasNode({ id: 'file', kind: 'operator', ref: 'file' }, { x: 0, y: 0 }, [{ code: 'file', input_ports: {}, output_ports: { output: { artifact_type: 'source_file' } } }])
  const parser = node('parser', 'source')
  assert.equal(connectionIssue({ source: 'file', sourceHandle: 'output', target: 'parser', targetHandle: 'input' }, [sourceFileProducer, parser], []).code, 'ROOT_INPUT')
})

test('graph sink accepts generic candidate graph output', () => {
  const graphCatalog = [{ code: 'graph', input_ports: { input: { artifact_type: 'chunk_set' } }, output_ports: { output: { artifact_type: 'candidate:graph' } } }]
  const source = makeCanvasNode({ id: 'g', kind: 'operator', ref: 'graph' }, { x: 0, y: 0 }, graphCatalog)
  const target = makeCanvasNode({ id: 's', kind: 'knowledge_sink', knowledge_type: 'graph', graph_mode: 'semantic', output_key: 'graph:semantic' }, { x: 1, y: 0 }, graphCatalog)
  assert.equal(connectionIssue({ source: 'g', sourceHandle: 'output', target: 's', targetHandle: 'input' }, [source, target], []), null)
})

test('DSL round trip preserves explicit ports and positions and loads legacy edges', () => {
  const graph = deserializeDefinition({ nodes: [{ id: 'a', kind: 'operator', ref: 'source' }, { id: 'b', kind: 'operator', ref: 'split' }], edges: [['a', 'b']], ui: { positions: { a: { x: 12, y: 34 } } } }, catalog)
  assert.equal(graph.edges[0].sourceHandle, 'output')
  assert.deepEqual(graph.nodes[0].position, { x: 12, y: 34 })
  assert.deepEqual(graph.missingPositions, ['b'])
  const serialized = serializeDefinition(graph.nodes, graph.edges)
  assert.equal(serialized.edges[0].target_port, 'input')
  assert.deepEqual(serialized.ui.positions.a, { x: 12, y: 34 })
})

test('cycle detection, validation locations, and cascading removal work', () => {
  const nodes = [node('a', 'source'), node('b', 'split'), sink]
  const edges = [{ id: 'ab', source: 'a', sourceHandle: 'output', target: 'b', targetHandle: 'input' }, { id: 'bs', source: 'b', sourceHandle: 'qa', target: 'sink', targetHandle: 'input' }]
  assert.equal(createsCycle(nodes, edges, { source: 'b', target: 'a' }), true)
  assert.deepEqual(validateFlow(nodes, edges, ['qa']), [])
  const invalid = validateFlow(nodes, edges.slice(0, 1), ['qa'])
  assert.ok(invalid.some(issue => issue.code === 'REQUIRED_INPUT' && issue.nodeId === 'sink'))
  const removed = removeElements(nodes, edges, ['b'])
  assert.equal(removed.nodes.length, 2)
  assert.equal(removed.edges.length, 0)
})

test('history records graph transactions and supports undo and redo', () => {
  const nodes = ref([node('a', 'source')]), edges = ref([])
  const history = useFlowHistory(nodes, edges)
  history.remember(); nodes.value.push(node('b', 'split'))
  history.undo(); assert.equal(nodes.value.length, 1); assert.equal(history.canRedo.value, true)
  history.redo(); assert.equal(nodes.value.length, 2); assert.equal(history.canUndo.value, true)
})
