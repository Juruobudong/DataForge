const DEFAULT_INPUT = { input: { artifact_type: '', cardinality: 'one' } }
const DEFAULT_OUTPUT = { output: { artifact_type: '', cardinality: 'many' } }
const cloneValue = value => JSON.parse(JSON.stringify(value))

export function artifactMatches(actual, expected) {
  if (!actual || !expected) return false
  return actual === expected || (expected.endsWith(':*') && actual.startsWith(expected.slice(0, -1)))
}

export function resolveCandidateType(type, definition = {}) {
  if (type !== 'candidate:*') return type || ''
  const family = definition.params?.knowledge_type
  if (!family) return type
  const mode = definition.params?.graph_mode
  return `candidate:${family}${family === 'graph' && mode ? `:${mode}` : ''}`
}

function catalogItem(catalog, code) {
  return catalog.find(item => item.code === code)
}

function normalizedPorts(raw, fallback) {
  return Object.fromEntries(Object.entries(raw || fallback).map(([name, value]) => [name, {
    artifact_type: typeof value === 'string' ? value : value?.artifact_type || '',
    cardinality: typeof value === 'string' ? (fallback === DEFAULT_INPUT ? 'one' : 'many') : value?.cardinality || (fallback === DEFAULT_INPUT ? 'one' : 'many'),
  }]))
}

export function resolveNodeMetadata(definition, catalog = [], subflows = []) {
  const status = definition.status || 'idle'
  if (definition.kind === 'knowledge_sink') {
    const outputKey = definition.output_key || (definition.knowledge_type === 'graph' && definition.graph_mode
      ? `graph:${definition.graph_mode}` : definition.knowledge_type)
    return {
      kind: 'knowledge_sink', name: 'Knowledge Sink', code: outputKey, category: '正式知识输出', status,
      known: true, inputs: { input: { artifact_type: `candidate:${outputKey}`, cardinality: 'one' } }, outputs: {}, parameterSchema: {},
      inputExample: { input: [{ canonical_content: '示例正式知识', source_chunk_id: 'chunk-example-001' }] }, outputExample: {},
    }
  }
  if (definition.kind === 'subflow') {
    const subflow = subflows.find(item => item.code === definition.ref)
    const child = subflow?.definition || {}
    const entry = child.nodes?.find(node => node.id === child.entry_node)
    const exit = child.nodes?.find(node => node.id === child.exit_node)
    const entryItem = entry?.kind === 'operator' ? catalogItem(catalog, entry.ref) : null
    const exitItem = exit?.kind === 'operator' ? catalogItem(catalog, exit.ref) : null
    return {
      kind: 'subflow', name: subflow?.name || definition.ref, code: definition.ref, category: '可复用子图', status,
      known: Boolean(subflow),
      revision: subflow?.revision, internalCount: child.nodes?.length || 0,
      inputs: normalizedPorts(entryItem?.input_ports, DEFAULT_INPUT),
      outputs: normalizedPorts(exitItem?.output_ports, DEFAULT_OUTPUT), parameterSchema: {},
      inputExample: entryItem?.input_example || {}, outputExample: exitItem?.output_example || {},
    }
  }
  const item = catalogItem(catalog, definition.ref)
  return {
    kind: 'operator', name: item?.name || definition.ref, code: definition.ref, category: item?.category || '未知算子', status,
    known: Boolean(item),
    inputs: normalizedPorts(item?.input_ports, DEFAULT_INPUT), outputs: normalizedPorts(item?.output_ports, DEFAULT_OUTPUT),
    parameterSchema: item?.parameter_schema || {}, inputExample: item?.input_example || {}, outputExample: item?.output_example || {}, version: item?.version,
  }
}

export function hasEditableParameters(node) {
  return node?.data?.meta?.kind === 'operator' && node?.data?.meta?.code !== 'document-parser'
}

export function makeCanvasNode(definition, position, catalog = [], subflows = []) {
  const meta = resolveNodeMetadata(definition, catalog, subflows)
  return {
    id: definition.id,
    type: meta.kind === 'knowledge_sink' ? 'knowledge-sink' : meta.kind,
    position: { x: Number(position?.x) || 0, y: Number(position?.y) || 0 },
    data: { definition: cloneValue(definition), meta },
  }
}

export function deserializeDefinition(value = {}, catalog = [], subflows = []) {
  const positions = value.ui?.positions || {}
  const missingPositions = []
  const nodes = (value.nodes || []).map((definition, index) => {
    if (!positions[definition.id]) missingPositions.push(definition.id)
    const position = positions[definition.id] || { x: 80 + (index % 4) * 300, y: 60 + Math.floor(index / 4) * 170 }
    return makeCanvasNode(definition, position, catalog, subflows)
  })
  const edges = (value.edges || []).map((edge, index) => {
    const source = Array.isArray(edge) ? edge[0] : edge.source
    const target = Array.isArray(edge) ? edge[1] : edge.target
    return {
      id: `edge-${index}-${source}-${target}`,
      type: 'dataforge', source, target,
      sourceHandle: Array.isArray(edge) ? 'output' : edge.source_port || 'output',
      targetHandle: Array.isArray(edge) ? 'input' : edge.target_port || 'input',
      data: { status: 'idle' },
    }
  })
  return { nodes, edges, missingPositions }
}

export function deserializeRuntimeDag(value = {}, catalog = []) {
  const graph = deserializeDefinition({ nodes: value.nodes || [], edges: value.edges || [], ui: value.ui || {} }, catalog)
  const runtimeById = Object.fromEntries((value.nodes || []).map(node => [node.id, node]))
  graph.nodes = graph.nodes.map(node => ({
    ...node,
    data: { ...node.data, definition: { ...node.data.definition, status: runtimeById[node.id]?.status || 'idle' }, meta: { ...node.data.meta, status: runtimeById[node.id]?.status || 'idle', runtime: runtimeById[node.id] } },
  }))
  graph.edges = graph.edges.map((edge, index) => {
    const raw = (value.edges || [])[index] || {}
    const type = raw.artifact_type || raw.type_code || ''
    return { ...edge, data: { status: raw.status || 'idle', artifactIds: raw.artifact_ids || [], label: type ? `${type}${raw.record_count ? ` · ${raw.record_count}` : ''}` : '' } }
  })
  return graph
}

export function serializeDefinition(nodes, edges) {
  return {
    schema_version: 3,
    nodes: nodes.map(node => ({ ...cloneValue(node.data.definition), id: node.id })),
    edges: edges.map(edge => ({
      source: edge.source, source_port: edge.sourceHandle || 'output',
      target: edge.target, target_port: edge.targetHandle || 'input',
    })),
    ui: { positions: Object.fromEntries(nodes.map(node => [node.id, { x: node.position.x, y: node.position.y }])) },
  }
}

export function connectionIssue(connection, nodes, edges) {
  const source = nodes.find(node => node.id === connection.source)
  const target = nodes.find(node => node.id === connection.target)
  const sourcePort = source?.data.meta.outputs?.[connection.sourceHandle || 'output']
  const targetPort = target?.data.meta.inputs?.[connection.targetHandle || 'input']
  if (!source || !target) return { code: 'UNKNOWN_NODE', message: '连线引用了不存在的节点' }
  if (source.id === target.id) return { code: 'SELF_CONNECTION', message: '节点不能连接自身', nodeId: source.id }
  if (source.data.meta.kind === 'knowledge_sink') return { code: 'SINK_SOURCE', message: 'Knowledge Sink 必须是终点', nodeId: source.id }
  if (!sourcePort) return { code: 'UNKNOWN_SOURCE_PORT', message: `输出端口不存在：${connection.sourceHandle || 'output'}`, nodeId: source.id }
  if (!targetPort) return { code: 'UNKNOWN_TARGET_PORT', message: `输入端口不存在：${connection.targetHandle || 'input'}`, nodeId: target.id }
  const actual = resolveCandidateType(sourcePort.artifact_type, source.data.definition)
  const expected = resolveCandidateType(targetPort.artifact_type, target.data.definition)
  if (expected === 'source_file') return { code: 'ROOT_INPUT', message: 'SourceFile 输入节点只能作为流程根节点', nodeId: target.id }
  const graphSinkFallback = target.data.meta.kind === 'knowledge_sink' && expected.startsWith('candidate:graph:') && actual === 'candidate:graph'
  if (!artifactMatches(actual, expected) && !graphSinkFallback) {
    return { code: 'TYPE_MISMATCH', message: `端口类型不兼容：${actual} → ${expected}`, nodeId: target.id }
  }
  if (edges.some(edge => edge.source === source.id && edge.target === target.id &&
    (edge.sourceHandle || 'output') === (connection.sourceHandle || 'output') &&
    (edge.targetHandle || 'input') === (connection.targetHandle || 'input'))) {
    return { code: 'DUPLICATE_EDGE', message: '相同端口之间已经存在连线', nodeId: target.id }
  }
  if (targetPort.cardinality !== 'many' && edges.some(edge => edge.target === target.id && (edge.targetHandle || 'input') === (connection.targetHandle || 'input'))) {
    return { code: 'PORT_CARDINALITY', message: `输入端口 ${connection.targetHandle || 'input'} 只允许一条连线`, nodeId: target.id }
  }
  return null
}

export function createsCycle(nodes, edges, candidate = null) {
  const links = candidate ? [...edges, candidate] : edges
  const outgoing = new Map(nodes.map(node => [node.id, []]))
  const indegree = new Map(nodes.map(node => [node.id, 0]))
  for (const edge of links) {
    if (!outgoing.has(edge.source) || !indegree.has(edge.target)) continue
    outgoing.get(edge.source).push(edge.target)
    indegree.set(edge.target, indegree.get(edge.target) + 1)
  }
  const queue = [...indegree].filter(([, count]) => count === 0).map(([id]) => id)
  let visited = 0
  while (queue.length) {
    const id = queue.shift(); visited += 1
    for (const target of outgoing.get(id) || []) {
      indegree.set(target, indegree.get(target) - 1)
      if (indegree.get(target) === 0) queue.push(target)
    }
  }
  return visited !== nodes.length
}

export function validateFlow(nodes, edges, outputTypes = []) {
  const issues = []
  const nodeIds = new Set(nodes.map(node => node.id))
  for (const edge of edges) {
    const issue = connectionIssue(edge, nodes, edges.filter(item => item.id !== edge.id))
    if (issue) issues.push({ ...issue, edgeId: edge.id })
  }
  for (const node of nodes) {
    const incoming = edges.filter(edge => edge.target === node.id)
    const outgoing = edges.filter(edge => edge.source === node.id)
    if (!node.data.meta.known) issues.push({ code: node.data.meta.kind === 'subflow' ? 'UNKNOWN_SUBFLOW' : 'UNKNOWN_OPERATOR', message: `${node.data.meta.kind === 'subflow' ? '子图' : '算子'}不存在或未发布：${node.data.meta.code}`, nodeId: node.id })
    if (nodes.length > 1 && !incoming.length && !outgoing.length) issues.push({ code: 'ISOLATED_NODE', message: '节点未接入流程', nodeId: node.id })
    for (const [port, spec] of Object.entries(node.data.meta.inputs || {})) {
      if (spec.artifact_type === 'source_file') continue
      if (!incoming.some(edge => (edge.targetHandle || 'input') === port)) issues.push({ code: 'REQUIRED_INPUT', message: `必需输入端口未连接：${port}`, nodeId: node.id })
    }
  }
  if (createsCycle(nodes, edges)) issues.push({ code: 'CYCLE', message: '流程必须是有向无环图' })
  const sinks = nodes.filter(node => node.data.meta.kind === 'knowledge_sink')
  if (!sinks.length) issues.push({ code: 'MISSING_SINK', message: '流程至少需要一个 Knowledge Sink' })
  for (const output of outputTypes) {
    if (!sinks.some(node => node.data.definition.output_key === output)) issues.push({ code: 'MISSING_OUTPUT_SINK', message: `模板输出缺少对应 Sink：${output}` })
  }
  for (const sink of sinks) {
    const key = sink.data.definition.output_key
    if (!outputTypes.includes(key)) issues.push({ code: 'UNDECLARED_SINK', message: `Sink 未在模板输出中声明：${key}`, nodeId: sink.id })
    if (edges.some(edge => edge.source === sink.id)) issues.push({ code: 'SINK_NOT_TERMINAL', message: 'Knowledge Sink 必须是终点', nodeId: sink.id })
  }
  return issues.filter((issue, index, all) => index === all.findIndex(other => other.code === issue.code && other.nodeId === issue.nodeId && other.edgeId === issue.edgeId && other.message === issue.message))
}

export function removeElements(nodes, edges, nodeIds = [], edgeIds = []) {
  const nodeSet = new Set(nodeIds), edgeSet = new Set(edgeIds)
  return {
    nodes: nodes.filter(node => !nodeSet.has(node.id)),
    edges: edges.filter(edge => !edgeSet.has(edge.id) && !nodeSet.has(edge.source) && !nodeSet.has(edge.target)),
  }
}

export function cloneGraph(nodes, edges) {
  return cloneValue({ nodes, edges })
}

// Capability-first 算子呈现：常用能力快捷入口 + 按业务能力分组的算子实现。
export const COMMON_OPERATOR_CAPABILITIES = [
  { code: 'qa-generator', label: '问答生成' },
  { code: 'prompt-generator', label: '文本生成' },
  { code: 'graph-extractor', label: '实体关系抽取' },
  { code: 'quality-evaluator', label: '质量检查' },
]

export function groupOperatorCapabilities(catalog = [], common = COMMON_OPERATOR_CAPABILITIES, query = '') {
  const commonCodes = new Set(common.map(item => item.code))
  const match = item => !query || `${item.name} ${item.code} ${item.category} ${item.display_name_zh || ''} ${item.description || ''}`.toLowerCase().includes(query.toLowerCase())
  const visible = catalog.filter(item => item.exposure === 'canvas' && item.enabled !== false && match(item))
  const byCode = Object.fromEntries(visible.map(item => [item.code, item]))
  const commonItems = common.map(entry => byCode[entry.code]).filter(Boolean).map(item => ({ ...item, _label: common.find(entry => entry.code === item.code)?.label }))
  const rest = visible.filter(item => !commonCodes.has(item.code))
  const groups = Object.entries(rest.reduce((result, item) => {
    const category = item.category || '其他'
    ;(result[category] ||= []).push(item)
    return result
  }, {}))
  return { common: commonItems, groups }
}
