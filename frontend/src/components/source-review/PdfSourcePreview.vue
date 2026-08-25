<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import 'pdfjs-dist/web/pdf_viewer.css'
import { anchorNotice, pdfHighlights, pdfTargetPages } from './sourceAnchorModel'

const props = defineProps({ url: { type: String, required: true }, anchor: { type: Object, default: () => ({}) } })
const viewport = ref(null), pages = ref([]), loading = ref(true), error = ref(''), scale = ref(1.15), fit = ref(true)
const pageElements = new Map(), visiblePages = new Set(), renderPromises = new Map()
let loadingTask = null, pdf = null, pdfjs = null, observer = null, locateVersion = 0

const targetPages = computed(() => pdfTargetPages(props.anchor))
const notice = computed(() => anchorNotice(props.anchor))

function pageStyle(page) {
  return { width: `${page.width * scale.value}px`, height: `${page.height * scale.value}px`, '--scale-factor': scale.value }
}

function setPageElement(number, element) {
  const previous = pageElements.get(number)
  if (previous && observer) observer.unobserve(previous)
  if (!element) { pageElements.delete(number); return }
  pageElements.set(number, element)
  observer?.observe(element)
}

async function renderPage(number) {
  const meta = pages.value[number - 1], element = pageElements.get(number)
  if (!pdf || !meta || !element || meta.renderedScale === scale.value || renderPromises.has(number)) return renderPromises.get(number)
  const promise = (async () => {
    const page = await pdf.getPage(number)
    const pageViewport = page.getViewport({ scale: scale.value })
    const canvas = element.querySelector('canvas'), context = canvas.getContext('2d')
    const outputScale = window.devicePixelRatio || 1
    canvas.width = Math.floor(pageViewport.width * outputScale)
    canvas.height = Math.floor(pageViewport.height * outputScale)
    canvas.style.width = `${pageViewport.width}px`; canvas.style.height = `${pageViewport.height}px`
    await page.render({ canvasContext: context, viewport: pageViewport, transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0] }).promise
    const textContainer = element.querySelector('.textLayer')
    textContainer.replaceChildren()
    await new pdfjs.TextLayer({ textContentSource: await page.getTextContent(), container: textContainer, viewport: pageViewport }).render()
    meta.renderedScale = scale.value
  })().catch(value => { error.value = `PDF 第 ${number} 页渲染失败：${value.message || value}` }).finally(() => renderPromises.delete(number))
  renderPromises.set(number, promise)
  return promise
}

async function locateAnchor() {
  const version = ++locateVersion
  if (!pdf || !targetPages.value.length) return
  await Promise.all(targetPages.value.map(renderPage))
  await nextTick()
  if (version !== locateVersion) return
  const firstPage = targetPages.value[0], element = pageElements.get(firstPage)
  const highlight = element?.querySelector('.source-highlight')
  ;(highlight || element)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

async function load() {
  loading.value = true; error.value = ''; pages.value = []
  try {
    await loadingTask?.destroy(); await pdf?.destroy()
    loadingTask = null; pdf = null; pageElements.clear(); visiblePages.clear(); renderPromises.clear()
    pdfjs ||= await import('pdfjs-dist')
    pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()
    loadingTask = pdfjs.getDocument({ url: props.url })
    pdf = await loadingTask.promise
    const values = []
    for (let number = 1; number <= pdf.numPages; number += 1) {
      const page = await pdf.getPage(number), base = page.getViewport({ scale: 1 })
      values.push({ number, width: base.width, height: base.height, renderedScale: 0 })
    }
    pages.value = values
    await nextTick(); applyFit(); await locateAnchor()
  } catch (value) { error.value = `PDF 加载失败：${value.message || value}` }
  finally { loading.value = false }
}

function applyFit() {
  if (!fit.value || !viewport.value || !pages.value.length) return
  scale.value = Math.max(.5, Math.min(2.5, (viewport.value.clientWidth - 44) / pages.value[0].width))
}
function zoom(delta) { fit.value = false; scale.value = Math.max(.5, Math.min(3, Math.round((scale.value + delta) * 10) / 10)) }
function fitWidth() { fit.value = true; applyFit() }

watch(() => props.url, load)
watch(() => props.anchor, locateAnchor, { deep: true })
watch(scale, async () => {
  pages.value.forEach(page => { page.renderedScale = 0 })
  await nextTick()
  await Promise.all([...new Set([...visiblePages, ...targetPages.value])].map(renderPage))
  await locateAnchor()
})

onMounted(() => {
  observer = new IntersectionObserver(entries => entries.forEach(entry => {
    const number = Number(entry.target.dataset.pageNumber)
    if (entry.isIntersecting) { visiblePages.add(number); renderPage(number) } else visiblePages.delete(number)
  }), { root: viewport.value, rootMargin: '500px 0px' })
  load()
})
onBeforeUnmount(() => { observer?.disconnect(); loadingTask?.destroy(); pdf?.destroy() })
</script>

<template>
  <section ref="viewport" class="pdf-viewport">
    <nav class="pdf-toolbar" aria-label="PDF 预览工具栏">
      <span>{{ pages.length ? `${targetPages[0] || 1} / ${pages.length} 页` : 'PDF' }}</span>
      <div><button type="button" title="缩小" @click="zoom(-.1)">−</button><button type="button" :class="{ active: fit }" @click="fitWidth">适宽</button><button type="button" title="放大" @click="zoom(.1)">＋</button></div>
    </nav>
    <p v-if="notice" class="anchor-notice">{{ notice }}</p>
    <p v-if="loading" class="preview-state">正在加载 PDF…</p>
    <p v-if="error" class="preview-state error">{{ error }}</p>
    <div class="pdf-pages">
      <article v-for="page in pages" :key="page.number" :ref="element => setPageElement(page.number, element)" class="pdf-page" :class="{ targeted: targetPages.includes(page.number) }" :data-page-number="page.number" :style="pageStyle(page)">
        <canvas />
        <div class="textLayer" />
        <div class="highlight-layer" aria-hidden="true">
          <i v-for="(position, index) in pdfHighlights(anchor, page.number)" :key="`${position.block_id}-${index}`" class="source-highlight" :class="{ primary: page.number === targetPages[0] && index === 0 }" :style="{ left: `${position.bbox[0] * 100}%`, top: `${position.bbox[1] * 100}%`, width: `${(position.bbox[2] - position.bbox[0]) * 100}%`, height: `${(position.bbox[3] - position.bbox[1]) * 100}%` }" />
        </div>
        <small>第 {{ page.number }} 页</small>
      </article>
    </div>
  </section>
</template>

<style scoped>
.pdf-viewport{position:relative;height:720px;overflow:auto;background:#edf1f6}.pdf-toolbar{position:sticky;top:0;z-index:12;display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--border);background:rgba(255,255,255,.96)}.pdf-toolbar div{display:flex;gap:5px}.pdf-toolbar button{min-height:34px;padding:5px 10px}.pdf-toolbar button.active{color:var(--blue);background:var(--blue-soft)}.anchor-notice{position:sticky;top:51px;z-index:11;margin:0;padding:7px 12px;border-bottom:1px solid #ead39a;color:#805b0a;background:#fff7dd;font-size:12px}.preview-state{padding:28px;text-align:center;color:var(--muted)}.pdf-pages{display:grid;justify-items:center;gap:18px;padding:20px}.pdf-page{position:relative;flex:none;background:#fff;box-shadow:0 4px 18px rgba(48,61,78,.16)}.pdf-page.targeted{box-shadow:0 0 0 2px #b9cff8,0 4px 18px rgba(48,61,78,.16)}canvas,.textLayer,.highlight-layer{position:absolute;inset:0}.textLayer{z-index:2}.highlight-layer{z-index:3;pointer-events:none}.source-highlight{position:absolute;border:1px solid rgba(47,111,237,.72);border-radius:2px;background:rgba(74,137,255,.25);box-shadow:0 0 0 1px rgba(255,255,255,.35) inset}.source-highlight.primary{background:rgba(255,190,46,.38);border-color:#e3a615}.pdf-page>small{position:absolute;right:7px;bottom:5px;z-index:4;padding:2px 6px;border-radius:9px;color:#fff;background:rgba(31,43,57,.62);font-size:10px}.error{color:var(--red)}
</style>
