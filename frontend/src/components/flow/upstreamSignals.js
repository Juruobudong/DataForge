import { resolveSubflow } from './flowModel.js'

const signalCodes = new Set(['Text2QASampleEvaluator', 'PromptedEvaluator', 'SemDeduplicateFilter'])
const endpoints = edge => Array.isArray(edge) ? edge : [edge.source, edge.target]

function ancestors(edges, target, includeTarget = false) {
  const found = new Set(includeTarget ? [target] : []), pending = [target]
  while (pending.length) {
    const id = pending.pop()
    for (const edge of edges) {
      const [source, destination] = endpoints(edge)
      if (destination === id && !found.has(source)) { found.add(source); pending.push(source) }
    }
  }
  return found
}

export function upstreamSignals(nodes, edges, selectedId, catalog, subflows) {
  const eligible = ancestors(edges, selectedId), result = []
  function visit(raw, prefix = '', stack = new Set()) {
    if (raw.kind === 'subflow') {
      const child = resolveSubflow(raw, subflows), identity = raw.subflow_revision_id || raw.ref
      if (!child?.definition || stack.has(identity)) return
      const definition = child.definition
      const reachable = ancestors(definition.edges || [], definition.exit_node, true)
      const nested = new Set([...stack, identity])
      for (const node of definition.nodes || []) if (reachable.has(node.id)) visit(node, `${prefix}${raw.id}::`, nested)
    } else if (signalCodes.has(raw.ref)) {
      const id = `${prefix}${raw.id}`, item = catalog.find(item => item.code === raw.ref)
      result.push({ id, operator: raw.ref, label: `${item?.display_name_zh || item?.name || raw.ref} · ${id}` })
    }
  }
  for (const node of nodes) if (eligible.has(node.id)) visit({ ...(node.data?.definition || node), id: node.id })
  return result
}
