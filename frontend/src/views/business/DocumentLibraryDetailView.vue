<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'

const route = useRoute(), router = useRouter(), libraryId = route.params.libraryId
const library = ref(null), tree = ref({ children: [] }), listing = ref({ items: [], total: 0 }), selectedPath = ref(null)
const keyword = ref(''), status = ref(''), fileType = ref(''), page = ref(1), selectedSources = ref([])
const queued = ref([]), preview = ref(null), results = ref([]), detail = ref(null), error = ref(''), dragging = ref(false), duplicatePolicy = ref('skip'), bindings = ref([]), templates = ref([]), templateId = ref('')
const directoryInput = ref(null), fileInput = ref(null), replaceInput = ref(null), replaceTarget = ref(null)
const files = computed(() => listing.value.items || [])
const hasActiveBinding = computed(() => bindings.value.some(item => item.status === 'active'))
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

async function openDetail(source) { try { detail.value = await api.sourceDetail(source.id) } catch (e) { error.value = e.message } }
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
async function bindTemplate() { try { await api.bindDocumentTemplate(libraryId, templateId.value); templateId.value = ''; await load() } catch (e) { error.value = e.message } }
async function unbindTemplate(binding) { try { if (!confirm(`解绑 ${binding.template.name}？结果知识库不会删除。`)) return; await api.unbindDocumentTemplate(libraryId, binding.template.id); await load() } catch (e) { error.value = e.message } }
async function processLibrary() { try { const result = await api.processDocumentLibrary(libraryId); results.value = result.jobs; await load() } catch (e) { error.value = e.message } }
async function processSelectedSources() { try { const result = await api.processSelectedDocumentSources(libraryId, selectedSources.value); results.value = result.jobs; await load() } catch (e) { error.value = e.message } }
function formatBytes(value) { return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MiB` : `${Math.ceil(value / 1024)} KiB` }
onMounted(load)
</script>

<template>
  <section>
    <div class="page-head"><div><button @click="router.push('/business/documents')">← 文档库</button><h2>{{ library?.name || '文档库文件' }}</h2><p>模板绑定驱动自动结果库与批量处理；可全选当前页文件后定向处理。</p></div><div class="page-actions"><button @click="fileInput?.click()">上传文件</button><button class="primary" @click="directoryInput?.click()">上传文件夹</button></div></div>
    <input ref="fileInput" class="sr-only" type="file" multiple accept=".pdf,.csv,.xlsx,.md,.doc,.docx,.txt" @change="chooseFiles">
    <input ref="directoryInput" class="sr-only" type="file" multiple webkitdirectory accept=".pdf,.csv,.xlsx,.md,.doc,.docx,.txt" @change="chooseFolder">
    <input ref="replaceInput" class="sr-only" type="file" accept=".pdf,.csv,.xlsx,.md,.doc,.docx,.txt" @change="replaceFile">
    <div class="document-browser"><aside class="panel directory-tree"><b>目录</b><button :class="{ active: selectedPath === null }" @click="selectedPath=null; page=1; load()">▾ 全部文件</button><button v-for="node in flatTree" :key="node.path" :class="{ active: selectedPath === node.path }" :style="{ paddingLeft: `${12 + node.depth * 16}px` }" @click="selectedPath=node.path; page=1; load()">▸ {{ node.name }} <small>{{ node.file_count }}</small></button></aside>
      <div class="panel file-area" @dragover.prevent="dragging=true" @dragleave="dragging=false" @drop="onDrop"><div class="actions"><input v-model="keyword" placeholder="搜索文件" @keyup.enter="page=1; load()"><select v-model="status" @change="page=1; load()"><option value="">全部状态</option><option value="uploaded">已上传</option><option value="deleted">已删除</option></select><select v-model="fileType" @change="page=1; load()"><option value="">全部格式</option><option value="pdf">PDF</option><option value="docx">DOCX</option><option value="xlsx">XLSX</option></select><button @click="load">筛选</button><span class="badge amber">已选当前页 {{ selectedSources.length }} 个</span><button class="primary" :disabled="!selectedSources.length || !hasActiveBinding" @click="processSelectedSources">处理选中文件</button><button class="danger" :disabled="!selectedSources.length" @click="hardDelete">彻底删除</button></div><div v-if="dragging" class="drop-zone">拖拽文件或文件夹到此处</div><div v-else-if="!files.length" class="drop-zone">拖拽文件或文件夹到此处，或使用顶部上传入口</div><table><thead><tr><th><input type="checkbox" :checked="allPageSourcesSelected" :disabled="!files.length" aria-label="全选当前页文件" @change="toggleAllPageSources"> 全选当前页</th><th>名称</th><th>类型</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody><tr v-for="source in files" :key="source.id"><td><input v-model="selectedSources" type="checkbox" :value="source.id"></td><td><b>{{ source.original_filename }}</b><small>{{ source.relative_path }}</small></td><td>{{ source.original_filename.split('.').pop()?.toUpperCase() }}</td><td><span class="badge" :class="source.status === 'deleted' ? 'red' : 'green'">{{ source.status }}</span></td><td>{{ source.updated_at }}</td><td><button @click="openDetail(source)">详情</button><button @click="chooseReplace(source)">替换</button></td></tr></tbody></table><p>共 {{ listing.total }} 个文件；全选仅作用于当前页。</p></div></div>
    <section class="panel"><div class="panel-head"><div><h3>已配置知识模板</h3><p>首次绑定自动创建结果知识库；后续批量处理仅处理新增或替换版本。</p></div><button class="primary" :disabled="!hasActiveBinding" @click="processLibrary">批量处理待更新文件（本次上传）</button></div><form class="actions" @submit.prevent="bindTemplate"><select v-model="templateId" required><option value="">选择已发布模板</option><option v-for="item in templates.filter(item=>item.status==='active' && item.revision_status==='published')" :key="item.id" :value="item.id">{{ item.name }} · r{{ item.revision }}</option></select><button>绑定模板</button></form><table v-if="bindings.length"><thead><tr><th>模板 / 修订</th><th>结果知识库</th><th>待处理</th><th>最近任务</th><th></th></tr></thead><tbody><tr v-for="binding in bindings" :key="binding.id"><td>{{ binding.template.name }} · r{{ binding.template.revision }}<small>{{ binding.status }}</small></td><td><span v-for="output in binding.outputs" :key="output.knowledge_type">{{ output.knowledge_type }}：{{ output.knowledge_library.name }}<br></span></td><td>{{ binding.pending_file_count }}</td><td>{{ binding.latest_job?.status || '—' }}</td><td><button v-if="binding.status==='active'" class="danger" @click="unbindTemplate(binding)">解绑</button></td></tr></tbody></table><p v-else>尚未绑定知识模板。</p></section>
    <section v-if="queued.length" class="panel"><div class="panel-head"><h3>上传预检</h3><button class="primary" @click="upload">开始上传</button></div><p>准备上传 {{ uploadStats.count }} 个文件，{{ formatBytes(uploadStats.bytes) }}</p><p>{{ Object.entries(uploadStats.types).map(([type, count]) => `${type} ${count}`).join(' · ') }}</p><ul class="queued-file-list" aria-label="待上传文件"><li v-for="item in queuedFiles" :key="item.relative_path"><div><b>{{ item.file.name }}</b><small>{{ item.relative_path }} · {{ formatBytes(item.file.size) }}</small></div><span class="badge" :class="item.issue ? (item.issue === '同路径文件' ? 'amber' : 'red') : 'blue'">{{ item.issue || '准备上传' }}</span></li></ul><label v-if="preview?.duplicates?.length">同路径文件 {{ preview.duplicates.join('、') }}：<select v-model="duplicatePolicy"><option value="skip">跳过</option><option value="replace">替换原文件</option><option value="keep_both">保留两份（自动重命名）</option></select></label><p v-if="preview?.unsupported?.length" class="error">不支持：{{ preview.unsupported.map(item => item.relative_path).join('、') }}</p><p v-if="preview?.oversized?.length" class="error">超过 200 MiB：{{ preview.oversized.join('、') }}</p></section>
    <details v-if="results.length" class="panel" open><summary>本次上传结果</summary><ul><li v-for="item in results" :key="`${item.relative_path}-${item.status}`">{{ item.relative_path }}：{{ item.status }}{{ item.error ? `（${item.error}）` : '' }}</li></ul></details>
    <section v-if="detail" class="panel source-detail"><div class="panel-head"><h3>{{ detail.source.original_filename }}</h3><button @click="detail=null">关闭</button></div><p>原目录：{{ detail.source.directory_path || '根目录' }} · {{ formatBytes(detail.source.version?.size_bytes || 0) }}</p><p><a :href="api.sourceDownloadUrl(detail.source.id, detail.source.current_version_id)">下载原始文件</a></p><details open><summary>解析结果</summary><pre>{{ detail.document_ir?.text || '尚未生成解析结果' }}</pre></details><details><summary>SourceChunk（{{ detail.source_chunks.length }}）</summary><ol><li v-for="chunk in detail.source_chunks" :key="chunk.id">{{ chunk.content }}</li></ol></details><details><summary>生成知识（{{ detail.knowledge_results.length }}）</summary><ul><li v-for="item in detail.knowledge_results" :key="item.knowledge_item_id">{{ item.knowledge_library_name }}：{{ item.content }}</li></ul></details><details><summary>处理任务与日志</summary><pre>{{ JSON.stringify(detail.jobs, null, 2) }}</pre></details></section>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>
