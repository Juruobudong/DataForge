import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { artifactMatches, connectionIssue, createsCycle, deserializeDefinition, deserializeRuntimeDag, groupOperatorCapabilities, hasEditableParameters, makeCanvasNode, operatorNodeSubtitle, removeElements, resolveNodeMetadata, runtimeArtifactLabel, serializeDefinition, subflowEnglishName, subflowPrimaryName, subflowSubtitle, validateFlow } from './flowModel.js'
import { ref } from 'vue'
import { useFlowHistory } from './composables/useFlowHistory.js'
import { dataflowOperators } from './__tests__/flowFixtures.js'

const catalog = [
  { code: 'source', name: 'Source', category: '文档', input_ports: { input: { artifact_type: 'source_file', cardinality: 'one' } }, output_ports: { output: { artifact_type: 'chunk_set', cardinality: 'many' } }, input_example: { input: [{ filename: 'guide.md' }] }, output_example: { output: [{ content: 'example' }] } },
  { code: 'split', name: 'Split', category: '生成', input_ports: { input: { artifact_type: 'chunk_set', cardinality: 'many' } }, output_ports: { qa: { artifact_type: 'candidate:qa' }, text: { artifact_type: 'candidate:text' } } },
]
const node = (id, ref, x = 0) => makeCanvasNode({ id, kind: 'operator', ref, params: {} }, { x, y: 0 }, catalog, [])
const sink = makeCanvasNode({ id: 'sink', kind: 'knowledge_sink', knowledge_type: 'qa', output_key: 'qa' }, { x: 400, y: 0 }, catalog, [])
const edgeView = readFileSync(new URL('./edges/FlowEdge.vue', import.meta.url), 'utf8')

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

test('runtime artifact labels use Chinese business terms and readable counts', () => {
  assert.equal(runtimeArtifactLabel('candidate:*', 5), '候选知识 · 5 条')
  assert.equal(runtimeArtifactLabel('candidate:text', 3), '文本候选 · 3 条')
  assert.equal(runtimeArtifactLabel('approved_source_chunks', 0), '已审核文档块 · 0 条')
  assert.equal(runtimeArtifactLabel('custom_extension', 2), 'custom_extension · 2 条')
  assert.match(edgeView, /:title="data\?\.technicalLabel \|\| data\?\.label"/)
})

test('operator metadata separates Chinese display name, English name, and technical code', () => {
  const bilingual = resolveNodeMetadata({ kind: 'operator', ref: 'null-filter' }, [{
    code: 'null-filter', name: 'Null Filter', display_name_zh: '空内容过滤器', category: '内容处理',
  }])
  assert.equal(bilingual.name, '空内容过滤器')
  assert.equal(bilingual.englishName, 'Null Filter')
  assert.equal(bilingual.code, 'null-filter')
  assert.equal(operatorNodeSubtitle(bilingual), 'Null Filter')
  assert.equal(operatorNodeSubtitle(bilingual, true), 'Null Filter · null-filter')

  const fallback = resolveNodeMetadata({ kind: 'operator', ref: 'legacy-cleaner' }, [{ code: 'legacy-cleaner', name: 'Legacy Cleaner' }])
  assert.equal(fallback.name, 'Legacy Cleaner')
  assert.equal(fallback.englishName, 'Legacy Cleaner')
  assert.equal(fallback.code, 'legacy-cleaner')
  assert.equal(operatorNodeSubtitle(fallback), 'legacy-cleaner')
  assert.equal(operatorNodeSubtitle(fallback, true), 'legacy-cleaner')
})

test('DataFlow class identity is bilingual and is not duplicated as a technical code', () => {
  for (const operator of dataflowOperators) {
    const definition = { kind: 'operator', ref: operator.code, operator_version: operator.version, operator_spec: operator }
    // Runtime uses the frozen name even if the current catalog is changed.
    const meta = resolveNodeMetadata(definition, [{ ...operator, name: 'wrong latest name' }])
    assert.equal(meta.name, operator.display_name_zh)
    assert.equal(operatorNodeSubtitle(meta, true), `${operator.code}`)
    assert.equal(operatorNodeSubtitle(meta), `${operator.code}`)
  }
})

test('subflow presentation separates built-in Chinese, English, code, and revision', () => {
  const builtin = { code: 'document-clean', name: 'Document Clean', display_name_zh: '文档清洗', revision: 1, definition: { nodes: [] } }
  assert.equal(subflowPrimaryName(builtin), '文档清洗')
  assert.equal(subflowEnglishName(builtin), 'Document Clean')
  assert.equal(subflowSubtitle(builtin), 'Document Clean · r1')
  assert.equal(subflowSubtitle(builtin, true), 'Document Clean · document-clean · r1')
  const meta = resolveNodeMetadata({ kind: 'subflow', ref: 'document-clean' }, [], [builtin])
  assert.equal(meta.name, '文档清洗')
  assert.equal(meta.englishName, 'Document Clean')

  const custom = { code: 'custom-subflow', name: '用户自定义子图', revision: 2 }
  assert.equal(subflowPrimaryName(custom), '用户自定义子图')
  assert.equal(subflowEnglishName(custom), '')
  assert.equal(subflowSubtitle(custom), 'r2')
  assert.equal(subflowSubtitle(custom, true), 'custom-subflow · r2')
})

test('only operator parameter schema properties are editable', () => {
  const parserCatalog = [{ code: 'document-parser', name: 'Document Parser', category: '文档', input_ports: { input: { artifact_type: 'source_file' } }, output_ports: { output: { artifact_type: 'document_ir' } } }]
  const parser = makeCanvasNode({ id: 'parser', kind: 'operator', ref: 'document-parser', params: {} }, { x: 0, y: 0 }, parserCatalog)
  assert.equal(hasEditableParameters(parser), false)
  const configurableCatalog = [{ code: 'prompt-generator', name: 'Prompt', category: 'LLM', parameter_schema: { type: 'object', properties: { llm_serving: { type: 'string' } } } }]
  const configurable = makeCanvasNode({ id: 'prompt', kind: 'operator', ref: 'prompt-generator', params: {} }, { x: 0, y: 0 }, configurableCatalog)
  assert.equal(hasEditableParameters(configurable), true)
  assert.equal(hasEditableParameters(node('source', 'source')), false)
  assert.equal(hasEditableParameters(sink), false)
})

test('connection validation covers legal, illegal, duplicate and cardinality cases', () => {
  const nodes = [node('source', 'source'), node('split', 'split'), sink]
  const first = { source: 'source', sourceHandle: 'output', target: 'split', targetHandle: 'input' }
  assert.equal(connectionIssue(first, nodes, []), null)
  const qa = { source: 'split', sourceHandle: 'qa', target: 'sink', targetHandle: 'input' }
  assert.equal(connectionIssue(qa, nodes, []), null)
  assert.equal(connectionIssue({ ...qa, sourceHandle: 'text' }, nodes, []).code, 'KNOWLEDGE_TYPE_MISMATCH')
  assert.equal(connectionIssue(first, nodes, [{ id: 'e', ...first }]).code, 'EDGE_DUPLICATED')
  const alternate = node('source-2', 'source')
  const oneInput = makeCanvasNode({ id: 'one', kind: 'operator', ref: 'source' }, { x: 0, y: 0 }, [{ code: 'source', input_ports: { input: { artifact_type: 'chunk_set', cardinality: 'one' } }, output_ports: { output: { artifact_type: 'chunk_set' } } }])
  assert.equal(connectionIssue({ source: 'source-2', sourceHandle: 'output', target: 'one', targetHandle: 'input' }, [nodes[0], alternate, oneInput], [{ id: 'used', source: 'source', sourceHandle: 'output', target: 'one', targetHandle: 'input' }]).code, 'INPUT_PORT_ALREADY_CONNECTED')
})

test('source file input remains a root-only port', () => {
  const sourceFileProducer = makeCanvasNode({ id: 'file', kind: 'operator', ref: 'file' }, { x: 0, y: 0 }, [{ code: 'file', input_ports: {}, output_ports: { output: { artifact_type: 'source_file' } } }])
  const parser = node('parser', 'source')
  assert.equal(connectionIssue({ source: 'file', sourceHandle: 'output', target: 'parser', targetHandle: 'input' }, [sourceFileProducer, parser], []).code, 'INPUT_NODE_CANNOT_HAVE_INCOMING')
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

test('runtime DAG reuses canvas nodes and overlays node and artifact edge state', () => {
  const graph = deserializeRuntimeDag({
    nodes: [{ id: 'a', kind: 'operator', ref: 'source', status: 'reused' }, { id: 'b', kind: 'operator', ref: 'split', status: 'failed' }],
    edges: [{ source: 'a', target: 'b', artifact_type: 'chunk_set', record_count: 5, status: 'completed' }],
  }, catalog)
  assert.equal(graph.nodes[0].data.meta.status, 'reused')
  assert.equal(graph.nodes[1].data.meta.status, 'failed')
  assert.equal(graph.edges[0].data.label, '文档块 · 5 条')
  assert.equal(graph.edges[0].data.technicalLabel, 'chunk_set')
  assert.equal(graph.edges[0].data.status, 'completed')
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

test('capability-first grouping surfaces common capabilities and folds the rest by category', () => {
  const catalog = [
    { code: 'Text2QAGenerator', name: 'QA Generator', display_name_zh: '问答生成器', category: '知识生成', exposure: 'canvas', enabled: true, version: 3 },
    { code: 'prompt-generator', name: 'Prompt Generator', display_name_zh: '提示词生成器', category: '知识生成', exposure: 'canvas', enabled: true, version: 4 },
    { code: 'graph-extractor', name: 'Graph Extractor', display_name_zh: '图谱抽取器', category: '知识生成', exposure: 'canvas', enabled: true, version: 4 },
    { code: 'graph-quality-validator', name: 'Graph Validator', display_name_zh: '图谱质量校验器', category: '质量治理', exposure: 'canvas', enabled: true, version: 3 },
    { code: 'artifact-merge', name: 'Artifact Merge', display_name_zh: '候选合并', category: '质量治理', exposure: 'canvas', enabled: true, version: 3 },
    { code: 'internal-op', name: 'Internal', display_name_zh: '内部算子', category: 'Runtime', exposure: 'internal', enabled: true, version: 3 },
    { code: 'disabled-op', name: 'Disabled', display_name_zh: '禁用算子', category: '知识生成', exposure: 'canvas', enabled: false, version: 3 },
  ]
  const result = groupOperatorCapabilities(catalog)
  assert.deepEqual(result.common.map(item => item._label), ['问答生成', '文本生成', '实体关系抽取', '图谱校验'])
  assert.ok(result.common.every(item => item.version >= 3))
  assert.deepEqual(Object.keys(Object.fromEntries(result.groups)), ['质量治理'])
  assert.deepEqual(result.groups[0][1].map(item => item.code), ['artifact-merge'])
  assert.ok(result.groups[0][1].every(item => !result.common.some(c => c.code === item.code)))
})

test('capability-first grouping filters by query and excludes internal operators', () => {
  const catalog = [
    { code: 'Text2QAGenerator', name: 'QA Generator', display_name_zh: '问答生成器', category: '知识生成', exposure: 'canvas', enabled: true },
    { code: 'source-binding', name: 'Source Binding', display_name_zh: '来源绑定器', category: '质量治理', exposure: 'canvas', enabled: true },
  ]
  assert.deepEqual(groupOperatorCapabilities(catalog, undefined, '来源').groups[0][1].map(item => item.code), ['source-binding'])
  assert.deepEqual(groupOperatorCapabilities(catalog, undefined, '来源').common, [])
})
