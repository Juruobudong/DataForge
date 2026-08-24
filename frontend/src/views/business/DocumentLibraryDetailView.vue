<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'
import { canApproveDocument, documentProductionStage } from './documentReviewModel'

const route = useRoute(), router = useRouter(), libraryId = route.params.libraryId
const library = ref(null), tree = ref({ children: [] }), listing = ref({ items: [], total: 0 }), selectedPath = ref(null)
const keyword = ref(''), status = ref(''), fileType = ref(''), page = ref(1), selectedSources = ref([])
const queued = ref([]), preview = ref(null), results = ref([]), detail = ref(null), reviewDetail = ref(null), error = ref(''), dragging = ref(false), duplicatePolicy = ref('skip'), bindings = ref([]), templates = ref([]), templateIds = ref([]), bindingTemplates = ref(false)
const editingChunkId = ref(''), editContent = ref(''), selectedChunkIds = ref([]), reviewBusy = ref(false)
const directoryInput = ref(null), fileInput = ref(null), replaceInput = ref(null), replaceTarget = ref(null)
const files = computed(() => listing.value.items || [])
const hasActiveBinding = computed(() => bindings.value.some(item => item.status === 'active'))
const hasResultCleanupInProgress = computed(() => bindings.value.some(binding =>
  binding.outputs?.some(output => output.state === 'deleting')))
const availableTemplates = computed(() => {
  const activeIds = new Set(bindings.value.filter(item => item.status === 'active').map(item => item.template.id))
  return templates.value.filter(item => item.status === 'active' && item.revision_status === 'published' && !activeIds.has(item.id))
})
const allTemplatesSelected = computed(() => availableTemplates.value.length > 0 &&
  availableTemplates.value.every(item => templateIds.value.includes(item.id)))
const someTemplatesSelected = computed(() => templateIds.value.length > 0 && !allTemplatesSelected.value)
const allPageSourcesSelected = computed(() => files.value.length > 0 && files.value.every(source => selectedSources.value.includes(source.id)))
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

async function load() {
  try {
    const [libraries, nextTree, nextList, nextBindings, nextTemplates] = await Promise.all([
      api.documentLibraries(),
      api.documentTree(libraryId),
      api.librarySources(libraryId, { path: selectedPath.value, keyword: keyword.value, status: status.value, file_type: fileType.value, page: page.value, page_size: 50 }), api.documentTemplateBindings(libraryId), api.flowTemplates(),
    ])
    library.value = libraries.find(item => item.id === libraryId) || null
    tree.value = nextTree; listing.value = nextList; bindings.value = nextBindings; templates.value = nextTemplates; selectedSources.value = []
  } catch (e) { error.value = e.message }
}

function normalizePath(file, folder = false) {
  const value = file.webkitRelativePath || file.name
  const parts = value.replaceAll('\\', '/').split('/').filter(Boolean)
  return folder && parts.length > 1 ? parts.slice(1).join('/') : parts.join('/')
}

async function queueFiles(fileList, folder = false) {
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
    await Promise.all(workers); queued.value = []; preview.value = null; await load()
  } catch (e) { error.value = e.message }
}

async function openDetail(source) {
  try {
    detail.value = await api.sourceDetail(source.id)
    reviewDetail.value = detail.value?.source?.current_version_id ? await api.sourceReview(detail.value.source.current_version_id) : null
    selectedChunkIds.value = []; editingChunkId.value = ''
  } catch (e) { error.value = e.message }
}
async function reloadReview() {
  if (!detail.value?.source?.current_version_id) return
  reviewDetail.value = await api.sourceReview(detail.value.source.current_version_id)
  detail.value = await api.sourceDetail(detail.value.source.id)
}
function startEdit(chunk) { editingChunkId.value = chunk.id; editContent.value = chunk.content }
async function saveChunk(chunk) { try { reviewBusy.value = true; await api.updateSourceChunk(chunk.id, { content: editContent.value, expected_revision_no: chunk.revision_no }); editingChunkId.value = ''; await reloadReview() } catch (e) { error.value = e.message } finally { reviewBusy.value = false } }
async function reviewChunk(chunk, status) { try { reviewBusy.value = true; await api.reviewSourceChunk(chunk.id, { status, expected_revision_no: chunk.revision_no }); await reloadReview(); await load() } catch (e) { error.value = e.message } finally { reviewBusy.value = false } }
async function reopenChunk(chunk) { try { reviewBusy.value = true; await api.reopenSourceChunk(chunk.id); await reloadReview(); await load() } catch (e) { error.value = e.message } finally { reviewBusy.value = false } }
async function removeChunk(chunk) { if (!confirm('删除该文档块？该操作会保留审核审计记录。')) return; try { reviewBusy.value = true; await api.deleteSourceChunk(chunk.id, chunk.revision_no); await reloadReview(); await load() } catch (e) { error.value = e.message } finally { reviewBusy.value = false } }
async function splitChunk(chunk) {
  const raw = prompt('请用空行分隔拆分后的文档块：', chunk.content)
  if (raw === null) return
  const parts = raw.split(/\n\s*\n/).map(value => value.trim()).filter(Boolean)
  try { reviewBusy.value = true; await api.splitSourceChunk(chunk.id, { parts, expected_revision_no: chunk.revision_no }); await reloadReview(); await load() } catch (e) { error.value = e.message } finally { reviewBusy.value = false }
}
async function mergeChunks() {
  const chunks = (reviewDetail.value?.chunks || []).filter(item => selectedChunkIds.value.includes(item.id))
  try { reviewBusy.value = true; await api.mergeSourceChunks({ chunk_ids: chunks.map(item => item.id), expected_revisions: Object.fromEntries(chunks.map(item => [item.id, item.revision_no])) }); selectedChunkIds.value = []; await reloadReview(); await load() } catch (e) { error.value = e.message } finally { reviewBusy.value = false }
}
async function approveDocument() { try { reviewBusy.value = true; await api.approveSourceReview(detail.value.source.current_version_id); await reloadReview(); await load() } catch (e) { error.value = e.message } finally { reviewBusy.value = false } }
async function retryPreparation() { try { await api.retrySourcePreparation(detail.value.source.current_version_id); await reloadReview(); await load() } catch (e) { error.value = e.message } }
const stageLabel = documentProductionStage
function chooseReplace(source) { replaceTarget.value = source; replaceInput.value?.click() }
async function replaceFile(event) {
  const file = event.target.files?.[0]; event.target.value = ''
  if (!file || !replaceTarget.value) return
  try { const form = new FormData(); form.append('file', file); await api.replaceSource(replaceTarget.value.id, form); replaceTarget.value = null; await load() } catch (e) { error.value = e.message }
}
async function hardDelete() {
  if (!selectedSources.value.length) return
  try { const body = { source_ids: selectedSources.value }; const check = await api.documentDeletionPreflight(body); if (!check.deletable) throw new Error('存在运行任务，整批不能彻底删除。'); if (!confirm(`将彻底删除 ${check.source_count} 个文件，并影响 ${check.impact.knowledge_item_count} 个知识项、${check.impact.vector_record_count} 条向量。\n此操作不可撤销，确定继续吗？`)) return; await api.requestDocumentDeletion(body); selectedSources.value = []; await load() } catch (e) { error.value = e.message }
}
function toggleAllPageSources(event) { selectedSources.value = event.target.checked ? files.value.map(source => source.id) : [] }
function toggleAllTemplates(event) { templateIds.value = event.target.checked ? availableTemplates.value.map(item => item.id) : [] }
async function bindTemplates() {
  if (!templateIds.value.length || bindingTemplates.value) return
  bindingTemplates.value = true; error.value = ''
  try { await api.bindDocumentTemplates(libraryId, templateIds.value); templateIds.value = []; await load() } catch (e) { error.value = e.message } finally { bindingTemplates.value = false }
}
async function unbindTemplate(binding) { try { if (!confirm(`解绑 ${binding.template.name}？结果知识库不会删除。`)) return; await api.unbindDocumentTemplate(libraryId, binding.template.id); await load() } catch (e) { error.value = e.message } }
function formatBytes(value) { return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MiB` : `${Math.ceil(value / 1024)} KiB` }
onMounted(load)
</script>

<template>
  <section>
    <div class="page-head"><div><button @click="router.push('/business/documents')">← 文档库</button><h2>{{ library?.name || '文档库文件' }}</h2><p>上传后自动解析与分块；人工审核通过后，系统自动运行已绑定知识模板。</p></div><div class="page-actions"><button @click="fileInput?.click()">上传文件</button><button class="primary" @click="directoryInput?.click()">上传文件夹</button></div></div>
    <input ref="fileInput" class="sr-only" type="file" multiple accept=".pdf,.csv,.xlsx,.md,.doc,.docx,.txt" @change="chooseFiles">
    <input ref="directoryInput" class="sr-only" type="file" multiple webkitdirectory accept=".pdf,.csv,.xlsx,.md,.doc,.docx,.txt" @change="chooseFolder">
    <input ref="replaceInput" class="sr-only" type="file" accept=".pdf,.csv,.xlsx,.md,.doc,.docx,.txt" @change="replaceFile">
    <div class="document-browser"><aside class="panel directory-tree"><b>目录</b><button :class="{ active: selectedPath === null }" @click="selectedPath=null; page=1; load()">▾ 全部文件</button><button v-for="node in flatTree" :key="node.path" :class="{ active: selectedPath === node.path }" :style="{ paddingLeft: `${12 + node.depth * 16}px` }" @click="selectedPath=node.path; page=1; load()">▸ {{ node.name }} <small>{{ node.file_count }}</small></button></aside>
      <div class="panel file-area" @dragover.prevent="dragging=true" @dragleave="dragging=false" @drop="onDrop"><div class="actions"><input v-model="keyword" placeholder="搜索文件" @keyup.enter="page=1; load()"><select v-model="status" @change="page=1; load()"><option value="">全部状态</option><option value="uploaded">已上传</option><option value="deleted">已删除</option></select><select v-model="fileType" @change="page=1; load()"><option value="">全部格式</option><option value="pdf">PDF</option><option value="docx">DOCX</option><option value="xlsx">XLSX</option></select><button @click="load">刷新 / 筛选</button><span class="badge amber">已选当前页 {{ selectedSources.length }} 个</span><button class="danger" :disabled="!selectedSources.length" @click="hardDelete">彻底删除</button></div><div v-if="dragging" class="drop-zone">拖拽文件或文件夹到此处</div><div v-else-if="!files.length" class="drop-zone">拖拽文件或文件夹到此处，或使用顶部上传入口</div><table><thead><tr><th><input type="checkbox" :checked="allPageSourcesSelected" :disabled="!files.length" aria-label="全选当前页文件" @change="toggleAllPageSources"> 全选当前页</th><th>名称</th><th>类型</th><th>生产阶段</th><th>更新时间</th><th>操作</th></tr></thead><tbody><tr v-for="source in files" :key="source.id"><td><input v-model="selectedSources" type="checkbox" :value="source.id"></td><td><b>{{ source.original_filename }}</b><small>{{ source.relative_path }}</small></td><td>{{ source.original_filename.split('.').pop()?.toUpperCase() }}</td><td><span class="badge" :class="source.version?.review_status === 'approved' ? 'green' : source.version?.preparation_status === 'failed' || source.version?.review_status === 'rejected' ? 'red' : 'amber'">{{ stageLabel(source) }}</span></td><td>{{ source.updated_at }}</td><td><button class="primary" @click="openDetail(source)">{{ source.version?.review_status === 'approved' ? '查看审核' : '进入审核' }}</button><button @click="chooseReplace(source)">替换</button></td></tr></tbody></table><p>共 {{ listing.total }} 个文件；全选仅作用于当前页。</p></div></div>
    <section class="panel">
      <div class="panel-head"><div><h3>审核通过后自动运行的知识模板</h3><p>可提前绑定多个已发布模板；绑定只声明审核通过后要运行的知识流程。</p></div><span class="badge blue">服务端审核 Gate</span></div>
      <form class="actions template-binding-form" @submit.prevent="bindTemplates">
        <details class="template-multiselect">
          <summary>{{ templateIds.length ? `已选择 ${templateIds.length} 个模板` : '选择已发布模板' }}</summary>
          <div class="template-options">
            <label v-if="availableTemplates.length" class="template-select-all"><input type="checkbox" :checked="allTemplatesSelected" :indeterminate="someTemplatesSelected" aria-label="全选可绑定模板" @change="toggleAllTemplates"> <span>全选</span></label>
            <label v-for="item in availableTemplates" :key="item.id"><input v-model="templateIds" type="checkbox" :value="item.id"> <span>{{ item.name }} · r{{ item.revision }}</span></label>
            <p v-if="!availableTemplates.length">没有可绑定的已发布模板。</p>
          </div>
        </details>
        <button :disabled="!templateIds.length || bindingTemplates">{{ bindingTemplates ? '绑定中…' : '绑定所选模板' }}</button>
      </form>
      <table v-if="bindings.length"><thead><tr><th>模板 / 修订</th><th>结果知识库</th><th>待处理</th><th>最近任务</th><th></th></tr></thead><tbody><tr v-for="binding in bindings" :key="binding.id"><td>{{ binding.template.name }} · r{{ binding.template.revision }}<small>{{ binding.status }}</small></td><td><span v-for="output in binding.outputs" :key="output.output_key"><template v-if="output.state === 'deleted'">{{ output.knowledge_type }}：已清理；下一次处理将新建结果库并全量重跑此模板</template><template v-else>{{ output.knowledge_type }}：{{ output.knowledge_library?.name || '结果库不可用' }}<small v-if="output.state === 'deleting'">正在清理，完成前不能再次处理</small></template><br></span></td><td>{{ binding.pending_file_count }}</td><td>{{ binding.latest_job?.status || '—' }}</td><td><button v-if="binding.status==='active'" class="danger" @click="unbindTemplate(binding)">解绑</button></td></tr></tbody></table><p v-if="hasResultCleanupInProgress" class="error">有结果知识库正在清理。请在知识库详情中重试失败删除，清理完成后再重新处理文档。</p><p v-else-if="!bindings.length">尚未绑定知识模板。</p>
    </section>
    <section v-if="queued.length" class="panel"><div class="panel-head"><h3>上传预检</h3><button class="primary" @click="upload">开始上传</button></div><p>准备上传 {{ uploadStats.count }} 个文件，{{ formatBytes(uploadStats.bytes) }}</p><p>{{ Object.entries(uploadStats.types).map(([type, count]) => `${type} ${count}`).join(' · ') }}</p><ul class="queued-file-list" aria-label="待上传文件"><li v-for="item in queuedFiles" :key="item.relative_path"><div><b>{{ item.file.name }}</b><small>{{ item.relative_path }} · {{ formatBytes(item.file.size) }}</small></div><span class="badge" :class="item.issue ? (item.issue === '同路径文件' ? 'amber' : 'red') : 'blue'">{{ item.issue || '准备上传' }}</span></li></ul><label v-if="preview?.duplicates?.length">同路径文件 {{ preview.duplicates.join('、') }}：<select v-model="duplicatePolicy"><option value="skip">跳过</option><option value="replace">替换原文件</option><option value="keep_both">保留两份（自动重命名）</option></select></label><p v-if="preview?.unsupported?.length" class="error">不支持：{{ preview.unsupported.map(item => item.relative_path).join('、') }}</p><p v-if="preview?.oversized?.length" class="error">超过 200 MiB：{{ preview.oversized.join('、') }}</p></section>
    <details v-if="results.length" class="panel" open><summary>本次上传结果</summary><ul><li v-for="item in results" :key="`${item.relative_path}-${item.status}`">{{ item.relative_path }}：{{ item.status }}{{ item.error ? `（${item.error}）` : '' }}</li></ul></details>
    <section v-if="detail" class="panel source-detail review-panel">
      <div class="panel-head"><div><h3>{{ detail.source.original_filename }}</h3><p>原目录：{{ detail.source.directory_path || '根目录' }} · {{ formatBytes(detail.source.version?.size_bytes || 0) }}</p></div><button @click="detail=null; reviewDetail=null">关闭</button></div>
      <div class="review-summary">
        <span class="badge" :class="reviewDetail?.review_status === 'approved' ? 'green' : reviewDetail?.review_status === 'rejected' ? 'red' : 'amber'">{{ reviewDetail?.review_status || '加载中' }}</span>
        <span>共 {{ reviewDetail?.counts?.total || 0 }} 块 · 待审核 {{ reviewDetail?.counts?.pending_review || 0 }} · 已通过 {{ reviewDetail?.counts?.approved || 0 }} · 已拒绝 {{ reviewDetail?.counts?.rejected || 0 }}</span>
        <button v-if="reviewDetail?.preparation_status === 'failed'" class="primary" @click="retryPreparation">重试解析与分块</button>
        <button :disabled="reviewBusy || !canApproveDocument(reviewDetail)" class="primary" @click="approveDocument">审核通过并自动生成知识</button>
      </div>
      <p v-if="reviewDetail?.counts?.pending_review" class="review-notice">当前文档存在 {{ reviewDetail.counts.pending_review }} 个待审核文档块，请完成审核后再运行知识流程。</p>
      <div class="review-workspace">
        <section class="original-pane">
          <div class="pane-title"><h4>原文</h4><a :href="api.sourceDownloadUrl(detail.source.id, detail.source.current_version_id)">下载原始文件</a></div>
          <iframe v-if="detail.source.original_filename.toLowerCase().endsWith('.pdf')" :src="api.sourceDownloadUrl(detail.source.id, detail.source.current_version_id)" title="PDF 原文预览"></iframe>
          <pre v-else>{{ detail.document_ir?.text || (reviewDetail?.preparation_status === 'failed' ? '解析失败，请重试。' : '正在解析原文…') }}</pre>
        </section>
        <section class="chunk-pane">
          <div class="pane-title"><h4>SourceChunk 人工审核</h4><button :disabled="selectedChunkIds.length < 2 || reviewBusy" @click="mergeChunks">合并已选连续块</button></div>
          <article v-for="chunk in reviewDetail?.chunks || []" :key="chunk.id" class="review-chunk" :class="chunk.review_status">
            <header><label><input v-model="selectedChunkIds" type="checkbox" :value="chunk.id"> #{{ chunk.chunk_index + 1 }}</label><span class="badge" :class="chunk.review_status === 'approved' ? 'green' : chunk.review_status === 'rejected' ? 'red' : 'amber'">{{ chunk.review_status }}</span></header>
            <textarea v-if="editingChunkId === chunk.id" v-model="editContent" rows="8" :aria-label="`编辑第 ${chunk.chunk_index + 1} 个文档块`"></textarea>
            <p v-else>{{ chunk.content }}</p>
            <footer v-if="editingChunkId === chunk.id"><button :disabled="reviewBusy" class="primary" @click="saveChunk(chunk)">保存修改</button><button @click="editingChunkId=''">取消</button></footer>
            <footer v-else>
              <template v-if="chunk.review_status === 'approved'"><button @click="reopenChunk(chunk)">重开审核</button></template>
              <template v-else><button @click="startEdit(chunk)">修改</button><button @click="splitChunk(chunk)">拆分</button><button class="danger" @click="removeChunk(chunk)">删除</button><button @click="reviewChunk(chunk, 'rejected')">拒绝</button><button class="primary" @click="reviewChunk(chunk, 'approved')">通过</button></template>
            </footer>
          </article>
          <p v-if="reviewDetail?.preparation_status !== 'completed' && !reviewDetail?.chunks?.length" class="empty-review">{{ reviewDetail?.preparation_status === 'failed' ? '解析失败，尚无可审核文档块。' : '解析、清洗和分块进行中…' }}</p>
        </section>
      </div>
      <details><summary>生成知识（{{ detail.knowledge_results.length }}）</summary><ul><li v-for="item in detail.knowledge_results" :key="item.knowledge_item_id">{{ item.knowledge_library_name }}：{{ item.content }}</li></ul></details>
      <details><summary>处理任务与日志</summary><pre>{{ JSON.stringify(detail.jobs, null, 2) }}</pre></details>
    </section>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.template-binding-form { align-items: flex-start; }
.template-multiselect { position: relative; min-width: min(360px, 100%); }
.template-multiselect summary { min-height: 40px; padding: 9px 36px 9px 13px; border: 1px solid var(--border); border-radius: 8px; color: #405069; background: #fff; font-size: var(--font-assist); cursor: pointer; }
.template-options { position: absolute; z-index: 8; display: grid; width: 100%; max-height: 260px; gap: 4px; overflow: auto; margin-top: 4px; padding: 8px; border: 1px solid var(--border); border-radius: 10px; background: #fff; box-shadow: var(--shadow); }
.template-options label { display: flex; gap: 8px; align-items: center; min-height: 40px; padding: 8px; border-radius: 7px; color: #405069; font-size: var(--font-assist); cursor: pointer; }
.template-options label:hover { background: var(--blue-soft); }
.template-options .template-select-all { border-bottom: 1px solid var(--border); border-radius: 0; font-weight: 600; }
.template-options p { margin: 8px; color: var(--muted); font-size: var(--font-assist); }
.review-panel { margin-top: 18px; }
.review-summary { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 12px 0; }
.review-notice { padding: 10px 12px; border: 1px solid #f2d18a; border-radius: 8px; color: #8a6112; background: #fff8e8; }
.review-workspace { display: grid; grid-template-columns: minmax(0, 1fr) minmax(440px, 1fr); gap: 16px; min-height: 620px; margin: 14px 0; }
.original-pane,.chunk-pane { min-width: 0; overflow: auto; border: 1px solid var(--border); border-radius: 12px; background: #f7f9fc; }
.pane-title { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; border-bottom: 1px solid var(--border); background: #fff; }
.pane-title h4 { margin: 0; }
.original-pane iframe { width: 100%; min-height: 720px; border: 0; background: #fff; }
.original-pane pre { min-height: 600px; margin: 0; padding: 18px; white-space: pre-wrap; line-height: 1.75; background: #fff; }
.chunk-pane { padding-bottom: 12px; }
.review-chunk { margin: 12px; padding: 12px; border: 1px solid #dce3ee; border-radius: 10px; background: #fff; }
.review-chunk.approved { border-color: #9fd5bf; }
.review-chunk.rejected { border-color: #e6aaaa; }
.review-chunk header,.review-chunk footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.review-chunk footer { justify-content: flex-end; flex-wrap: wrap; margin-top: 10px; }
.review-chunk p { white-space: pre-wrap; line-height: 1.7; }
.review-chunk textarea { width: 100%; margin-top: 10px; resize: vertical; line-height: 1.6; }
.empty-review { padding: 40px; color: var(--muted); text-align: center; }
@media (max-width: 640px) { .template-multiselect { width: 100%; min-width: 0; } }
</style>
