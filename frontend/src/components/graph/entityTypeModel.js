export const entityLabel = value => String(value || '').normalize('NFKC').trim()

export function displayedEntityTypes(items, catalog) {
  const known = [...(catalog?.base || []), ...(catalog?.presets || []).flatMap(item => item.entity_types)]
  return (items || []).map(item => {
    if (typeof item !== 'string') return item
    const match = known.find(entry => [entry.code, entry.label].includes(entityLabel(item)))
    return { ...(match || { label: entityLabel(item) }), source: 'custom', preset: undefined }
  })
}

export function medicalCoverage(items, catalog) {
  const medical = catalog?.presets?.find(item => item.code === 'medical')?.entity_types || []
  const types = displayedEntityTypes(items, catalog)
  const labels = new Set(types.map(item => entityLabel(item.label)))
  const codes = new Set(types.map(item => item.code))
  return { count: medical.filter(item => labels.has(item.label) || codes.has(item.code)).length, total: medical.length }
}

export function removeEntityReferences(graphConfig, nodes, removedCodes) {
  const removed = new Set(removedCodes)
  if (!removed.size) return { graphConfig, nodes }
  const clean = item => ({ ...item, ...Object.fromEntries(['source_types', 'target_types']
    .filter(key => key in item).map(key => [key, (item[key] || []).filter(code => !removed.has(code))])) })
  return {
    graphConfig: { ...graphConfig, relation_types: (graphConfig.relation_types || []).map(clean) },
    nodes: nodes.map(node => {
      const definition = node.data.definition
      if (!['entity-extractor', 'relation-extractor', 'entity-relation-extractor'].includes(definition.ref)) return node
      const params = { ...(definition.params || {}) }
      if (['entity-extractor', 'entity-relation-extractor'].includes(definition.ref) && Array.isArray(params.entity_types)) {
        params.entity_type_scope ??= params.entity_types.length ? 'subset' : 'all'
        params.entity_types = params.entity_types.filter(code => !removed.has(code))
      }
      if (['relation-extractor', 'entity-relation-extractor'].includes(definition.ref)) params.relation_constraints = (params.relation_constraints || []).map(clean)
      return { ...node, data: { ...node.data, definition: { ...definition, params } } }
    }),
  }
}
