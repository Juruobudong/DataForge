<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'

const route = useRoute(), router = useRouter()
let libraryId = route.params.libraryId, viewEpoch = 0, loadRequest = 0, active = true
const library = ref(null), tree = ref({ children: [] }), listing = ref({ items: [], total: 0 }), selectedPath = ref(null)
const keyword = ref(''), status = ref(''), fileType = ref(''), page = ref(1), selectedSources = ref([])
const queued = ref([]), preview = ref(null), results = ref([]), error = ref(''), notice = ref(''), dragging = ref(false), duplicatePolicy = ref('skip'), bindings = ref([]), templates = ref([]), templateIds = ref([]), bindingTemplates = ref(false)
const directoryInput = ref(null), fileInput = ref(null), replaceInput = ref(null), replaceTarget = ref(null)
const mutationBusy = ref(false), loading = ref(false)
const files = computed(() => listing.value.items || [])
const hasActiveBinding = computed(() => bindings.value.some(item => item.status === 'active'))
const hasResultCleanupInProgress = computed(() => bindings.value.some(binding =>
  binding.outputs?.some(output => output.state === 'deleting')))
const activeTemplateIds = computed(() => new Set(bindings.value.filter(item => item.status === 'active').map(item => item.template.id)))
const publishedTemplates = computed(() => templates.value.filter(item =>
  item.status === 'active' && (item.published_revision != null || item.revision_status === 'published')))
const availableTemplates = computed(() => {
  return publishedTemplates.value.filter(item => !activeTemplateIds.value.has(item.id))
})
const allTemplatesSelected = computed(() => availableTemplates.value.length > 0 &&
  availableTemplates.value.every(item => templateIds.value.includes(item.id)))
const someTemplatesSelected = computed(() => templateIds.value.length > 0 && !allTemplatesSelected.value)
const allPageSourcesSelected = computed(() => files.value.length > 0 && files.value.every(source => selectedSources.value.includes(source.id)))
const somePageSourcesSelected = computed(() => selectedSources.value.length > 0 && !allPageSourcesSelected.value)
const selectedSourceRows = computed(() => files.value.filter(source => selectedSources.value.includes(source.id)))
const reviewableSelected = computed(() => selectedSourceRows.value.filter(source => {
  const parsed = source.version?.parsed_document
  return source.version?.parse_status === 'completed' && parsed?.review_status === 'pending'
}))
const flatTree = computed(() => {
  const output = []
  const visit = (node, depth = 0) => (node.children || []).forEach(child => { output.push({ ...child, depth }); visit(child, depth + 1) })
  visit(tree.value); return output
})
const uploadStats = computed(() => queued.value.reduce((summary, item) => {
  const suffix = (item.file.name.split('.').pop() || '未知').toUpperCase(); summary.count += 1; summary.bytes += item.file.size; summary.types[suffix] = (summary.types[suffix] || 0) + 1; return summary
}, { count: 0, bytes: 0, types: {} }))
const queuedFiles = computed(() => {
  const unsupported = new Map((preview.value?.unsupported || []).map(item => [item.relative_path, item.error]))
  const oversized = new Set(preview.value?.oversized || []), duplicates = new Set(preview.value?.duplicates || [])
  return queued.value.map(item => ({
    ...item,
    issue: unsupported.get(item.relative_path) || (oversized.has(item.relative_path) ? '超过 200 MiB' : '') || (duplicates.has(item.relative_path) ? '同路径文件' : ''),
  }))
})

async function load(afterReview = false) {
  if (!active || (mutationBusy.value && afterReview !== true)) return
  const requestId = ++loadRequest, requestLibraryId = libraryId
  loading.value = true
  try {
    const [libraries, nextTree, nextList, nextBindings, nextTemplates] = await Promise.all([
      api.documentLibraries(),
      api.documentTree(requestLibraryId),
      api.librarySources(requestLibraryId, { path: selectedPath.value, keyword: keyword.value, status: status.value, file_type: fileType.value, page: page.value, page_size: 50 }), api.documentTemplateBindings(requestLibraryId), api.flowTemplates(),
    ])
    if (!active || requestId !== loadRequest || requestLibraryId !== libraryId) return
    library.value = libraries.find(item => item.id === requestLibraryId) || null
    tree.value = nextTree; listing.value = nextList; bindings.value = nextBindings; templates.value = nextTemplates; selectedSources.value = []
  } catch (e) { if (active && requestId === loadRequest && requestLibraryId === libraryId) error.value = e.message }
  finally { if (active && requestId === loadRequest) loading.value = false }
}

function normalizePath(file, folder = false) {
  const value = file.webkitRelativePath || file.name
  const parts = value.replaceAll('\\', '/').split('/').filter(Boolean)
  return folder && parts.length > 1 ? parts.slice(1).join('/') : parts.join('/')
}

async function queueFiles(fileList, folder = false) {
  if (mutationBusy.value) return
  queued.value = [...fileList].map(file => ({ file, relative_path: normalizePath(file, folder) }))
  results.value = []
  try { preview.value = await api.sourceImportPreflight(libraryId, queued.value.map(item => ({ relative_path: item.relative_path, size_bytes: item.file.size }))) } catch (e) { error.value = e.message }
}
function chooseFiles(event) { queueFiles(event.target.files); event.target.value = '' }
function chooseFolder(event) { queueFiles(event.target.files, true); event.target.value = '' }

async function readDropEntry(entry, prefix = '') {
  if (entry.isFile) return new Promise(resolve => entry.file(file => resolve([{ file, relative_path: `${prefix}${file.name}` }])))
  if (!entry.isDirectory) return []
  const reader = entry.createReader(), children = []
  const read = () => new Promise(resolve => reader.readEntries(resolve))
  let batch
  do { batch = await read(); children.push(...batch) } while (batch.length)
  return (await Promise.all(children.map(child => readDropEntry(child, `${prefix}${entry.name}/`)))).flat()
}
async function onDrop(event) {
  event.preventDefault(); dragging.value = false
  if (mutationBusy.value || loading.value) return
  const entries = [...event.dataTransfer.items || []].map(item => item.webkitGetAsEntry?.()).filter(Boolean)
  if (entries.length) {
    const dropped = (await Promise.all(entries.map(entry => readDropEntry(entry)))).flat()
    // A single dropped directory becomes the import root, just like webkitdirectory.
    const root = entries.length === 1 && entries[0].isDirectory ? `${entries[0].name}/` : ''
    queued.value = dropped.map(item => ({ ...item, relative_path: root && item.relative_path.startsWith(root) ? item.relative_path.slice(root.length) : item.relative_path }))
    try { preview.value = await api.sourceImportPreflight(libraryId, queued.value.map(item => ({ relative_path: item.relative_path, size_bytes: item.file.size }))) } catch (e) { error.value = e.message }
  } else await queueFiles(event.dataTransfer.files)
}

function importBatches() {
  const batches = []; let batch = [], bytes = 0
  for (const item of queued.value) {
    if (batch.length && bytes + item.file.size > 200 * 1024 * 1024) { batches.push(batch); batch = []; bytes = 0 }
    batch.push(item); bytes += item.file.size
  }
  if (batch.length) batches.push(batch)
  return batches
}
async function upload() {
  if (!queued.value.length) return
  try {
    const batches = importBatches(), policy = preview.value?.duplicates?.length ? duplicatePolicy.value : 'skip'
    const pool = [...batches], workers = Array.from({ length: Math.min(3, pool.length) }, async () => {
      while (pool.length) { const batch = pool.shift(); const form = new FormData(); batch.forEach(item => form.append('files', item.file)); form.append('manifest', JSON.stringify(batch.map(item => ({ relative_path: item.relative_path, size_bytes: item.file.size })))); form.append('duplicate_policy', policy); const response = await api.importSources(libraryId, form); results.value.push(...response.results) }
    })
    await Promise.all(workers)
    const confirmations = results.value.filter(item => item.status === 'confirmation_required' && item.source?.reactivation_version)
    if (confirmations.length && confirm(`${confirmations.length} 个文件内容命中历史版本。确认重新启用历史版本且不创建新版本吗？`)) {
      const activated = []
      for (const item of confirmations) {
        const result = await api.reactivateSourceVersion(
          item.source.id, item.source.reactivation_version.id, item.source.expected_current_version_id,
        )
        activated.push(result.notice)
        item.status = 'reactivated'
      }
      notice.value = activated.join('；')
    }
    queued.value = []; preview.value = null; await load()
  } catch (e) { error.value = e.message }
}

function openParsedDocument(source) {
  if (mutationBusy.value || loading.value) return
  const versionId = source.version?.id || source.current_version_id
  if (!versionId) return
  router.push(`/business/documents/${libraryId}/sources/${source.id}/versions/${versionId}/parsed`)
}
function parseStatus(source) {
  return source.version?.parse_status || 'pending'
}
function stageLabel(source) {
  return { pending: '待解析', queued: '待解析', running: '解析中', succeeded: '解析成功', completed: '解析成功', failed: '解析失败' }[parseStatus(source)] || '待解析'
}
function stageClass(source) {
  return parseStatus(source) === 'failed' ? 'red' : ['succeeded', 'completed'].includes(parseStatus(source)) ? 'green' : 'amber'
}
function reviewLabel(source) {
  const parsed = source.version?.parsed_document
  if (!parsed) return '等待解析'
  return { pending: '待审阅', approved: '已通过', superseded: '已更新' }[parsed.review_status] || '待审阅'
}
function reviewClass(source) {
  const status = source.version?.parsed_document?.review_status
  return status === 'approved' ? 'green' : status === 'superseded' ? 'red' : 'amber'
}
function chooseReplace(source) { replaceTarget.value = source; replaceInput.value?.click() }
async function replaceFile(event) {
  const file = event.target.files?.[0]; event.target.value = ''
  if (!file || !replaceTarget.value) return
  try {
    const target = replaceTarget.value, form = new FormData(); form.append('file', file)
    const result = await api.replaceSource(target.id, form)
    notice.value = result.version_action === 'unchanged' ? '文件内容与当前版本相同，未创建新版本。' : '已创建新的文件版本。'
    replaceTarget.value = null; await load()
  } catch (e) {
    const problem = e.problem
    if (e.status === 409 && problem?.code === 'SOURCE_VERSION_REACTIVATION_REQUIRED' &&
        confirm(`该内容已存在于 v${problem.reactivation_version.version_no}。确认重新启用该历史版本且不创建新版本吗？`)) {
      try {
        const result = await api.reactivateSourceVersion(
          replaceTarget.value.id, problem.reactivation_version.id, problem.expected_current_version_id,
        )
        notice.value = result.notice; replaceTarget.value = null; await load()
      } catch (reactivationError) { error.value = reactivationError.message }
    } else error.value = e.message
  }
}
async function hardDelete() {
  if (mutationBusy.value || !selectedSources.value.length) return
  try { const body = { source_ids: selectedSources.value }; const check = await api.documentDeletionPreflight(body); if (!check.deletable) throw new Error('存在运行任务，整批不能彻底删除。'); if (!confirm(`将彻底删除 ${check.source_count} 个文件，并影响 ${check.impact.knowledge_item_count} 个知识项、${check.impact.vector_record_count} 条向量。\n此操作不可撤销，确定继续吗？`)) return; await api.requestDocumentDeletion(body); selectedSources.value = []; await load() } catch (e) { error.value = e.message }
}
async function approveSelectedPage() {
  if (mutationBusy.value || !selectedSources.value.length) return
  const reviewable = reviewableSelected.value
  const approved = selectedSourceRows.value.filter(item => item.version?.parsed_document?.review_status === 'approved').length
  const unavailable = selectedSourceRows.value.length - reviewable.length - approved
  const estimatedJobs = reviewable.length * bindings.value.filter(item => item.status === 'active').length
  if (!confirm(`将审阅通过当前页所选文件。\n\n所选：${selectedSourceRows.value.length}\n可批准：${reviewable.length}\n已通过：${approved}\n解析未完成或不可用：${unavailable}\n预计触发模板任务：${estimatedJobs}\n\n需要修正 OCR 或表格内容的文件请先进入单文件页面校订。`)) return
  mutationBusy.value = true; error.value = ''; notice.value = ''
  try {
    const items = reviewable.map(source => ({
      source_id: source.id,
      source_version_id: source.version.id,
      parsed_document_id: source.version.parsed_document.id,
      expected_content_digest: source.version.parsed_document.content_digest,
      expected_anchor_map_digest: source.version.parsed_document.anchor_map_digest,
    }))
    const result = items.length ? await api.batchReviewParsedDocuments(libraryId, items) : {
      approved: [], already_approved: [], skipped: [], failed: [], dispatches: [],
    }
    notice.value = `审阅完成：批准 ${result.approved?.length || 0}，已通过 ${approved + (result.already_approved?.length || 0)}，跳过 ${unavailable + (result.skipped?.length || 0)}，失败 ${result.failed?.length || 0}，启动任务 ${(result.dispatches || []).filter(item => item.knowledge_job_id).length}。`
    selectedSources.value = []
    await load(true)
  } catch (e) { error.value = e.message } finally { mutationBusy.value = false }
}
function toggleAllPageSources(event) { if (!mutationBusy.value && !loading.value) selectedSources.value = event.target.checked ? files.value.map(source => source.id) : [] }
function toggleAllTemplates(event) { templateIds.value = event.target.checked ? availableTemplates.value.map(item => item.id) : [] }
async function bindTemplates() {
  if (!templateIds.value.length || bindingTemplates.value) return
  bindingTemplates.value = true; error.value = ''
  try { await api.bindDocumentTemplates(libraryId, templateIds.value); templateIds.value = []; await load() } catch (e) { error.value = e.message } finally { bindingTemplates.value = false }
}
async function unbindTemplate(binding) { try { if (!confirm(`解绑 ${binding.template.name}？结果知识库不会删除。`)) return; await api.unbindDocumentTemplate(libraryId, binding.template.id); await load() } catch (e) { error.value = e.message } }
function formatBytes(value) { return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MiB` : `${Math.ceil(value / 1024)} KiB` }
onMounted(load)
watch(() => route.params.libraryId, value => {
  libraryId = value; ++viewEpoch; ++loadRequest
  mutationBusy.value = false; notice.value = ''; error.value = ''
  library.value = null; listing.value = { items: [], total: 0 }; tree.value = { children: [] }
  selectedSources.value = []; selectedPath.value = null; page.value = 1; bindings.value = []; templateIds.value = []
  load()
})
onBeforeUnmount(() => { active = false; ++viewEpoch; ++loadRequest })
</script>

<template>
  <section>
    <fieldset class="document-controls" :disabled="mutationBusy || loading" :aria-busy="mutationBusy">
    <div class="page-head"><div><button @click="router.push('/business/documents')">← 文档库</button><h2>{{ library?.name || '文档库文件' }}</h2><p>上传只触发独立解析；校订并通过审阅后自动运行绑定模板。</p></div><div class="page-actions"><button @click="fileInput?.click()">上传文件</button><button class="primary" @click="directoryInput?.click()">上传文件夹</button></div></div>
    <input ref="fileInput" class="sr-only" type="file" multiple accept=".pdf,.csv,.xlsx,.md,.doc,.docx,.txt,.json,.jsonl" @change="chooseFiles">
    <input ref="directoryInput" class="sr-only" type="file" multiple webkitdirectory accept=".pdf,.csv,.xlsx,.md,.doc,.docx,.txt,.json,.jsonl" @change="chooseFolder">
    <input ref="replaceInput" class="sr-only" type="file" accept=".pdf,.csv,.xlsx,.md,.doc,.docx,.txt,.json,.jsonl" @change="replaceFile">
    <p v-if="notice" role="status" aria-live="polite" class="copy-notice">{{ notice }}</p>
    <div class="document-browser"><aside class="panel directory-tree"><b>目录</b><button :class="{ active: selectedPath === null }" @click="selectedPath=null; page=1; load()">▾ 全部文件</button><button v-for="node in flatTree" :key="node.path" :class="{ active: selectedPath === node.path }" :style="{ paddingLeft: `${12 + node.depth * 16}px` }" @click="selectedPath=node.path; page=1; load()">▸ {{ node.name }} <small>{{ node.file_count }}</small></button></aside>
      <div class="panel file-area" @dragover.prevent="dragging=true" @dragleave="dragging=false" @drop="onDrop">
        <div class="actions">
          <input v-model="keyword" placeholder="搜索文件" @keyup.enter="page=1; load()">
          <select v-model="status" aria-label="文件状态" @change="page=1; load()"><option value="">全部状态</option><option value="uploaded">已上传</option><option value="deleted">已删除</option></select>
          <select v-model="fileType" aria-label="文件格式" @change="page=1; load()"><option value="">全部格式</option><option value="pdf">PDF</option><option value="docx">DOCX</option><option value="xlsx">XLSX</option></select>
          <button @click="load">刷新 / 筛选</button><span class="badge amber">已选当前页 {{ selectedSources.length }} 个</span>
          <button class="primary batch-review-button" :disabled="!selectedSources.length" @click="approveSelectedPage">审阅通过所选文件（{{ reviewableSelected.length }}）</button>
          <button class="danger" :disabled="!selectedSources.length" @click="hardDelete">彻底删除</button>
        </div>
        <div v-if="dragging" class="drop-zone">拖拽文件或文件夹到此处</div><div v-else-if="!files.length" class="drop-zone">拖拽文件或文件夹到此处，或使用顶部上传入口</div>
        <table><thead><tr><th><input type="checkbox" :checked="allPageSourcesSelected" :indeterminate="somePageSourcesSelected" :disabled="!files.length" aria-label="全选当前页文件" @click.stop @change="toggleAllPageSources"> 全选当前页</th><th>名称</th><th>类型</th><th>解析状态</th><th>审阅状态</th><th>更新时间</th><th>操作</th></tr></thead>
          <tbody><tr v-for="source in files" :key="source.id" class="source-row" tabindex="0" @click="openParsedDocument(source)" @keydown.enter="openParsedDocument(source)"><td @click.stop><input v-model="selectedSources" type="checkbox" :value="source.id" :aria-label="`选择文件 ${source.original_filename}`"></td><td><b>{{ source.original_filename }}</b><small>{{ source.relative_path }}</small></td><td>{{ source.original_filename.split('.').pop()?.toUpperCase() }}</td><td><span class="badge" :class="stageClass(source)">{{ stageLabel(source) }}</span></td><td><span class="badge" :class="reviewClass(source)">{{ reviewLabel(source) }}</span></td><td>{{ source.updated_at }}</td><td><button class="primary" @click.stop="openParsedDocument(source)">{{ source.version?.parsed_document?.review_status === 'approved' ? '查看校订结果' : '查看并审阅' }}</button><button @click.stop="chooseReplace(source)">替换</button><a :href="api.sourceDownloadUrl(source.id, source.version?.id)" @click.stop>下载</a></td></tr></tbody>
        </table><p>共 {{ listing.total }} 个文件；全选仅作用于当前页。</p>
      </div>
    </div>
    <section class="panel">
      <div class="panel-head"><div><h3>已绑定的知识模板</h3><p>文档审阅通过后自动运行全部绑定模板；流程内部会自动冻结输入快照。</p></div><span class="badge blue">解析审阅后自动运行</span></div>
      <form class="actions template-binding-form" @submit.prevent="bindTemplates">
        <details class="template-multiselect">
          <summary>{{ templateIds.length ? `已选择 ${templateIds.length} 个模板` : '选择已发布模板' }}</summary>
          <div class="template-options">
            <label v-if="availableTemplates.length" class="template-select-all"><input type="checkbox" :checked="allTemplatesSelected" :indeterminate="someTemplatesSelected" aria-label="全选可绑定模板" @change="toggleAllTemplates"> <span>全选未绑定模板</span></label>
            <label v-for="item in publishedTemplates" :key="item.id" :class="{ bound: activeTemplateIds.has(item.id) }"><input v-model="templateIds" type="checkbox" :value="item.id" :disabled="activeTemplateIds.has(item.id)"> <span>{{ item.name }} · r{{ item.published_revision ?? item.revision }}<template v-if="activeTemplateIds.has(item.id)"> · 已绑定</template></span></label>
            <p v-if="!publishedTemplates.length">没有可绑定的已发布模板。</p>
          </div>
        </details>
        <button :disabled="!templateIds.length || bindingTemplates">{{ bindingTemplates ? '绑定中…' : '绑定所选模板' }}</button>
      </form>
        <table v-if="bindings.length"><thead><tr><th>模板 / 修订</th><th>结果知识库</th><th>待审阅 / 待调度</th><th>最近任务</th><th></th></tr></thead><tbody><tr v-for="binding in bindings" :key="binding.id"><td>{{ binding.template.name }} · r{{ binding.template.revision }}<small>{{ binding.status }}</small></td><td><span v-for="output in binding.outputs" :key="output.output_key"><template v-if="output.state === 'deleted'">{{ output.knowledge_type }}：已清理；下一次自动调度将新建结果库</template><template v-else>{{ output.knowledge_type }}：{{ output.knowledge_library?.name || '结果库不可用' }}<small v-if="output.state === 'deleting'">正在清理，完成前自动调度会阻塞</small></template><br></span></td><td>待审 {{ binding.review_counts?.pending || 0 }} · 待调度 {{ binding.pending_file_count }}<small v-if="binding.dispatch_counts?.blocked">阻塞 {{ binding.dispatch_counts.blocked }}</small><small v-if="binding.latest_dispatch_error" class="error">{{ binding.latest_dispatch_error.code }}：{{ binding.latest_dispatch_error.message }}</small></td><td>{{ binding.latest_job?.status || '—' }}</td><td><button v-if="binding.status==='active'" class="danger" @click="unbindTemplate(binding)">解绑</button></td></tr></tbody></table><p v-if="hasResultCleanupInProgress" class="error">有结果知识库正在清理，相关自动调度会在清理完成后重试。</p><p v-else-if="!bindings.length">尚未绑定知识模板；已批准文档会在绑定后自动运行。</p>
    </section>
    <section v-if="queued.length" class="panel"><div class="panel-head"><h3>上传预检</h3><button class="primary" @click="upload">开始上传</button></div><p>准备上传 {{ uploadStats.count }} 个文件，{{ formatBytes(uploadStats.bytes) }}</p><p>{{ Object.entries(uploadStats.types).map(([type, count]) => `${type} ${count}`).join(' · ') }}</p><ul class="queued-file-list" aria-label="待上传文件"><li v-for="item in queuedFiles" :key="item.relative_path"><div><b>{{ item.file.name }}</b><small>{{ item.relative_path }} · {{ formatBytes(item.file.size) }}</small></div><span class="badge" :class="item.issue ? (item.issue === '同路径文件' ? 'amber' : 'red') : 'blue'">{{ item.issue || '准备上传' }}</span></li></ul><label v-if="preview?.duplicates?.length">同路径文件 {{ preview.duplicates.join('、') }}：<select v-model="duplicatePolicy"><option value="skip">跳过</option><option value="replace">替换原文件</option><option value="keep_both">保留两份（自动重命名）</option></select></label><p v-if="preview?.unsupported?.length" class="error">不支持：{{ preview.unsupported.map(item => item.relative_path).join('、') }}</p><p v-if="preview?.oversized?.length" class="error">超过 200 MiB：{{ preview.oversized.join('、') }}</p></section>
    <details v-if="results.length" class="panel" open><summary>本次上传结果</summary><ul><li v-for="item in results" :key="`${item.relative_path}-${item.status}`">{{ item.relative_path }}：{{ item.status }}{{ item.error ? `（${item.error}）` : '' }}</li></ul></details>
    <p v-if="error" class="error">{{ error }}</p>
    </fieldset>
  </section>
</template>

<style scoped>
.document-controls { min-width: 0; margin: 0; padding: 0; border: 0; }
.template-binding-form { align-items: flex-start; }
.template-multiselect { position: relative; min-width: min(360px, 100%); }
.template-multiselect summary { min-height: 40px; padding: 9px 36px 9px 13px; border: 1px solid var(--border); border-radius: 8px; color: #405069; background: #fff; font-size: var(--font-assist); cursor: pointer; }
.template-options { position: absolute; z-index: 8; display: grid; width: 100%; max-height: 260px; gap: 4px; overflow: auto; margin-top: 4px; padding: 8px; border: 1px solid var(--border); border-radius: 10px; background: #fff; box-shadow: var(--shadow); }
.template-options label { display: flex; gap: 8px; align-items: center; min-height: 40px; padding: 8px; border-radius: 7px; color: #405069; font-size: var(--font-assist); cursor: pointer; }
.template-options label:hover { background: var(--blue-soft); }
.template-options label.bound { color: var(--muted); cursor: default; }
.template-options label.bound:hover { background: transparent; }
.template-options .template-select-all { border-bottom: 1px solid var(--border); border-radius: 0; font-weight: 600; }
.template-options p { margin: 8px; color: var(--muted); font-size: var(--font-assist); }
.source-row { cursor: pointer; }
.source-row:focus-visible { outline: 2px solid var(--blue); outline-offset: -2px; }
@media (max-width: 640px) { .template-multiselect { width: 100%; min-width: 0; } }
</style>
