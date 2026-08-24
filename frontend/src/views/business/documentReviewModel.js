export function documentProductionStage(source = {}) {
  const version = source.version || {}
  if (version.preparation_status === 'queued') return '等待解析'
  if (version.preparation_status === 'running') return '解析与分块中'
  if (version.preparation_status === 'failed') return '解析失败'
  return { pending: '待审核', in_review: '审核中', approved: '审核通过', rejected: '审核未通过' }[version.review_status] || source.status
}

export function canApproveDocument(review = {}) {
  const counts = review.counts || {}
  return review.preparation_status === 'completed' && Number(counts.total || 0) > 0 && Number(counts.rejected || 0) === 0
}
