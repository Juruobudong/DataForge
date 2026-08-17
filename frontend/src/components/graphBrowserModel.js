import dagre from '@dagrejs/dagre'

const TYPE_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444', '#14b8a6', '#f97316', '#ec4899', '#0ea5e9']

export function graphUiState(summary) {
  if (!summary) return 'loading'
  const count = summary.stats?.entity_count ?? summary.entity_count ?? 0
  return count > 0 && summary.nodes?.length ? 'ready' : 'empty'
}

export function typeColor(typeCode) {
  if (typeCode == null || typeCode === '') return '#94a3b8'
  let hash = 0
  const key = String(typeCode)
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) >>> 0
  return TYPE_COLORS[hash % TYPE_COLORS.length]
}

export function entityTypes(nodes) {
  const values = []
  for (const node of nodes || []) {
    const key = node.type_code || node.type
    if (key && !values.some(item => item.key === key)) values.push({ key, label: node.type_label || node.type || key })
  }
  return values.sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
}

export function relationTypes(edges) {
  const values = []
  for (const edge of edges || []) {
    const key = edge.relation_type || edge.predicate
    if (key && !values.some(item => item.key === key)) values.push({ key, label: edge.relation_type_label || edge.predicate || key })
  }
  return values.sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
}

export function layoutGraph(graph) {
  const layout = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  layout.setGraph({ rankdir: 'LR', ranksep: 92, nodesep: 54, edgesep: 28, marginx: 34, marginy: 34 })
  const sourceNodes = graph?.nodes || []
  const sourceEdges = graph?.edges || []
  const widthFor = node => Math.max(130, Math.min(240, 130 + String(node.name || '').length * 10))
  sourceNodes.forEach(node => layout.setNode(node.id, { width: widthFor(node), height: 66 }))
  sourceEdges.forEach(edge => layout.setEdge(edge.source, edge.target))
  dagre.layout(layout)
  return {
    nodes: sourceNodes.map(node => {
      const point = layout.node(node.id) || { x: 0, y: 0, width: widthFor(node), height: 66 }
      const color = typeColor(node.type_code)
      return {
        id: node.id,
        data: { label: node.name, meta: node, color },
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
