import { checkEdgeCompatibility, createsCycle as edgeCreatesCycle } from './edge/edgeCompatibility.js'

const DEFAULT_INPUT = { input: { artifact_type: '', cardinality: 'one', required: true, binding: 'edge' } }
const DEFAULT_OUTPUT = { output: { artifact_type: '', cardinality: 'many', required: false, binding: 'edge' } }
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

const ARTIFACT_TYPE_LABELS = {
  approved_source_chunks: '已审核文档块',
  source_file: '源文件',
  document_ir: '文档解析结果',
  chunk_set: '文档块',
  source_chunk_set: '来源文档块',
  'candidate:*': '候选知识',
  'candidate:text': '文本候选',
  'candidate:qa': '问答候选',
  'candidate:qa-agent-faq': 'FAQ 候选',
  'candidate:graph': '图谱候选',
  'candidate:graph:triple': '三元组候选',
  'candidate:graph:semantic': '语义图谱候选',
  entity_candidate_set: '实体候选集',
  relation_candidate_set: '关系候选集',
  semantic_relation_set: '语义关系集',
  execution: '执行结果',
}

export function runtimeArtifactLabel(type, recordCount = null) {
  const raw = String(type || '')
  const label = ARTIFACT_TYPE_LABELS[raw]
    || (raw.startsWith('knowledge_preview:') ? '知识预览' : raw.startsWith('knowledge_item:') ? '知识项' : raw)
  if (!label) return ''
  const hasCount = recordCount !== null && recordCount !== undefined && Number.isFinite(Number(recordCount))
  return `${label}${hasCount ? ` · ${Number(recordCount)} 条` : ''}`
}

function catalogItem(catalog, code, version = null) {
  const item = catalog.find(item => item.code === code)
  if (version == null || item?.version === version) return item
  return item?.versions?.find(entry => entry.version === version)
}

function normalizedPorts(raw, fallback) {
  return Object.fromEntries(Object.entries(raw || fallback).map(([name, value]) => [name, {
    artifact_type: typeof value === 'string' ? value : value?.artifact_type || '',
    cardinality: typeof value === 'string' ? (fallback === DEFAULT_INPUT ? 'one' : 'many') : value?.cardinality || (fallback === DEFAULT_INPUT ? 'one' : 'many'),
    required: typeof value === 'string' ? fallback === DEFAULT_INPUT : value?.required ?? (fallback === DEFAULT_INPUT),
    binding: typeof value === 'string' ? 'edge' : value?.binding || (value?.artifact_type === 'source_file' ? 'system_injected' : value?.artifact_type === 'approved_source_chunks' ? 'runtime_input' : 'edge'),
  }]))
}

export function nodeRole(definition = {}) {
  if (['flow_input', 'operator', 'knowledge_output'].includes(definition.node_role)) return definition.node_role
  if (definition.kind === 'knowledge_sink') return 'knowledge_output'
  if (definition.kind === 'operator' && definition.ref === 'reviewed-source-chunk-input') return 'flow_input'
  return 'operator'
}

export function operatorNodeSubtitle(meta = {}, showTechnicalCode = false) {
  const englishName = meta.englishName || ''
  const base = englishName && englishName !== meta.name ? englishName : meta.code || ''
  return showTechnicalCode && meta.code && meta.code !== base ? `${base} · ${meta.code}` : base
}

export function subflowRevisions(items = []) {
  return items.flatMap(item => item.revisions?.length ? item.revisions : [item])
}

export function resolveSubflow(definition, items = []) {
  if (definition.subflow_revision_id) return subflowRevisions(items).find(item => item.code === definition.ref && (item.revision_id || item.latest_revision_id) === definition.subflow_revision_id)
  return items.find(item => item.code === definition.ref)
}

export function subflowNodeDefinition(item, kind = 'subflow') {
  return { kind, ref: item.code, params: {}, ...(kind === 'subflow' ? { subflow_revision_id: item.revision_id || item.latest_revision_id } : { operator_version: item.version }) }
}

export function subflowPrimaryName(item = {}) {
  return item.display_name_zh || item.name || item.code || ''
}

export function subflowEnglishName(item = {}) {
  const primary = subflowPrimaryName(item)
  const englishName = item.englishName || (item.display_name_zh ? item.name : '')
  return englishName && englishName !== primary ? englishName : ''
}

export function subflowSubtitle(item = {}, showTechnicalCode = false) {
  const values = []
  const englishName = subflowEnglishName(item)
  if (englishName) values.push(englishName)
  if (showTechnicalCode && item.code && item.code !== englishName) values.push(item.code)
  if (item.revision != null) values.push(`r${item.revision}`)
  return values.join(' · ')
}

export function resolveNodeMetadata(definition, catalog = [], subflows = []) {
  const status = definition.status || 'idle'
  if (definition.kind === 'knowledge_sink') {
    const outputKey = definition.output_key || (definition.knowledge_type === 'graph' && definition.graph_mode
      ? `graph:${definition.graph_mode}` : definition.knowledge_type)
    return {
      kind: 'knowledge_sink', nodeRole: 'knowledge_output', name: '知识输出', code: outputKey, category: '正式知识输出', status,
      known: true, inputs: { input: { artifact_type: `candidate:${outputKey}`, cardinality: 'one', required: true, binding: 'edge' } }, outputs: {}, parameterSchema: {},
      inputExample: { input: [{ canonical_content: '示例正式知识', source_chunk_id: 'chunk-example-001' }] }, outputExample: {},
    }
  }
  if (definition.kind === 'subflow') {
    const subflow = resolveSubflow(definition, subflows)
    const child = subflow?.definition || {}
    const entry = child.nodes?.find(node => node.id === child.entry_node)
    const exit = child.nodes?.find(node => node.id === child.exit_node)
    const entryItem = entry?.kind === 'operator' ? catalogItem(catalog, entry.ref, entry.operator_version) : null
    const exitItem = exit?.kind === 'operator' ? catalogItem(catalog, exit.ref, exit.operator_version) : null
    return {
      kind: 'subflow', nodeRole: 'operator', name: subflowPrimaryName(subflow || { code: definition.ref }),
      englishName: subflowEnglishName(subflow || {}), code: definition.ref, category: '可复用子流程', status,
      known: Boolean(subflow),
      revision: subflow?.revision, revisionId: definition.subflow_revision_id, subflowId: subflow?.id,
      versionLocked: Boolean(definition.subflow_revision_id), internalCount: child.nodes?.length || 0, internalEdgeCount: child.edges?.length || 0,
      inputs: normalizedPorts(Object.keys(subflow?.input_contract || {}).length ? subflow.input_contract : entryItem?.input_ports, DEFAULT_INPUT),
      outputs: normalizedPorts(Object.keys(subflow?.output_contract || {}).length ? subflow.output_contract : exitItem?.output_ports, DEFAULT_OUTPUT), parameterSchema: {},
      inputExample: entryItem?.input_example || {}, outputExample: exitItem?.output_example || {},
    }
  }
  const catalogVersion = catalogItem(catalog, definition.ref, definition.operator_version)
  const item = definition.operator_spec || catalogVersion
  const role = nodeRole({ ...definition, node_role: definition.node_role || item?.node_role })
  return {
    kind: 'operator', nodeRole: role, name: item?.display_name_zh || item?.name || definition.ref, englishName: item?.name || definition.ref,
    code: definition.ref, category: item?.category || '未知算子', status,
    provider: item?.runtime_requirements?.provider || item?.provider || 'dataforge', dependencyStatus: catalogVersion?.dependency_status || { status: 'unknown', reason: '该版本不在当前可用目录中' },
    known: Boolean(item),
    inputs: normalizedPorts(item?.input_ports, DEFAULT_INPUT), outputs: normalizedPorts(item?.output_ports, DEFAULT_OUTPUT),
    parameterSchema: item?.parameter_schema || {}, inputExample: item?.input_example || {}, outputExample: item?.output_example || {}, version: item?.version,
  }
}

export function hasEditableParameters(node) {
  return node?.data?.meta?.kind === 'operator' && Object.keys(node?.data?.meta?.parameterSchema?.properties || {}).length > 0
}

export function makeCanvasNode(definition, position, catalog = [], subflows = []) {
  const normalized = { ...cloneValue(definition), node_role: nodeRole(definition) }
  const meta = resolveNodeMetadata(normalized, catalog, subflows)
  return {
    id: definition.id,
    type: meta.nodeRole === 'flow_input' ? 'flow-input' : meta.kind === 'knowledge_sink' ? 'knowledge-sink' : meta.kind,
    position: { x: Number(position?.x) || 0, y: Number(position?.y) || 0 },
    data: { definition: normalized, meta },
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
    return { ...edge, data: { status: raw.status || 'idle', artifactIds: raw.artifact_ids || [], label: runtimeArtifactLabel(type, raw.record_count), technicalLabel: type } }
  })
  return graph
}

export function serializeDefinition(nodes, edges) {
  return {
    schema_version: 3,
    nodes: nodes.map(node => ({ ...cloneValue(node.data.definition), id: node.id, node_role: nodeRole(node.data.definition) })),
    edges: edges.map(edge => ({
      source: edge.source, source_port: edge.sourceHandle || 'output',
      target: edge.target, target_port: edge.targetHandle || 'input',
    })),
    ui: { positions: Object.fromEntries(nodes.map(node => [node.id, { x: node.position.x, y: node.position.y }])) },
  }
}

export function connectionIssue(connection, nodes, edges) {
  const result = checkEdgeCompatibility({
    flowContext: { schemaVersion: 3 }, nodes, edges,
    sourceNodeId: connection.source, sourcePortId: connection.sourceHandle || 'output',
    targetNodeId: connection.target, targetPortId: connection.targetHandle || 'input',
    originalEdgeId: connection.id || null,
  })
  return result.allowed ? null : { code: result.reasonCode, message: result.message, nodeId: result.nodeId,
    resolvedSourceType: result.resolvedSourceType, resolvedTargetType: result.resolvedTargetType }
}

export function createsCycle(nodes, edges, candidate = null) {
  return edgeCreatesCycle(nodes, edges, candidate)
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
    const role = node.data.meta.nodeRole || nodeRole(node.data.definition)
    if (nodes.length > 1 && !incoming.length && !outgoing.length) issues.push({ code: 'ISOLATED_NODE', message: '节点未接入流程', nodeId: node.id })
    if (role === 'flow_input' && incoming.length) issues.push({ code: 'FLOW_INPUT_HAS_INCOMING', message: '流程输入不能连接 Incoming Edge', nodeId: node.id })
    if (role === 'flow_input' && nodes.length > 1 && !outgoing.length) issues.push({ code: 'FLOW_INPUT_UNUSED', message: '流程输入必须连接至少一个下游节点', nodeId: node.id })
    if (role === 'knowledge_output' && outgoing.length) issues.push({ code: 'SINK_NOT_TERMINAL', message: '知识输出必须是终点', nodeId: node.id })
    for (const [port, spec] of Object.entries(node.data.meta.inputs || {})) {
      if ((spec.binding || 'edge') !== 'edge' || spec.required === false) continue
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

export function validateSubflow(nodes, edges) {
  const entries = nodes.filter(node => !edges.some(edge => edge.target === node.id))
  const exits = nodes.filter(node => !edges.some(edge => edge.source === node.id))
  const issues = validateFlow(nodes, edges).filter(issue => issue.code !== 'MISSING_SINK' && !(issue.code === 'REQUIRED_INPUT' && entries.some(node => node.id === issue.nodeId)))
  if (entries.length !== 1 || exits.length !== 1) issues.push({ code: 'SUBFLOW_BOUNDARY', message: '子流程必须连通、单入口、单出口' })
  for (const node of nodes) if (node.data.meta.nodeRole !== 'operator' || node.data.definition.ref === 'reviewed-source-chunk-input') issues.push({ code: 'SUBFLOW_NODE_ROLE', nodeId: node.id, message: '子流程不能包含流程输入或知识输出' })
  return issues
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
