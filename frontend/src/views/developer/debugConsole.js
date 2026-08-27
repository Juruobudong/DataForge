// Runtime nodes already carry the Catalog metadata used by the canvas.
export function consoleNodeLabels(runtimeNodes = []) {
  return new Map(runtimeNodes.map(node => {
    const meta = node.data?.meta || {}
    const definition = node.data?.definition || {}
    const role = meta.nodeRole || definition.node_role
    let label = meta.known === false ? '' : meta.name
    if (role === 'flow_input') label = '已审核文档块'
    else if (role === 'knowledge_output' || definition.kind === 'knowledge_sink') label = '知识输出'
    return [node.id, label || node.id]
  }))
}

export function consoleNodePresentation(nodeId, labels) {
  if (!nodeId) return { label: '流程运行', technicalId: '' }
  const label = labels.get(nodeId) || nodeId
  return { label, technicalId: label === nodeId ? '' : nodeId }
}
