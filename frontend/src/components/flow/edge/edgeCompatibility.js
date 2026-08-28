import { edgeMessage } from './edgeMessages.js'

/**
 * @typedef {Object} ResolvedPortContract
 * @property {string} rawType
 * @property {string} kind
 * @property {string=} knowledgeType
 * @property {string=} graphMode
 * @property {string} resolvedType
 */

/** @typedef {{allowed:boolean, reasonCode?:string, message?:string, resolvedSourceType?:string, resolvedTargetType?:string, nodeId?:string}} EdgeCompatibility */

export const SNAP_RADIUS = 28
export const portKey = (nodeId, direction, portId) => `${nodeId}::${direction}::${portId}`

function definitionOf(node) { return node?.data?.definition || node || {} }
function metaOf(node) { return node?.data?.meta || {} }
function paramsOf(node) { return definitionOf(node).params || {} }
function roleOf(node) {
  const definition = definitionOf(node), meta = metaOf(node)
  if (meta.nodeRole) return meta.nodeRole
  if (definition.node_role) return definition.node_role
  if (definition.kind === 'knowledge_sink') return 'knowledge_output'
  if (definition.kind === 'operator' && definition.ref === 'reviewed-source-chunk-input') return 'flow_input'
  return 'operator'
}

function outputContext(value) {
  const raw = String(value || '')
  if (!raw) return null
  const [knowledgeType, graphMode] = raw.split(':')
  return { knowledgeType, graphMode: graphMode || undefined }
}

function sinkContext(node) {
  const definition = definitionOf(node)
  if (definition.kind !== 'knowledge_sink') return null
  const output = outputContext(definition.output_key)
  return {
    knowledgeType: definition.knowledge_type || output?.knowledgeType,
    graphMode: definition.graph_mode || output?.graphMode || undefined,
  }
}

function reachableContexts(nodeId, nodesById, outgoing, trail = new Set()) {
  if (trail.has(nodeId)) return []
  const nextTrail = new Set(trail); nextTrail.add(nodeId)
  const values = []
  const sink = sinkContext(nodesById.get(nodeId))
  if (sink?.knowledgeType) values.push(sink)
  for (const target of outgoing.get(nodeId) || []) values.push(...reachableContexts(target, nodesById, outgoing, nextTrail))
  return values.filter((value, index, all) => index === all.findIndex(other => other.knowledgeType === value.knowledgeType && other.graphMode === value.graphMode))
}

function candidateParts(rawType) {
  if (!rawType.startsWith('candidate:')) return {}
  const parts = rawType.split(':')
  return { knowledgeType: parts[1] && parts[1] !== '*' ? parts[1] : undefined, graphMode: parts[1] === 'graph' ? parts[2] : undefined }
}

/** @returns {ResolvedPortContract} */
export function resolvePortContract({ node, port, nodes = [], edges = [], flowContext = {}, trail = new Set() }) {
  let rawType = String(port?.artifact_type || '')
  let acceptedTypes = port?.accepted_types
  if (port?.output_by_input && !trail.has(node.id)) {
    const incoming = edges.filter(edge => edge.target === node.id)
    const edge = incoming.length === 1 ? incoming[0] : null
    const parent = edge && nodes.find(item => item.id === edge.source)
    const parentPort = parent && metaOf(parent).outputs?.[edge.sourceHandle || edge.source_port || 'output']
    const inputType = parentPort && resolvePortContract({ node: parent, port: parentPort, nodes, edges, flowContext, trail: new Set([...trail, node.id]) }).resolvedType
    rawType = port.output_by_input[inputType] || rawType
    if (rawType === 'text_record_set') acceptedTypes = [...new Set(Object.values(port.output_by_input))]
  }
  if (acceptedTypes) return { rawType, kind: rawType, resolvedType: rawType, acceptedTypes }
  if (!rawType.startsWith('candidate:')) return { rawType, kind: rawType, resolvedType: rawType }
  const declared = candidateParts(rawType), params = paramsOf(node)
  let knowledgeType = declared.knowledgeType || params.knowledge_type || undefined
  let graphMode = declared.graphMode || (knowledgeType === 'graph' ? params.graph_mode || undefined : undefined)
  const nodesById = new Map(nodes.map(item => [item.id, item]))
  const outgoing = new Map(nodes.map(item => [item.id, []]))
  for (const edge of edges) {
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, [])
    outgoing.get(edge.source).push(edge.target)
  }
  let contexts = reachableContexts(node.id, nodesById, outgoing)
  if (!contexts.length) contexts = (flowContext.outputTypes || []).map(outputContext).filter(Boolean)
  if (contexts.length === 1) {
    knowledgeType ||= contexts[0].knowledgeType
    if (knowledgeType === 'graph') graphMode ||= contexts[0].graphMode
  }
  let resolvedType = rawType
  if (knowledgeType) resolvedType = `candidate:${knowledgeType}${knowledgeType === 'graph' && graphMode ? `:${graphMode}` : ''}`
  return { rawType, kind: 'candidate', knowledgeType, graphMode, resolvedType }
}

function issue(reasonCode, data = {}) {
  return { allowed: false, reasonCode, message: edgeMessage(reasonCode, data.message), ...data }
}

function contractIssue(source, target) {
  if (source.acceptedTypes || target.acceptedTypes) {
    const sources = source.acceptedTypes || [source.resolvedType], targets = target.acceptedTypes || [target.resolvedType]
    if (sources.some(actual => targets.some(expected => actual === expected || expected.endsWith(':*') && actual.startsWith(expected.slice(0, -1))))) return null
  }
  if (source.kind === 'candidate' && target.kind === 'candidate') {
    source.knowledgeType ||= target.knowledgeType
    target.knowledgeType ||= source.knowledgeType
    if (source.knowledgeType === 'graph' && target.knowledgeType === 'graph') {
      source.graphMode ||= target.graphMode
      target.graphMode ||= source.graphMode
    }
    if (source.knowledgeType) source.resolvedType = `candidate:${source.knowledgeType}${source.knowledgeType === 'graph' && source.graphMode ? `:${source.graphMode}` : ''}`
    if (target.knowledgeType) target.resolvedType = `candidate:${target.knowledgeType}${target.knowledgeType === 'graph' && target.graphMode ? `:${target.graphMode}` : ''}`
  }
  const types = { resolvedSourceType: source.resolvedType, resolvedTargetType: target.resolvedType }
  if (source.kind !== target.kind) return issue('PORT_TYPE_MISMATCH', types)
  if (source.kind !== 'candidate') return source.resolvedType === target.resolvedType ? null : issue('PORT_TYPE_MISMATCH', types)
  if (!source.knowledgeType || !target.knowledgeType) return issue('OPERATOR_CONTRACT_MISMATCH', types)
  if (source.knowledgeType !== target.knowledgeType) return issue('KNOWLEDGE_TYPE_MISMATCH', types)
  if (source.knowledgeType === 'graph') {
    if (!source.graphMode || !target.graphMode) return issue('OPERATOR_CONTRACT_MISMATCH', types)
    if (source.graphMode !== target.graphMode) return issue('GRAPH_MODE_MISMATCH', types)
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

/** @returns {EdgeCompatibility} */
export function checkEdgeCompatibility({ flowContext = {}, nodes = [], edges = [], sourceNodeId, sourcePortId = 'output', targetNodeId, targetPortId = 'input', originalEdgeId = null }) {
  if (![2, 3].includes(Number(flowContext.schemaVersion ?? 3))) return issue('FLOW_DSL_VERSION_UNSUPPORTED')
  const source = nodes.find(node => node.id === sourceNodeId), target = nodes.find(node => node.id === targetNodeId)
  if (!source) return issue('SOURCE_NODE_NO_OUTPUT')
  if (!target) return issue('TARGET_NODE_NO_INPUT')
  if (source.id === target.id) return issue('EDGE_SELF_LOOP', { nodeId: source.id })
  if (roleOf(source) === 'knowledge_output' || definitionOf(source).kind === 'knowledge_sink') return issue('SINK_NODE_CANNOT_HAVE_OUTGOING', { nodeId: source.id })
  if (roleOf(target) === 'flow_input') return issue('INPUT_NODE_CANNOT_HAVE_INCOMING', { nodeId: target.id })
  const sourcePort = metaOf(source).outputs?.[sourcePortId], targetPort = metaOf(target).inputs?.[targetPortId]
  if (!sourcePort && metaOf(source).inputs?.[sourcePortId]) return issue('EDGE_DIRECTION_INVALID', { nodeId: source.id })
  if (!targetPort && metaOf(target).outputs?.[targetPortId]) return issue('EDGE_DIRECTION_INVALID', { nodeId: target.id })
  if (!sourcePort) return issue('SOURCE_NODE_NO_OUTPUT', { nodeId: source.id })
  if (!targetPort) return issue('TARGET_NODE_NO_INPUT', { nodeId: target.id })
  if ((targetPort.binding || 'edge') !== 'edge') return issue('INPUT_NODE_CANNOT_HAVE_INCOMING', { nodeId: target.id })
  const currentEdges = edges.filter(edge => edge.id !== originalEdgeId)
  if (currentEdges.some(edge => edge.source === source.id && edge.target === target.id && (edge.sourceHandle || 'output') === sourcePortId && (edge.targetHandle || 'input') === targetPortId)) return issue('EDGE_DUPLICATED', { nodeId: target.id })
  if (targetPort.cardinality !== 'many' && currentEdges.some(edge => edge.target === target.id && (edge.targetHandle || 'input') === targetPortId)) return issue('INPUT_PORT_ALREADY_CONNECTED', { nodeId: target.id })
  const candidate = { source: source.id, sourceHandle: sourcePortId, target: target.id, targetHandle: targetPortId }
  if (createsCycle(nodes, currentEdges, candidate)) return issue('EDGE_WOULD_CREATE_CYCLE', { nodeId: target.id })
  const simulatedEdges = [...currentEdges, candidate]
  const sourceContract = resolvePortContract({ node: source, port: sourcePort, nodes, edges: simulatedEdges, flowContext })
  const targetContract = resolvePortContract({ node: target, port: targetPort, nodes, edges: simulatedEdges, flowContext })
  const mismatch = contractIssue(sourceContract, targetContract)
  if (mismatch) return { ...mismatch, nodeId: target.id }
  return { allowed: true, resolvedSourceType: sourceContract.resolvedType, resolvedTargetType: targetContract.resolvedType }
}

export function buildCompatibilityMap({ mode, flowContext, nodes, edges, sourceNodeId, sourcePortId, targetNodeId, targetPortId, originalEdgeId }) {
  const result = new Map()
  if (mode === 'reconnecting-source') {
    for (const node of nodes) for (const portId of Object.keys(metaOf(node).outputs || {})) {
      const value = checkEdgeCompatibility({ flowContext, nodes, edges, sourceNodeId: node.id, sourcePortId: portId, targetNodeId, targetPortId, originalEdgeId })
      result.set(portKey(node.id, 'output', portId), value)
    }
  } else {
    for (const node of nodes) for (const portId of Object.keys(metaOf(node).inputs || {})) {
      const value = checkEdgeCompatibility({ flowContext, nodes, edges, sourceNodeId, sourcePortId, targetNodeId: node.id, targetPortId: portId, originalEdgeId })
      result.set(portKey(node.id, 'input', portId), value)
    }
  }
  return result
}

export function nearestPort(pointer, ports, compatibility, radius = SNAP_RADIUS) {
  let nearest = null
  for (const port of ports) {
    const distance = Math.hypot(pointer.x - port.x, pointer.y - port.y)
    if (distance > radius || nearest && nearest.distance <= distance) continue
    nearest = { ...port, distance, compatibility: compatibility.get(port.key) }
  }
  return nearest
}
