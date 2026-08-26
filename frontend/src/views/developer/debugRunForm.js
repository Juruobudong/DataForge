export const NO_DEBUG_REVIEW_INPUTS = '暂无可用于调试的当前审核快照。请先完成文档上传、Source Preparation 和人工审核。'

export function debugRunPreflightIssue(debugOptions, selectedReviewIds = [], sinkBindings = {}, inputSource = 'source_review_snapshot') {
  if (!debugOptions) return '调试选项尚未加载。'
  if (inputSource === 'builtin_sample') return ''
  if (!(debugOptions.review_inputs || []).length) return NO_DEBUG_REVIEW_INPUTS
  if (!selectedReviewIds.length) return '至少选择一份同一文档库中的审核文档。'
  const missingOutputKeys = (debugOptions.sink_requirements || [])
    .filter(item => !String(sinkBindings[item.output_key] || '').trim())
    .map(item => item.output_key)
  return missingOutputKeys.length
    ? `请为以下输出选择预览知识库：${missingOutputKeys.join('、')}`
    : ''
}
