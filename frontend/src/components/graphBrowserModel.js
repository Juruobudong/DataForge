import dagre from '@dagrejs/dagre'

export function graphUiState(summary) {
  if (!summary) return 'loading'
  return summary.entity_count > 0 && summary.nodes?.length ? 'ready' : 'empty'
}

export function layoutGraph(graph) {
  const layout = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  layout.setGraph({ rankdir: 'LR', ranksep: 92, nodesep: 54, edgesep: 28, marginx: 34, marginy: 34 })
  const sourceNodes = graph?.nodes || []
  const sourceEdges = graph?.edges || []
  sourceNodes.forEach(node => layout.setNode(node.id, { width: 210, height: 66 }))
  sourceEdges.forEach(edge => layout.setEdge(edge.source, edge.target))
  dagre.layout(layout)
  return {
    nodes: sourceNodes.map(node => {
      const point = layout.node(node.id) || { x: 0, y: 0, width: 210, height: 66 }
      return {
        id: node.id,
        data: { label: node.name, meta: node },
        position: { x: point.x - point.width / 2, y: point.y - point.height / 2 },
      }
    }),
    edges: sourceEdges.map(edge => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.predicate,
      data: { meta: edge },
    })),
  }
}
