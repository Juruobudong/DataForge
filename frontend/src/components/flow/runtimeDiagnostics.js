// Summarize existing server-redacted logs, without deriving chunk counts from them.
const failureCode = /\b(?:QA_OUTPUT_INVALID|QA_EMPTY_OUTPUT|QA_PROTOCOL_INVALID|SOURCE_LINEAGE_MISSING|SOURCE_LINEAGE_MISMATCH|SERVING_CONFIG_DRIFT|(?:LLM|OPERATOR)_[A-Z_]*(?:FAILED|INVALID|ERROR|TIMEOUT|CANCELLED))\b/
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
  for (const log of node?.logs || []) {
    for (const line of String(log.message || '').replace(ansi, '').split(/\r?\n/)) {
      const match = line.match(failureCode)
      if (match) { loggedError = true; add(line.slice(match.index)) }
    }
  }
  const failed = node?.status === 'failed' || Boolean(error || node?.error)
  const hasFailure = failed || chunkFailure || loggedError
  const qaDirectionsInvalid = [...reasons].some(reason => reason.includes('QA_OUTPUT_INVALID') && /提问方向.*JSON.*字符串数组/.test(reason))
  return {
    processing, chunkFailure, failed, loggedError, hasFailure, reasons: [...reasons],
    title: failed ? '节点执行失败' : chunkFailure ? '执行已结束，但存在分块失败' : '发现处理异常',
    explanation: qaDirectionsInvalid
      ? '问答生成第一阶段要求模型返回 JSON 字符串数组；本次响应未通过这一格式校验。当前日志未记录原始模型响应，无法确定具体返回内容。'
      : '',
  }
}
