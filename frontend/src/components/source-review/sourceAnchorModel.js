export function normalizedPositions(anchor = {}) {
  const values = Array.isArray(anchor.positions) ? anchor.positions.filter(item => item && typeof item === 'object') : []
  return values.toSorted((left, right) => {
    if ((left.page_index ?? Number.MAX_SAFE_INTEGER) !== (right.page_index ?? Number.MAX_SAFE_INTEGER)) return (left.page_index ?? Number.MAX_SAFE_INTEGER) - (right.page_index ?? Number.MAX_SAFE_INTEGER)
    if ((left.block_index ?? Number.MAX_SAFE_INTEGER) !== (right.block_index ?? Number.MAX_SAFE_INTEGER)) return (left.block_index ?? Number.MAX_SAFE_INTEGER) - (right.block_index ?? Number.MAX_SAFE_INTEGER)
    return String(left.block_id || '').localeCompare(String(right.block_id || ''))
  })
}

export function pdfTargetPages(anchor = {}) {
  const pages = normalizedPositions(anchor).map(item => Number(item.page)).filter(Number.isFinite)
  if (!pages.length && Number(anchor.page || anchor.page_start)) pages.push(Number(anchor.page || anchor.page_start))
  return [...new Set(pages)].sort((a, b) => a - b)
}

export function pdfHighlights(anchor = {}, pageNumber) {
  return normalizedPositions(anchor).filter(item => item.kind === 'pdf_bbox' && Number(item.page) === Number(pageNumber) && Array.isArray(item.bbox) && item.bbox.length === 4)
}

export function docxBlockIds(anchor = {}) {
  return [...new Set(normalizedPositions(anchor).filter(item => item.kind === 'docx_block').map(item => String(item.block_id || '')).filter(Boolean))]
}

export function anchorLabel(anchor = {}) {
  const pages = pdfTargetPages(anchor)
  if (pages.length === 1) return `第${pages[0]}页`
  if (pages.length > 1) return `跨第${pages[0]}–${pages.at(-1)}页`
  const blocks = normalizedPositions(anchor).filter(item => item.kind === 'docx_block')
  if (blocks.length) {
    const first = Number(blocks[0].block_index) + 1
    const last = Number(blocks.at(-1).block_index) + 1
    return first === last ? `DOCX 第${first}块` : `DOCX 第${first}–${last}块`
  }
  return '来源定位不可用'
}

export function anchorNotice(anchor = {}) {
  if (anchor.precision === 'unavailable') return '来源定位不可用；可重新分块生成定位信息。'
  if (anchor.anchor_version === 1 || anchor.precision === 'page') return '当前仅支持页级定位；重新分块后可精确高亮。'
  if (anchor.precision === 'parent') return '该拆分块继承父 Chunk 的近似来源区域。'
  if (anchor.position_status === 'partial') return '部分来源区域缺少坐标，已高亮其余可用位置。'
  return ''
}
