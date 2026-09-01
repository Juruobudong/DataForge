import { nodeFailureInfo } from '../../components/flow/runtimeDiagnostics.js'

export const RESULT_PAGE_SIZE = 50
const labels = { text: '文本', 'qa-question': '问答·Q检索', 'qa-full': '问答·QA检索', 'graph:triple': '三元组图谱', 'graph:semantic': '语义图谱' }
const terminal = new Set(['completed', 'completed_with_warnings', 'failed', 'cancelled'])

export function finalOutputKey(node) {
  return node.output_key || (node.knowledge_type === 'graph' ? `graph:${node.graph_mode || 'triple'}` : node.knowledge_type)
}

export function finalResultOutputs(detail) {
  const graph = detail?.runtime_dag || { nodes: [], edges: [] }
  const records = new Map((detail?.nodes || []).map(node => [node.node_id, node]))
  const incoming = new Map()
  for (const edge of graph.edges || []) {
    const source = Array.isArray(edge) ? edge[0] : edge.source
    const target = Array.isArray(edge) ? edge[1] : edge.target
    if (!incoming.has(target)) incoming.set(target, [])
    incoming.get(target).push(source)
  }
  return (graph.nodes || []).filter(node => node.kind === 'knowledge_sink').map(sink => {
    const key = finalOutputKey(sink)
    const preview = detail?.sink_previews?.find(item => item.output_key === key)
    const ancestors = new Set(), pending = [sink.id]
    while (pending.length) {
      const id = pending.pop()
      if (ancestors.has(id)) continue
      ancestors.add(id)
      pending.push(...(incoming.get(id) || []))
    }
    const diagnostics = (graph.nodes || []).filter(node => ancestors.has(node.id)).flatMap(node => {
      const record = records.get(node.id)
      if (!record) return []
      const processing = (Array.isArray(record.metrics?.chunk_processing) ? record.metrics.chunk_processing : [])
        .filter(item => item.output_key === key)
      const { failed, loggedError, reasons, explanation } = nodeFailureInfo(record)
      if (!processing.length && !failed && !loggedError) return []
      return [{ nodeId: node.id, name: node.operator_spec?.display_name_zh || node.operator_spec?.name || node.label || node.ref || node.id, processing, failed, loggedError, reasons, explanation }]
    })
    const count = preview?.candidate_count ?? preview?.quality?.candidate_count
    const hasWarning = diagnostics.some(item => item.failed || item.loggedError || item.processing.some(stat => stat.failed_chunks > 0))
    const allFailed = diagnostics.some(item => item.failed || item.processing.some(stat => stat.failed_chunks > 0 && stat.successful_chunks === 0))
    const sinkStatus = records.get(sink.id)?.status || sink.status
    let state = 'waiting', status = '等待输出'
    if (hasWarning) {
      state = 'warning'
      status = allFailed && !count ? '处理失败' : '部分处理失败'
    } else if (preview) {
      state = 'ready'
      status = count === 0 ? (diagnostics.some(item => item.processing.length) ? '成功零产出' : '零条结果') : '有结果'
    } else if (sinkStatus === 'skipped' && (records.has(sink.id) || terminal.has(detail?.status))) {
      state = 'unavailable'; status = '已跳过'
    } else if (terminal.has(detail?.status)) {
      state = 'unavailable'; status = detail.status === 'cancelled' ? '已停止，尚未到达输出' : '尚未到达输出'
    }
    return { id: sink.id, key, label: labels[key] || key || '扩展知识', preview, count, diagnostics, hasWarning, state, status }
  })
}

export function finalResultColumns(key) {
  if (['qa-question', 'qa-full'].includes(key)) return ['问题', '答案']
  if (key === 'graph:triple') return ['主体', '关系', '客体', '客体类别']
  if (key === 'graph:semantic') return ['来源实体', '关系', '目标实体']
  return [key === 'text' ? '正文' : '正文摘要']
}

export function finalResultCells(key, item) {
  const data = item.data_json || {}
  if (['qa-question', 'qa-full'].includes(key)) return [data.question, data.answer]
  if (key === 'graph:triple') return [data.subject, data.predicate || data.predicate_code, data.object,
    data.data?.object_kind === 'literal' ? '字面值' : data.data?.object_kind === 'entity' ? '实体' : '未记录']
  if (key === 'graph:semantic') return [data.source_entity?.name, data.relation?.type_label || data.relation?.type, data.target_entity?.name]
  return [item.canonical_content]
}
