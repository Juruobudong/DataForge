// Runtime nodes already carry the Catalog metadata used by the canvas.
import { operatorLabel } from '../../components/flow/flowModel.js'

export function consoleNodeLabels(runtimeNodes = []) {
  return new Map(runtimeNodes.map(node => {
    const meta = node.data?.meta || {}
    const definition = node.data?.definition || {}
    const role = meta.nodeRole || definition.node_role
    let label = meta.known === false ? '' : operatorLabel(meta)
    if (role === 'flow_input') label = '文档输入'
    else if (role === 'knowledge_output' || definition.kind === 'knowledge_sink') label = '知识输出'
    return [node.id, label || node.id]
  }))
}

export function consoleNodePresentation(nodeId, labels) {
  if (!nodeId) return { label: '流程运行', technicalId: '' }
  const label = labels.get(nodeId) || nodeId
  return { label, technicalId: label === nodeId ? '' : nodeId }
}

export function consoleEventMessage(event) {
  if (event.type !== 'node.operator_log') return event.message
  return `[${event.payload?.stream || '日志'}]${event.payload?.truncated ? ' [已截断]' : ''} ${event.message || ''}`
}
