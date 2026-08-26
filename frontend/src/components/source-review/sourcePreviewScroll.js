function finiteNumber(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

export function scrollTargetWithin(container, target, options = {}) {
  if (!container || !target || typeof container.getBoundingClientRect !== 'function' || typeof target.getBoundingClientRect !== 'function' || typeof container.scrollTo !== 'function') return false
  const containerRect = container.getBoundingClientRect()
  const targetRect = target.getBoundingClientRect()
  const clientHeight = Math.max(0, finiteNumber(container.clientHeight))
  const currentTop = finiteNumber(container.scrollTop)
  const targetTop = finiteNumber(targetRect.top) - finiteNumber(containerRect.top) + currentTop
  const targetHeight = Math.max(0, finiteNumber(targetRect.height, finiteNumber(target.offsetHeight)))
  const align = options.align === 'start' ? 'start' : 'center'
  const offset = Math.max(0, finiteNumber(options.offset))
  const requestedTop = align === 'start'
    ? targetTop - offset
    : targetTop + targetHeight / 2 - clientHeight / 2
  const maximumTop = Math.max(0, finiteNumber(container.scrollHeight) - clientHeight)
  const top = Math.min(maximumTop, Math.max(0, requestedTop))
  container.scrollTo({ top, behavior: options.behavior || 'smooth' })
  return true
}

export function visiblePageNumber(container, pageElements, fallback = 1, stickyOffset = 0) {
  if (!container || typeof container.getBoundingClientRect !== 'function') return fallback
  const containerRect = container.getBoundingClientRect()
  const viewportTop = finiteNumber(containerRect.top) + Math.max(0, finiteNumber(stickyOffset))
  const viewportBottom = finiteNumber(containerRect.bottom, viewportTop + finiteNumber(container.clientHeight))
  const marker = viewportTop + Math.max(0, viewportBottom - viewportTop) / 2
  let result = fallback, bestDistance = Number.POSITIVE_INFINITY
  for (const [number, element] of pageElements || []) {
    if (!element || typeof element.getBoundingClientRect !== 'function') continue
    const rect = element.getBoundingClientRect()
    const top = finiteNumber(rect.top), bottom = finiteNumber(rect.bottom, top + finiteNumber(rect.height))
    const distance = marker < top ? top - marker : marker > bottom ? marker - bottom : 0
    if (distance < bestDistance) {
      bestDistance = distance
      result = finiteNumber(number, fallback)
    }
  }
  return result
}
