<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'
import PdfSourcePreview from '../../components/source-review/PdfSourcePreview.vue'

const route = useRoute(), router = useRouter()
const libraryId = route.params.libraryId, sourceId = route.params.sourceId, versionId = route.params.versionId
const detail = ref(null), content = ref(null), anchors = ref(null), selectedAnchor = ref({ precision: 'unavailable' })
const sheetIndex = ref(0), rowOffset = ref(0), error = ref(''), busy = ref(false)
const markdownDraft = ref(''), originalMarkdown = ref(''), originalTablePage = ref(null)
const reviewBusy = ref(false), notice = ref('')
let active = true, pollTimer = null

const parsed = computed(() => detail.value?.parsed_document || null)
const parseJob = computed(() => detail.value?.parse_job || null)
const source = computed(() => detail.value?.source || {})
const isPdf = computed(() => String(source.value?.version?.original_filename || source.value?.original_filename || '').toLowerCase().endsWith('.pdf'))
const anchorItems = computed(() => anchors.value?.anchors || anchors.value?.items || anchors.value?.cells || (Array.isArray(anchors.value) ? anchors.value : []))
const tableRows = computed(() => content.value?.rows || [])
const tableColumns = computed(() => Array.from({ length: Number(content.value?.sheet?.column_count || 0) }, (_, index) => index))
const dirty = computed(() => parsed.value?.kind === 'textual'
  ? markdownDraft.value !== originalMarkdown.value
  : JSON.stringify(content.value?.rows || []) !== JSON.stringify(originalTablePage.value?.rows || []))

function parseLabel() {
  return { pending: '待解析', queued: '待解析', running: '解析中', completed: '解析成功', failed: '解析失败' }[source.value?.version?.parse_status || parseJob.value?.status] || '待解析'
}
function sourceAnchor(item) { return item?.source_anchor || item?.anchor || item || { precision: 'unavailable' } }
function anchorName(item, index) {
  const anchor = sourceAnchor(item)
  if (anchor.page || anchor.page_no) return `第 ${anchor.page || anchor.page_no} 页${anchor.bbox ? ' · 区域' : ''}`
  if (anchor.sheet_index != null || anchor.row_index != null) return `${anchor.sheet || `Sheet ${Number(anchor.sheet_index || 0) + 1}`} · 行 ${anchor.row_index ?? '—'} · 列 ${anchor.column_index ?? '—'}`
  return anchor.block_id || anchor.paragraph_id || anchor.markdown_range?.start != null ? `定位 ${index + 1}` : `Anchor ${index + 1}`
}
async function loadParsedContent() {
  if (!parsed.value) { content.value = null; anchors.value = null; return }
  const params = parsed.value.kind === 'tabular' ? { sheet: sheetIndex.value, offset: rowOffset.value, limit: 200 } : {}
  const [nextContent, nextAnchors] = await Promise.all([
    api.parsedDocumentContent(parsed.value.id, params), api.parsedDocumentAnchors(parsed.value.id),
  ])
  if (!active) return
  content.value = nextContent; anchors.value = nextAnchors
  if (parsed.value.kind === 'textual') {
    markdownDraft.value = nextContent.markdown || ''; originalMarkdown.value = markdownDraft.value
  } else originalTablePage.value = structuredClone(nextContent)
}
async function load() {
  try {
    const next = await api.sourceDetail(sourceId, versionId)
    if (!active) return
    detail.value = next
    if (next.parsed_document) {
      clearInterval(pollTimer); pollTimer = null
      await loadParsedContent()
    } else if (['pending', 'queued', 'running'].includes(next.source?.version?.parse_status || next.parse_job?.status)) {
      if (!pollTimer) pollTimer = setInterval(load, 2000)
    }
  } catch (value) { error.value = value.message; clearInterval(pollTimer); pollTimer = null }
}
async function retryParse() {
  busy.value = true; error.value = ''
  try { await api.retryParseJob(versionId); await load() } catch (value) { error.value = value.message } finally { busy.value = false }
}
async function selectSheet(index) { if (dirty.value && !confirm('当前页有未提交校订，切换 Sheet 将丢失这些修改。确定继续吗？')) return; sheetIndex.value = index; rowOffset.value = 0; await loadParsedContent() }
async function changePage(delta) { if (dirty.value && !confirm('当前页有未提交校订，翻页将丢失这些修改。确定继续吗？')) return; rowOffset.value = Math.max(0, rowOffset.value + delta * 200); await loadParsedContent() }
function gridUpdates() {
  const original = new Map((originalTablePage.value?.rows || []).flatMap(row => (row.cells || []).map(cell => [`${row.row_index}:${cell.column_index}`, cell])))
  return (content.value?.rows || []).flatMap(row => (row.cells || []).flatMap(cell => {
    const before = original.get(`${row.row_index}:${cell.column_index}`)
    if (before && before.value === cell.value && before.value_type === cell.value_type) return []
    let value = cell.value
    if (cell.value_type === 'empty') value = null
    else if (cell.value_type === 'number') { value = Number(value); if (!Number.isFinite(value)) throw new Error(`行 ${row.row_index} 列 ${cell.column_index} 不是有效数字`) }
    else if (cell.value_type === 'boolean') value = value === true || value === 'true'
    else value = value == null ? '' : String(value)
    return [{ sheet_index: sheetIndex.value, row_index: row.row_index, column_index: cell.column_index, value, value_type: cell.value_type }]
  }))
}
async function approveParsedDocument() {
  if (!parsed.value || reviewBusy.value) return
  reviewBusy.value = true; error.value = ''; notice.value = ''
  try {
    const body = {
      expected_content_digest: parsed.value.content_digest,
      expected_anchor_map_digest: parsed.value.anchor_map_digest,
      ...(parsed.value.kind === 'textual' ? { markdown: markdownDraft.value } : { cell_updates: gridUpdates() }),
    }
    const result = await api.reviewParsedDocument(parsed.value.id, body)
    const started = (result.dispatches || []).filter(item => item.knowledge_job_id).length
    const blocked = (result.dispatches || []).filter(item => ['blocked', 'failed'].includes(item.status)).length
    notice.value = `解析内容已通过审阅，启动 ${started} 个模板任务${blocked ? `，${blocked} 个绑定被阻塞` : ''}。`
    await load()
  } catch (value) { error.value = value.message } finally { reviewBusy.value = false }
}
function warnUnsaved(event) { if (!dirty.value) return; event.preventDefault(); event.returnValue = '' }

onMounted(() => { load(); window.addEventListener('beforeunload', warnUnsaved) })
onBeforeRouteLeave(() => !dirty.value || confirm('当前解析校订尚未通过审阅，确定离开吗？'))
onBeforeUnmount(() => { active = false; clearInterval(pollTimer); window.removeEventListener('beforeunload', warnUnsaved) })
</script>

<template>
  <section class="parsed-document-page">
    <header class="parsed-header">
      <button @click="router.push(`/business/documents/${libraryId}`)">← 返回文档库</button>
      <div><h2>{{ source.original_filename || source.version?.original_filename || '文档解析结果' }}</h2><p>校订当前解析内容并通过审阅后，系统会自动运行全部绑定模板。</p></div>
      <span :class="['badge', parsed?.review_status === 'approved' ? 'green' : parseLabel() === '解析失败' ? 'red' : 'amber']">{{ parsed?.review_status === 'approved' ? '审阅已通过' : parseLabel() }}</span>
      <button v-if="parsed" class="primary" :disabled="reviewBusy" @click="approveParsedDocument">{{ reviewBusy ? '提交中…' : parsed.review_status === 'approved' ? '保存新校订并重新运行' : '通过审阅并运行' }}</button>
      <button v-if="parseLabel() === '解析失败'" class="primary" :disabled="busy" @click="retryParse">{{ busy ? '重试中…' : '重试解析' }}</button>
    </header>

    <p v-if="parseLabel() === '解析中' || parseLabel() === '待解析'" class="notice">ParseJob {{ parseJob?.attempt_no || 1 }} 正在处理；上传不会自动执行分块或知识流程。</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="error" class="error">{{ error }}</p>

    <section v-if="parsed" class="parsed-meta panel">
      <div><small>内容契约</small><b>{{ parsed.kind }} · {{ parsed.content_format }}</b></div>
      <div><small>Parser Revision</small><b>{{ parsed.parser_adapter }} · {{ parsed.parser_revision }}</b></div>
      <div><small>内容摘要</small><code>{{ parsed.content_digest }}</code></div>
      <div><small>Anchor 摘要</small><code>{{ parsed.anchor_map_digest }}</code></div>
    </section>

    <div v-if="parsed?.kind === 'textual'" class="textual-layout">
      <PdfSourcePreview v-if="isPdf" :url="api.sourcePreviewUrl(sourceId, versionId)" :anchor="selectedAnchor" />
      <section v-else class="source-info panel"><h3>原文件信息</h3><dl><div><dt>文件</dt><dd>{{ source.version?.original_filename }}</dd></div><div><dt>类型</dt><dd>{{ source.version?.media_type || '—' }}</dd></div><div><dt>大小</dt><dd>{{ source.version?.size_bytes || 0 }} bytes</dd></div></dl><p>DOCX、HTML、MD 与 TXT 的结构定位由右侧 Markdown range 与 paragraph/table Anchor 联动。</p></section>
      <section class="markdown-pane panel"><header><div><h3>规范化 Markdown</h3><p>可修正 OCR/解析结果；批准后会创建新的不可变修订。</p></div><span v-if="dirty" class="badge amber">未提交校订</span></header><textarea v-model="markdownDraft" aria-label="Markdown 校订内容" spellcheck="false"></textarea></section>
      <aside class="anchor-pane panel"><h3>SourceAnchor</h3><button v-for="(item, index) in anchorItems" :key="item.id || index" :class="{ active: selectedAnchor === sourceAnchor(item) }" @click="selectedAnchor=sourceAnchor(item)">{{ anchorName(item, index) }}</button><p v-if="!anchorItems.length">当前解析器未返回可交互 Anchor。</p></aside>
    </div>

    <section v-else-if="parsed?.kind === 'tabular'" class="table-layout panel">
      <header><div><h3>Table Grid</h3><p>保留原始 row/column index、空单元格和值类型；不会拼接成 Markdown。</p></div><div class="actions"><button v-for="index in content?.sheet_count || 0" :key="index" :class="{ primary: sheetIndex === index - 1 }" @click="selectSheet(index - 1)">Sheet {{ index }}</button></div></header>
      <div class="table-scroll"><table><thead><tr><th>row</th><th v-for="column in tableColumns" :key="column">c{{ column }}</th></tr></thead><tbody><tr v-for="row in tableRows" :key="row.row_index"><th>{{ row.row_index }}</th><td v-for="cell in row.cells || []" :key="cell.column_index" :class="{ empty: cell.value_type === 'empty' }" @click="selectedAnchor={ sheet_index: sheetIndex, sheet: content?.sheet?.name, row_index: row.row_index, column_index: cell.column_index }"><select v-model="cell.value_type" :aria-label="`行 ${row.row_index} 列 ${cell.column_index} 类型`"><option value="empty">empty</option><option value="string">string</option><option value="number">number</option><option value="boolean">boolean</option></select><select v-if="cell.value_type === 'boolean'" v-model="cell.value"><option :value="true">true</option><option :value="false">false</option></select><input v-else v-model="cell.value" :disabled="cell.value_type === 'empty'" :aria-label="`行 ${row.row_index} 列 ${cell.column_index} 值`"></td></tr></tbody></table></div>
      <footer><button :disabled="rowOffset === 0" @click="changePage(-1)">上一页</button><span>{{ rowOffset + 1 }}–{{ Math.min(rowOffset + 200, content?.total || 0) }} / {{ content?.total || 0 }}</span><button :disabled="rowOffset + 200 >= (content?.total || 0)" @click="changePage(1)">下一页</button></footer>
    </section>
  </section>
</template>

<style scoped>
  .parsed-document-page{display:grid;min-height:0;gap:14px}.parsed-header{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto auto;align-items:center;gap:12px}.parsed-header h2,.parsed-header p{margin:0}.parsed-header p{margin-top:4px;color:var(--muted)}.notice{padding:10px 12px;border:1px solid #bad0f5;border-radius:8px;background:var(--blue-soft)}.parsed-meta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.parsed-meta div{display:grid;gap:5px}.parsed-meta small{color:var(--muted)}.parsed-meta code{overflow:hidden;text-overflow:ellipsis}.textual-layout{display:grid;min-height:620px;grid-template-columns:minmax(300px,1fr) minmax(380px,1.15fr) 220px;gap:14px}.source-info dl div{display:grid;grid-template-columns:90px 1fr;padding:10px 0;border-bottom:1px solid var(--border)}.source-info dt{color:var(--muted)}.source-info dd{margin:0}.markdown-pane,.anchor-pane{min-height:0;overflow:auto}.markdown-pane textarea{box-sizing:border-box;width:100%;min-height:540px;padding:14px;border:1px solid var(--border);border-radius:8px;resize:vertical;background:#fbfcfe;font:13px/1.75 ui-monospace,SFMono-Regular,Consolas,monospace}.anchor-pane{display:flex;flex-direction:column;gap:7px}.anchor-pane button{text-align:left}.anchor-pane button.active{border-color:var(--blue);color:var(--blue);background:var(--blue-soft)}.table-layout header,.table-layout footer{display:flex;align-items:center;justify-content:space-between;gap:12px}.table-layout h3,.table-layout p{margin:0}.table-scroll{overflow:auto;margin:14px 0;max-height:650px}.table-scroll th{position:sticky;top:0;background:#f3f6fa}.table-scroll td,.table-scroll th{min-width:110px}.table-scroll td select,.table-scroll td input{box-sizing:border-box;width:100%;margin:2px 0}.table-scroll td.empty{background:#f7f8fa;color:#a5adba}.table-layout footer{justify-content:flex-end}@media(max-width:1100px){.textual-layout{grid-template-columns:1fr}.parsed-meta{grid-template-columns:1fr 1fr}}@media(max-width:720px){.parsed-header,.parsed-meta{grid-template-columns:1fr}.textual-layout{min-height:0}}
</style>
