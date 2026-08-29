// Summarize existing server-redacted logs, without deriving chunk counts from them.
const failureCode = /\b(?:QA_OUTPUT_INVALID|QA_EMPTY_OUTPUT|QA_PROTOCOL_INVALID|QA_STRUCTURED_OUTPUT_UNSUPPORTED|SOURCE_LINEAGE_MISSING|SOURCE_LINEAGE_MISMATCH|SERVING_CONFIG_DRIFT|GRAPH_[A-Z_]*(?:FAILED|INVALID|UNRESOLVED|AMBIGUOUS|LITERAL)|(?:LLM|OPERATOR)_[A-Z_]*(?:FAILED|INVALID|ERROR|TIMEOUT|CANCELLED))\b/
const ansi = /\u001b\[[0-?]*[ -/]*[@-~]/g

export function nodeFailureInfo(node) {
  const processing = Array.isArray(node?.metrics?.chunk_processing) ? node.metrics.chunk_processing : []
  const chunkFailure = processing.some(item => item.failed_chunks > 0)
  const reasons = new Set()
  const add = value => {
    if (typeof value === 'string' && value.trim()) reasons.add(value.replace(ansi, '').trim())
  }
  const error = typeof node?.error_detail === 'string' ? node.error_detail : node?.error_detail?.message
  add(error); add(node?.error)
  let loggedError = false
  let hasResponseExcerpt = false
  for (const log of node?.logs || []) {
    for (const line of String(log.message || '').replace(ansi, '').split(/\r?\n/)) {
      // Diagnostic samples are data, not error/status messages. A clipped JSON
      // record is also ignored; terminal errors are logged separately.
      if (line.startsWith('QA_DIAGNOSTIC ')) {
        try { hasResponseExcerpt ||= Boolean(JSON.parse(line.slice(14)).response_excerpt) } catch {}
        continue
      }
      const match = line.match(failureCode)
      if (match) { loggedError = true; add(line.slice(match.index)) }
    }
  }
  const failed = node?.status === 'failed' || Boolean(error || node?.error)
  const hasFailure = failed || chunkFailure || loggedError
  const qaDirectionsInvalid = [...reasons].some(reason => reason.includes('QA_OUTPUT_INVALID') && /提问方向.*JSON.*字符串数组/.test(reason))
  return {
    processing, chunkFailure, failed, loggedError, hasFailure, reasons: [...reasons],
    recoveredChunks: Number(node?.metrics?.qa_recovery?.recovered_chunks || 0),
    title: failed ? '节点执行失败' : chunkFailure ? '执行已结束，但存在分块失败' : '发现处理异常',
    explanation: hasResponseExcerpt && [...reasons].some(reason => reason.includes('QA_OUTPUT_INVALID'))
      ? '问答输出未通过格式校验；日志中已记录受限、凭据脱敏的模型响应片段。'
      : qaDirectionsInvalid
      ? '问答生成第一阶段要求模型返回 JSON 字符串数组；本次响应未通过这一格式校验。当前日志未记录原始模型响应，无法确定具体返回内容。'
      : '',
  }
}
