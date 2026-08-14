import { connectionIssue, createsCycle } from '../flowModel.js'

export function useFlowConnections(nodes, edges, reportError) {
  function issueFor(connection) {
    const currentEdges = connection.id ? edges.value.filter(edge => edge.id !== connection.id) : edges.value
    return connectionIssue(connection, nodes.value, currentEdges) ||
      (createsCycle(nodes.value, currentEdges, connection) ? { code: 'CYCLE', message: '此连线会形成环路' } : null)
  }
  function isValidConnection(connection) { return !issueFor(connection) }
  function addTypedEdge(connection) {
    const issue = issueFor(connection)
    if (issue) { reportError(issue); return false }
    edges.value.push({
      id: `edge-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
      type: 'dataforge', ...connection,
      sourceHandle: connection.sourceHandle || 'output', targetHandle: connection.targetHandle || 'input',
      data: { status: 'idle' },
    })
    reportError(null)
    return true
  }
  return { issueFor, isValidConnection, addTypedEdge }
}
