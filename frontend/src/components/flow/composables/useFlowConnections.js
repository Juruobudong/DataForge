import { checkEdgeCompatibility } from '../edge/edgeCompatibility.js'

export function useFlowConnections(nodes, edges, flowContext, reportError) {
  function issueFor(connection) {
    const result = checkEdgeCompatibility({
      flowContext: flowContext.value, nodes: nodes.value, edges: edges.value,
      sourceNodeId: connection.source, sourcePortId: connection.sourceHandle || 'output',
      targetNodeId: connection.target, targetPortId: connection.targetHandle || 'input',
      originalEdgeId: connection.id || null,
    })
    return result.allowed ? null : { code: result.reasonCode, ...result }
  }
  function isValidConnection(connection) { return !issueFor(connection) }
  function addTypedEdge(connection, beforeCommit) {
    const issue = issueFor(connection)
    if (issue) { reportError(issue); return false }
    beforeCommit?.()
    edges.value.push({
      id: `edge-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
      type: 'dataforge', ...connection,
      sourceHandle: connection.sourceHandle || 'output', targetHandle: connection.targetHandle || 'input',
      data: { status: 'idle' },
    })
    reportError(null)
    return true
  }
  function reconnectTypedEdge(originalEdge, connection, beforeCommit) {
    const candidate = { ...connection, id: originalEdge.id }
    const issue = issueFor(candidate)
    if (issue) { reportError(issue); return false }
    const index = edges.value.findIndex(edge => edge.id === originalEdge.id)
    if (index < 0) return false
    beforeCommit?.()
    edges.value.splice(index, 1, {
      ...originalEdge, ...connection, id: originalEdge.id, type: originalEdge.type || 'dataforge',
      sourceHandle: connection.sourceHandle || 'output', targetHandle: connection.targetHandle || 'input',
      data: { ...(originalEdge.data || {}), status: originalEdge.data?.status || 'idle' }, selected: true,
    })
    reportError(null)
    return true
  }
  return { issueFor, isValidConnection, addTypedEdge, reconnectTypedEdge }
}
