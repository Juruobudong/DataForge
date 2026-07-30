<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from './api'

const workspace = ref('business')
const activePage = ref('overview')
const loading = ref(true)
const busy = ref(false)
const toast = ref(null)
const lastUpdated = ref('')
const routeDepth = ref(0)
const returnLabel = ref('')

const dashboard = ref({ counts: {}, knowledge_counts: {} })
const sources = ref([])
const knowledgeTypes = ref([])
const standardPipelines = ref([])
const knowledgeJobs = ref([])
const knowledgeBases = ref([])
const dataflowPipelines = ref([])
const dataflowTasks = ref([])
const studioStatus = ref({})

const selectedSourceId = ref('')
const showUpload = ref(false)
const uploadFile = ref(null)
const uploadName = ref('')
const uploadVersionSourceId = ref('')

const jobMode = ref('list')
const wizardStep = ref(1)
const selectedVersionIds = ref([])
const selectedJobId = ref('')
const jobForm = reactive({ name: '', knowledge_type_id: '' })

const selectedBaseId = ref('')
const baseDetail = ref(null)
const recordQuery = ref('')
const recordPage = ref(1)
const lineage = ref(null)

const showTypeForm = ref(false)
const typeForm = reactive({
  name: '',
  description: '',
  fields: [{ name: 'content', type: 'string', required: true }]
})

const publishForm = reactive({
  name: '',
  description: '',
  dataflow_pipeline_id: '',
  sample_task_id: '',
  knowledge_type_id: '',
  version: 1,
  make_default: true
})

const businessNav = [
  { id: 'overview', label: '工作台', note: '今天要做的事' },
  { id: 'sources', label: '文档管理', note: '上传和管理源文件' },
  { id: 'jobs', label: '处理任务', note: '把文档生成知识' },
  { id: 'knowledge', label: '知识库', note: '查看结果与来源' }
]
const developerNav = [
  { id: 'types', label: '知识类型', note: '定义输出数据格式' },
  { id: 'standard', label: '标准流程', note: '验证并发布处理能力' },
  { id: 'studio', label: 'DataFlow 调试台', note: '搭建和调试流程' }
]

const pages = {
  overview: ['工作台', '查看系统现状和下一步建议'],
  sources: ['文档管理', '上传、更新并管理待处理的源文档'],
  jobs: ['处理任务', '选择文档和生成内容，系统自动完成处理'],
  knowledge: ['知识库', '查看已入库的知识以及处理前后对照'],
  types: ['知识类型', '配置业务可选择的输出数据格式'],
  standard: ['标准流程', '把调试成功的 DataFlow 流程验证并发布'],
  studio: ['DataFlow 调试台', '面向技术人员的流程搭建和样本调试空间']
}

const activeNav = computed(() => workspace.value === 'business' ? businessNav : developerNav)
const pageTitle = computed(() => pages[activePage.value]?.[0] || '')
const pageDescription = computed(() => pages[activePage.value]?.[1] || '')
const readyKnowledgeTypes = computed(() => knowledgeTypes.value.filter(type =>
  standardPipelines.value.some(pipe =>
    pipe.knowledge_type_id === type.id && pipe.active && pipe.validation_status === 'validated'
  )
))
const latestVersions = computed(() => sources.value
  .filter(source => source.latest_version)
  .map(source => ({ ...source.latest_version, source_name: source.name, source_kind: source.kind })))
const selectedSource = computed(() => sources.value.find(item => item.id === selectedSourceId.value))
const selectedJob = computed(() => knowledgeJobs.value.find(item => item.id === selectedJobId.value))
const selectedBase = computed(() => knowledgeBases.value.find(item => item.id === selectedBaseId.value))
const selectedType = computed(() => knowledgeTypes.value.find(item => item.id === jobForm.knowledge_type_id))
const selectedPipeline = computed(() => dataflowPipelines.value.find(item => item.id === publishForm.dataflow_pipeline_id))
const compatibleTasks = computed(() => dataflowTasks.value.filter(task =>
  task.pipeline_id === publishForm.dataflow_pipeline_id && task.status === 'completed'
))
const runningJobs = computed(() => knowledgeJobs.value.filter(job => ['pending', 'running'].includes(job.status)))
const totalRecords = computed(() => knowledgeBases.value.reduce((total, base) => total + (base.record_count || 0), 0))
const actionLabel = computed(() => ({
  overview: '新建处理任务',
  sources: '上传文档',
  jobs: jobMode.value === 'create' ? '' : '新建处理任务',
  types: '新建知识类型',
  standard: '进入调试台'
}[activePage.value] || ''))
const canGoBack = computed(() => routeDepth.value > 0)

function notify(message, error = false) {
  toast.value = { message, error }
  window.setTimeout(() => { toast.value = null }, 3200)
}

function formatTime(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
  }).format(new Date(value))
}

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function statusText(status) {
  return {
    pending: '等待处理', running: '处理中', completed: '已完成', failed: '处理失败',
    validated: '已发布', configured: '待验证', available: '可使用'
  }[status] || status || '—'
}

function kindText(kind, filename = '') {
  const extension = filename.split('.').pop()?.toLowerCase()
  return ({ pdf: 'PDF', csv: 'CSV', md: 'Markdown', doc: 'Word', docx: 'Word', txt: '文本' })[extension]
    || ({ file: '文件', document: '文档' })[kind] || kind || '文件'
}

function recordSummary(record) {
  const data = record?.data || {}
  return data.question || data.content || data.subject || data.messages?.[0]?.content || JSON.stringify(data)
}

function recordContent(value) {
  if (value == null) return '—'
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function fieldTypeText(type) {
  return ({ string: '文本', integer: '整数', array: '列表', object: '对象' })[type] || type
}

async function refreshAll(silent = false) {
  if (!silent) loading.value = true
  try {
    const [dash, sourceList, types, standards, jobs, bases, studio, pipelines, tasks] = await Promise.all([
      api.dashboard(), api.sources(), api.knowledgeTypes(), api.standardPipelines(),
      api.knowledgeJobs(), api.knowledgeBases(), api.studioStatus(),
      api.dataflowPipelines(), api.dataflowTasks()
    ])
    dashboard.value = dash
    sources.value = sourceList
    knowledgeTypes.value = types
    standardPipelines.value = standards
    knowledgeJobs.value = jobs
    knowledgeBases.value = bases
    studioStatus.value = studio
    dataflowPipelines.value = pipelines
    dataflowTasks.value = tasks
    if (!selectedSourceId.value && sourceList.length) selectedSourceId.value = sourceList[0].id
    if (!selectedBaseId.value && bases.length) selectedBaseId.value = bases[0].id
    lastUpdated.value = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date())
  } catch (error) {
    notify(error.message, true)
  } finally {
    loading.value = false
  }
}

function currentRoute(overrides = {}) {
  return {
    dataforge: true,
    workspace: workspace.value,
    activePage: activePage.value,
    jobMode: jobMode.value,
    wizardStep: wizardStep.value,
    showUpload: showUpload.value,
    showTypeForm: showTypeForm.value,
    selectedSourceId: selectedSourceId.value,
    selectedJobId: selectedJobId.value,
    selectedBaseId: selectedBaseId.value,
    routeDepth: routeDepth.value,
    returnLabel: returnLabel.value,
    ...overrides
  }
}

function routeUrl(route) {
  const view = route.jobMode === 'create' ? '/create' : route.showUpload ? '/upload' : route.showTypeForm ? '/new' : ''
  return `#/${route.workspace}/${route.activePage}${view}`
}

function routeFromHash() {
  const [workspaceName, pageName, viewName] = window.location.hash.replace(/^#\//, '').split('/')
  const validWorkspace = ['business', 'developer'].includes(workspaceName) ? workspaceName : 'business'
  const allowedPages = validWorkspace === 'business'
    ? businessNav.map(item => item.id)
    : developerNav.map(item => item.id)
  const validPage = allowedPages.includes(pageName)
    ? pageName
    : validWorkspace === 'business' ? 'overview' : 'types'
  return currentRoute({
    workspace: validWorkspace,
    activePage: validPage,
    jobMode: validPage === 'jobs' && viewName === 'create' ? 'create' : 'list',
    showUpload: validPage === 'sources' && viewName === 'upload',
    showTypeForm: validPage === 'types' && viewName === 'new',
    routeDepth: 0,
    returnLabel: ''
  })
}

function applyRoute(route) {
  workspace.value = route.workspace || 'business'
  activePage.value = route.activePage || (workspace.value === 'business' ? 'overview' : 'types')
  jobMode.value = route.jobMode || 'list'
  wizardStep.value = route.wizardStep || 1
  showUpload.value = Boolean(route.showUpload)
  showTypeForm.value = Boolean(route.showTypeForm)
  selectedSourceId.value = route.selectedSourceId || selectedSourceId.value
  selectedJobId.value = route.selectedJobId || ''
  selectedBaseId.value = route.selectedBaseId || selectedBaseId.value
  routeDepth.value = Number(route.routeDepth || 0)
  returnLabel.value = route.returnLabel || ''
  if (activePage.value !== 'knowledge') lineage.value = null
}

function pushRoute(overrides) {
  const previousLabel = jobMode.value === 'create' ? '新建处理任务' : pageTitle.value
  const route = currentRoute({
    showUpload: false,
    showTypeForm: false,
    jobMode: 'list',
    ...overrides,
    routeDepth: routeDepth.value + 1,
    returnLabel: previousLabel
  })
  window.history.pushState(route, '', routeUrl(route))
  applyRoute(route)
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function replaceRoute(overrides = {}) {
  const route = currentRoute(overrides)
  window.history.replaceState(route, '', routeUrl(route))
  applyRoute(route)
}

function goBack(fallback = {}) {
  if (canGoBack.value) {
    window.history.back()
    return
  }
  replaceRoute({
    workspace: fallback.workspace || workspace.value,
    activePage: fallback.activePage || (workspace.value === 'business' ? 'overview' : 'types'),
    jobMode: 'list',
    showUpload: false,
    showTypeForm: false,
    routeDepth: 0,
    returnLabel: ''
  })
}

function switchWorkspace(next) {
  if (workspace.value === next) return
  pushRoute({
    workspace: next,
    activePage: next === 'business' ? 'overview' : 'types'
  })
}

function navigate(page) {
  if (activePage.value === page && jobMode.value === 'list' && !showUpload.value && !showTypeForm.value) return
  pushRoute({
    workspace: ['overview', 'sources', 'jobs', 'knowledge'].includes(page) ? 'business' : 'developer',
    activePage: page
  })
}

function openUpload() {
  pushRoute({ workspace: 'business', activePage: 'sources', showUpload: true })
}

function openTypeForm() {
  pushRoute({ workspace: 'developer', activePage: 'types', showTypeForm: true })
}

function closeCurrentPanel() {
  goBack({ activePage: activePage.value })
}

function handlePrimaryAction() {
  if (activePage.value === 'overview' || activePage.value === 'jobs') return openTaskWizard()
  if (activePage.value === 'sources') return openUpload()
  if (activePage.value === 'types') return openTypeForm()
  if (activePage.value === 'standard') navigate('studio')
}

function openTaskWizard(versionId = '') {
  selectedVersionIds.value = versionId ? [versionId] : []
  jobForm.name = ''
  jobForm.knowledge_type_id = readyKnowledgeTypes.value[0]?.id || ''
  pushRoute({
    workspace: 'business',
    activePage: 'jobs',
    jobMode: 'create',
    wizardStep: versionId ? 2 : 1
  })
}

function closeTaskWizard() {
  goBack({ workspace: 'business', activePage: 'jobs' })
}

function openJob(jobId) {
  pushRoute({ workspace: 'business', activePage: 'jobs', selectedJobId: jobId })
}

function openKnowledgeBase(baseId) {
  pushRoute({ workspace: 'business', activePage: 'knowledge', selectedBaseId: baseId })
}

function toggleVersion(id) {
  selectedVersionIds.value = selectedVersionIds.value.includes(id)
    ? selectedVersionIds.value.filter(item => item !== id)
    : [...selectedVersionIds.value, id]
}

function nextWizardStep() {
  if (wizardStep.value === 1 && !selectedVersionIds.value.length) return notify('请至少选择一份文档', true)
  if (wizardStep.value === 2 && !jobForm.knowledge_type_id) return notify('请选择要生成的内容', true)
  replaceRoute({ wizardStep: wizardStep.value + 1 })
}

function previousWizardStep() {
  if (wizardStep.value > 1) replaceRoute({ wizardStep: wizardStep.value - 1 })
}

async function startKnowledgeJob() {
  if (!jobForm.name.trim()) return notify('请填写知识库名称', true)
  busy.value = true
  try {
    const job = await api.startKnowledgeJob({
      name: jobForm.name.trim(),
      knowledge_type_id: jobForm.knowledge_type_id,
      source_version_ids: selectedVersionIds.value
    })
    selectedJobId.value = job.id
    replaceRoute({ jobMode: 'list', wizardStep: 1, selectedJobId: job.id })
    notify('任务已创建，系统正在后台处理')
    await refreshAll(true)
  } catch (error) {
    notify(error.message, true)
  } finally {
    busy.value = false
  }
}

async function uploadSource() {
  if (!uploadFile.value) return notify('请先选择文件', true)
  busy.value = true
  const form = new FormData()
  form.append('file', uploadFile.value)
  if (uploadName.value.trim()) form.append('name', uploadName.value.trim())
  if (uploadVersionSourceId.value) form.append('source_id', uploadVersionSourceId.value)
  try {
    const result = await api.uploadSource(form)
    showUpload.value = false
    uploadFile.value = null
    uploadName.value = ''
    uploadVersionSourceId.value = ''
    replaceRoute({ showUpload: false })
    await refreshAll(true)
    selectedSourceId.value = result.source.id
    notify(result.created ? '文档上传成功' : '相同内容已经存在，未重复保存')
  } catch (error) {
    notify(error.message, true)
  } finally {
    busy.value = false
  }
}

async function selectBase(id, page = 1) {
  selectedBaseId.value = id
  recordPage.value = page
  lineage.value = null
  try {
    baseDetail.value = await api.knowledgeBase(id, { page, pageSize: 30, query: recordQuery.value })
  } catch (error) {
    notify(error.message, true)
  }
}

async function showLineage(recordId) {
  try {
    lineage.value = await api.knowledgeRecordLineage(recordId)
  } catch (error) {
    notify(error.message, true)
  }
}

async function createKnowledgeType() {
  const validFields = typeForm.fields.filter(field => field.name.trim())
  if (!typeForm.name.trim() || !validFields.length) return notify('请填写类型名称和至少一个字段', true)
  busy.value = true
  try {
    const properties = Object.fromEntries(validFields.map(field => [field.name.trim(), field.type]))
    const required = validFields.filter(field => field.required).map(field => field.name.trim())
    if (!required.length) return notify('至少需要一个必填字段', true)
    await api.createKnowledgeType({
      name: typeForm.name.trim(),
      description: typeForm.description.trim(),
      schema: { type: 'object', required, properties }
    })
    Object.assign(typeForm, {
      name: '', description: '', fields: [{ name: 'content', type: 'string', required: true }]
    })
    replaceRoute({ showTypeForm: false })
    await refreshAll(true)
    notify('知识类型已创建，可继续为它发布标准流程')
  } catch (error) {
    notify(error.message, true)
  } finally {
    busy.value = false
  }
}

async function publishStandardPipeline() {
  if (!publishForm.dataflow_pipeline_id || !publishForm.sample_task_id || !publishForm.knowledge_type_id || !publishForm.name.trim()) {
    return notify('请完整选择流程、成功样本、知识类型并填写名称', true)
  }
  busy.value = true
  try {
    const result = await api.publishStandardPipeline({ ...publishForm, name: publishForm.name.trim() })
    notify(`发布成功，已验证 ${result.checked_records} 条样本数据`)
    Object.assign(publishForm, {
      name: '', description: '', dataflow_pipeline_id: '', sample_task_id: '',
      knowledge_type_id: '', version: 1, make_default: true
    })
    await refreshAll(true)
  } catch (error) {
    notify(error.message, true)
  } finally {
    busy.value = false
  }
}

async function setDefaultPipeline(id) {
  try {
    await api.setDefaultPipeline(id)
    await refreshAll(true)
    notify('默认流程已更新，业务任务将自动使用它')
  } catch (error) {
    notify(error.message, true)
  }
}

watch(selectedBaseId, id => { if (id) selectBase(id) })
watch(() => publishForm.dataflow_pipeline_id, () => { publishForm.sample_task_id = '' })

let poller
onMounted(async () => {
  const initialRoute = window.history.state?.dataforge
    ? window.history.state
    : routeFromHash()
  window.history.replaceState(initialRoute, '', routeUrl(initialRoute))
  applyRoute(initialRoute)
  window.addEventListener('popstate', handlePopState)
  await refreshAll()
  if (selectedBaseId.value) await selectBase(selectedBaseId.value)
  poller = window.setInterval(() => {
    if (runningJobs.value.length) refreshAll(true)
  }, 3000)
})
function handlePopState(event) {
  if (event.state?.dataforge) {
    applyRoute(event.state)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
}
onBeforeUnmount(() => {
  window.clearInterval(poller)
  window.removeEventListener('popstate', handlePopState)
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">D</span>
        <div><strong>DataForge</strong><small>知识生产平台</small></div>
      </div>

      <div class="workspace-switch" aria-label="工作空间">
        <button type="button" :class="{ active: workspace === 'business' }" @click="switchWorkspace('business')">
          <b>业务工作区</b><small>文档到知识</small>
        </button>
        <button type="button" :class="{ active: workspace === 'developer' }" @click="switchWorkspace('developer')">
          <b>流程开发区</b><small>配置与调试</small>
        </button>
      </div>

      <nav>
        <p class="nav-heading">{{ workspace === 'business' ? '日常使用' : '技术配置' }}</p>
        <button
          v-for="item in activeNav" :key="item.id"
          type="button"
          class="nav-link" :class="{ active: activePage === item.id }"
          @click="navigate(item.id)"
        >
          <span class="nav-dot"></span>
          <span><b>{{ item.label }}</b><small>{{ item.note }}</small></span>
        </button>
      </nav>

      <div class="sidebar-footer">
        <span class="health-dot" :class="{ online: dashboard.health?.status === 'ok' }"></span>
        <div><b>{{ dashboard.health?.status === 'ok' ? '系统运行正常' : '服务连接异常' }}</b><small>数据保存在本机</small></div>
      </div>
    </aside>

    <main class="main-content">
      <header class="topbar">
        <div class="topbar-title">
          <button v-if="canGoBack" class="top-back" type="button" @click="goBack()" :aria-label="`返回${returnLabel || '上一页'}`">
            <span aria-hidden="true">←</span>
            返回{{ returnLabel || '上一页' }}
          </button>
          <div class="breadcrumb">{{ workspace === 'business' ? '业务工作区' : '流程开发区' }} / {{ pageTitle }}</div>
          <h1>{{ pageTitle }}</h1>
          <p>{{ pageDescription }}</p>
        </div>
        <div class="topbar-action">
          <small v-if="lastUpdated">数据自动更新 · {{ lastUpdated }}</small>
          <button v-if="actionLabel" class="primary-button" type="button" @click="handlePrimaryAction">{{ actionLabel }}</button>
        </div>
      </header>

      <div v-if="loading" class="page-loader"><span></span><p>正在载入工作空间…</p></div>

      <template v-else>
        <section v-if="activePage === 'overview'" class="page">
          <div class="next-action">
            <div>
              <span class="eyebrow">建议下一步</span>
              <h2>{{ sources.length ? '从已有文档创建一个处理任务' : '先上传第一份源文档' }}</h2>
              <p>{{ sources.length ? '只需选择文档和希望生成的内容，其余步骤由系统自动完成。' : '支持 PDF、CSV、Markdown、Word 和 TXT 文件。' }}</p>
            </div>
            <button class="primary-button large" type="button" @click="sources.length ? openTaskWizard() : openUpload()">
              {{ sources.length ? '开始创建任务' : '上传文档' }}
            </button>
          </div>

          <div class="metric-grid">
            <article><span>源文档</span><strong>{{ sources.length }}</strong><small>已纳入管理</small></article>
            <article><span>处理中</span><strong>{{ runningJobs.length }}</strong><small>系统自动执行</small></article>
            <article><span>知识库</span><strong>{{ knowledgeBases.length }}</strong><small>可供后续应用使用</small></article>
            <article><span>知识条目</span><strong>{{ totalRecords }}</strong><small>均可回溯来源</small></article>
          </div>

          <div class="two-column">
            <article class="panel">
              <div class="panel-heading"><div><span class="eyebrow">最近任务</span><h2>处理进展</h2></div></div>
              <div v-if="knowledgeJobs.length" class="simple-list">
                <button v-for="job in knowledgeJobs.slice(0, 5)" :key="job.id" type="button" @click="openJob(job.id)">
                  <span><b>{{ job.name }}</b><small>{{ job.knowledge_type_name }} · {{ formatTime(job.created_at) }}</small></span>
                  <span class="status" :class="job.status">{{ statusText(job.status) }}</span>
                </button>
              </div>
              <div v-else class="empty-state">还没有处理任务</div>
            </article>

            <article class="panel">
              <div class="panel-heading"><div><span class="eyebrow">可用能力</span><h2>可以生成什么</h2></div></div>
              <div v-if="readyKnowledgeTypes.length" class="capability-list">
                <div v-for="type in readyKnowledgeTypes" :key="type.id"><span class="check">✓</span><span><b>{{ type.name }}</b><small>{{ type.description }}</small></span></div>
              </div>
              <div v-else class="empty-state">流程管理员尚未发布可用能力</div>
            </article>
          </div>
        </section>

        <section v-if="activePage === 'sources'" class="page">
          <article v-if="showUpload" class="panel editor-panel">
            <div class="panel-heading">
              <div><span class="eyebrow">添加内容</span><h2>上传文档</h2></div>
              <button class="text-button" type="button" @click="closeCurrentPanel">取消并返回</button>
            </div>
            <div class="upload-grid">
              <label class="file-picker">
                <input type="file" accept=".pdf,.csv,.md,.doc,.docx,.txt" @change="uploadFile = $event.target.files[0]" />
                <b>{{ uploadFile?.name || '选择文件' }}</b>
                <small>支持 PDF、CSV、Markdown、Word、TXT</small>
              </label>
              <label><span>文档名称（可选）</span><input v-model="uploadName" placeholder="默认使用文件名" /></label>
              <label><span>作为已有文档的新版本（可选）</span>
                <select v-model="uploadVersionSourceId"><option value="">创建新文档</option><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}</option></select>
              </label>
            </div>
            <div class="editor-actions"><button class="primary-button" type="button" :disabled="busy" @click="uploadSource">{{ busy ? '正在上传…' : '确认上传' }}</button></div>
          </article>

          <div class="content-split">
            <article class="panel list-panel">
              <div class="panel-heading"><div><span class="eyebrow">全部文档</span><h2>{{ sources.length }} 份源文档</h2></div></div>
              <div v-if="sources.length" class="entity-list">
                  <button v-for="source in sources" :key="source.id" class="entity-row" type="button" :class="{ selected: selectedSourceId === source.id }" @click="selectedSourceId = source.id">
                  <span class="file-badge">{{ kindText(source.kind, source.latest_version?.original_filename) }}</span>
                  <span><b>{{ source.name }}</b><small>{{ source.version_count }} 个版本 · {{ formatTime(source.latest_version?.created_at) }}</small></span>
                </button>
              </div>
              <div v-else class="empty-state">还没有文档，点击右上角“上传文档”开始</div>
            </article>

            <article class="panel detail-panel">
              <template v-if="selectedSource">
                <div class="panel-heading"><div><span class="eyebrow">文档详情</span><h2>{{ selectedSource.name }}</h2></div></div>
                <div class="detail-summary">
                  <div><span>版本数量</span><b>{{ selectedSource.version_count }}</b></div>
                  <div><span>最近更新</span><b>{{ formatTime(selectedSource.latest_version?.created_at) }}</b></div>
                  <div><span>文件类型</span><b>{{ kindText(selectedSource.kind, selectedSource.latest_version?.original_filename) }}</b></div>
                </div>
                <h3 class="section-title">版本记录</h3>
                <div class="version-list">
                  <div v-for="version in selectedSource.versions" :key="version.id">
                    <span class="version-tag">V{{ version.version_no }}</span>
                    <span><b>{{ version.original_filename }}</b><small>{{ formatSize(version.size_bytes) }} · {{ formatTime(version.created_at) }}</small></span>
                    <button class="ghost-button" type="button" @click="openTaskWizard(version.id)">用此版本创建任务</button>
                  </div>
                </div>
              </template>
              <div v-else class="empty-state">从左侧选择一份文档查看详情</div>
            </article>
          </div>
        </section>

        <section v-if="activePage === 'jobs'" class="page">
          <template v-if="jobMode === 'create'">
            <div class="wizard-heading">
              <button class="back-button" type="button" @click="closeTaskWizard">← 返回{{ returnLabel || '任务列表' }}</button>
              <div class="wizard-steps">
                <div v-for="(label, index) in ['选择文档', '选择生成内容', '确认并开始']" :key="label" :class="{ active: wizardStep >= index + 1 }">
                  <span>{{ index + 1 }}</span><b>{{ label }}</b>
                </div>
              </div>
            </div>

            <article class="panel wizard-panel">
              <template v-if="wizardStep === 1">
                <span class="eyebrow">第 1 步</span><h2>选择要处理的文档</h2>
                <p class="help-text">系统默认使用每份文档的最新版本，也可以一次选择多份并行处理。</p>
                <div v-if="latestVersions.length" class="document-grid">
                  <button v-for="version in latestVersions" :key="version.id" type="button" :class="{ selected: selectedVersionIds.includes(version.id) }" @click="toggleVersion(version.id)">
                    <span class="selection">{{ selectedVersionIds.includes(version.id) ? '✓' : '' }}</span>
                    <span><b>{{ version.source_name }}</b><small>{{ version.original_filename }} · {{ formatSize(version.size_bytes) }}</small></span>
                  </button>
                </div>
                <div v-else class="empty-state">
                  <p>暂无可选文档，请先上传一份源文档。</p>
                  <button class="ghost-button" type="button" @click="openUpload">去上传文档</button>
                </div>
              </template>

              <template v-if="wizardStep === 2">
                <span class="eyebrow">第 2 步</span><h2>希望生成什么内容？</h2>
                <p class="help-text">这里只展示管理员已配置并验证通过的内容类型。</p>
                <div v-if="readyKnowledgeTypes.length" class="type-grid">
                  <button v-for="type in readyKnowledgeTypes" :key="type.id" type="button" :class="{ selected: jobForm.knowledge_type_id === type.id }" @click="jobForm.knowledge_type_id = type.id">
                    <span class="selection">{{ jobForm.knowledge_type_id === type.id ? '✓' : '' }}</span>
                    <b>{{ type.name }}</b><small>{{ type.description }}</small>
                  </button>
                </div>
                <div v-else class="empty-state">
                  <p>当前暂无可用生成内容，需要先在后台发布标准流程。</p>
                  <button class="ghost-button" type="button" @click="closeTaskWizard">返回任务列表</button>
                </div>
              </template>

              <template v-if="wizardStep === 3">
                <span class="eyebrow">第 3 步</span><h2>确认任务信息</h2>
                <p class="help-text">系统会自动选择已验证的默认处理方案，并在完成后创建知识库。</p>
                <label class="form-field"><span>知识库名称</span><input v-model="jobForm.name" placeholder="例如：临床指南知识库" /></label>
                <div class="confirmation">
                  <div><span>处理文档</span><b>{{ selectedVersionIds.length }} 份</b></div>
                  <div><span>生成内容</span><b>{{ selectedType?.name }}</b></div>
                  <div><span>处理方式</span><b>系统自动匹配</b><small>已发布并通过格式验证</small></div>
                </div>
              </template>

              <footer class="wizard-footer">
                <button v-if="wizardStep > 1" class="ghost-button" type="button" @click="previousWizardStep">上一步</button>
                <span></span>
                <button v-if="wizardStep < 3" class="primary-button" type="button" @click="nextWizardStep">下一步</button>
                <button v-else class="primary-button" type="button" :disabled="busy" @click="startKnowledgeJob">{{ busy ? '正在创建…' : '开始处理' }}</button>
              </footer>
            </article>
          </template>

          <template v-else>
            <div class="content-split jobs-split">
              <article class="panel list-panel">
                <div class="panel-heading"><div><span class="eyebrow">全部任务</span><h2>{{ knowledgeJobs.length }} 个处理任务</h2></div></div>
                <div v-if="knowledgeJobs.length" class="entity-list">
                  <button v-for="job in knowledgeJobs" :key="job.id" class="entity-row" type="button" :class="{ selected: selectedJobId === job.id }" @click="selectedJobId = job.id">
                    <span class="status-dot" :class="job.status"></span>
                    <span><b>{{ job.name }}</b><small>{{ job.knowledge_type_name }} · {{ formatTime(job.created_at) }}</small></span>
                    <span class="status" :class="job.status">{{ statusText(job.status) }}</span>
                  </button>
                </div>
                <div v-else class="empty-state">还没有任务，点击右上角开始创建</div>
              </article>

              <article class="panel detail-panel">
                <template v-if="selectedJob">
                  <div class="panel-heading"><div><span class="eyebrow">任务详情</span><h2>{{ selectedJob.name }}</h2></div><span class="status" :class="selectedJob.status">{{ statusText(selectedJob.status) }}</span></div>
                  <div class="progress"><i :style="{ width: `${selectedJob.progress || 0}%` }"></i></div>
                  <div class="detail-summary">
                    <div><span>处理进度</span><b>{{ selectedJob.progress || 0 }}%</b></div>
                    <div><span>文档数量</span><b>{{ selectedJob.source_version_ids?.length || 0 }}</b></div>
                    <div><span>生成内容</span><b>{{ selectedJob.knowledge_type_name }}</b></div>
                  </div>
                  <div v-if="selectedJob.validation?.checked_records" class="result-box">
                    <b>格式验证已完成</b>
                    <span>检查 {{ selectedJob.validation.checked_records }} 条，通过 {{ selectedJob.validation.valid_records }} 条</span>
                  </div>
                  <div v-if="selectedJob.error" class="error-box">{{ selectedJob.error }}</div>
                  <button v-if="selectedJob.knowledge_base_id" class="primary-button" type="button" @click="openKnowledgeBase(selectedJob.knowledge_base_id)">查看生成的知识库</button>
                </template>
                <div v-else class="empty-state">从左侧选择一个任务查看进展</div>
              </article>
            </div>
          </template>
        </section>

        <section v-if="activePage === 'knowledge'" class="page">
          <div class="content-split knowledge-split">
            <article class="panel list-panel">
              <div class="panel-heading"><div><span class="eyebrow">全部知识库</span><h2>{{ knowledgeBases.length }} 个知识库</h2></div></div>
              <div v-if="knowledgeBases.length" class="entity-list">
                <button v-for="base in knowledgeBases" :key="base.id" class="entity-row" type="button" :class="{ selected: selectedBaseId === base.id }" @click="selectBase(base.id)">
                  <span class="file-badge knowledge">知识</span>
                  <span><b>{{ base.name }}</b><small>{{ base.knowledge_type_name }} · {{ base.record_count }} 条</small></span>
                </button>
              </div>
              <div v-else class="empty-state">
                <p>处理任务完成后，知识库会出现在这里。</p>
                  <button class="ghost-button" type="button" @click="openTaskWizard()">新建处理任务</button>
              </div>
            </article>

            <article class="panel detail-panel">
              <template v-if="baseDetail">
                <div class="panel-heading"><div><span class="eyebrow">知识库详情</span><h2>{{ baseDetail.knowledge_base.name }}</h2></div><span class="status available">可使用</span></div>
                <div class="detail-summary">
                  <div><span>知识类型</span><b>{{ baseDetail.knowledge_base.knowledge_type_name }}</b></div>
                  <div><span>条目数量</span><b>{{ baseDetail.knowledge_base.record_count }}</b></div>
                  <div><span>生成时间</span><b>{{ formatTime(baseDetail.knowledge_base.created_at) }}</b></div>
                </div>
                <form class="record-search" @submit.prevent="selectBase(selectedBaseId, 1)">
                  <label><span>搜索知识内容或源文档</span><input v-model="recordQuery" placeholder="输入关键词" /></label>
                  <button class="ghost-button" type="submit">搜索</button>
                </form>
                <div v-if="baseDetail.records.length" class="record-list">
                  <button v-for="(record, index) in baseDetail.records" :key="record.id" type="button" @click="showLineage(record.id)">
                    <span>{{ (recordPage - 1) * 30 + index + 1 }}</span>
                    <span><b>{{ recordSummary(record) }}</b><small>来源：{{ record.source_name }} · V{{ record.source_version_no }}</small></span>
                    <em>查看前后对照</em>
                  </button>
                </div>
                <div v-else class="empty-state">没有找到匹配的知识条目</div>
                <div class="pagination">
                  <button class="ghost-button" type="button" :disabled="recordPage <= 1" @click="selectBase(selectedBaseId, recordPage - 1)">上一页</button>
                  <span>第 {{ recordPage }} / {{ baseDetail.pagination.pages }} 页</span>
                  <button class="ghost-button" type="button" :disabled="recordPage >= baseDetail.pagination.pages" @click="selectBase(selectedBaseId, recordPage + 1)">下一页</button>
                </div>
              </template>
              <div v-else class="empty-state">从左侧选择一个知识库</div>
            </article>
          </div>

          <article v-if="lineage" class="panel comparison-panel">
            <div class="panel-heading"><div><span class="eyebrow">逐条溯源</span><h2>处理前后对照</h2></div><button class="text-button" type="button" @click="lineage = null">关闭</button></div>
            <p class="help-text">左侧是这条知识实际引用的源文档片段，右侧是处理后写入知识库的内容。</p>
            <div class="compare-grid">
              <section>
                <header><span>处理前</span><b>{{ lineage.source_name }} · V{{ lineage.source_version_no }}</b></header>
                <pre>{{ lineage.source_locator?.source_excerpt || '该记录未保存可展示的源文本片段' }}</pre>
              </section>
              <section class="after">
                <header><span>处理后</span><b>{{ lineage.knowledge_type_name }}</b></header>
                <pre>{{ recordContent(lineage.data) }}</pre>
              </section>
            </div>
            <details class="technical-details">
              <summary>查看技术信息</summary>
              <pre>{{ recordContent({ source_locator: lineage.source_locator, record_id: lineage.id, standard_pipeline: lineage.standard_pipeline_name }) }}</pre>
            </details>
          </article>
        </section>

        <section v-if="activePage === 'types'" class="page developer-page">
          <div class="developer-notice"><b>这里的配置会直接影响业务工作区</b><span>新类型创建后，需要发布至少一个通过验证的标准流程，业务人员才能选择它。</span></div>

          <article v-if="showTypeForm" class="panel editor-panel">
            <div class="panel-heading"><div><span class="eyebrow">类型配置</span><h2>新建知识类型</h2></div><button class="text-button" type="button" @click="closeCurrentPanel">取消并返回</button></div>
            <div class="type-form">
              <label><span>类型名称</span><input v-model="typeForm.name" placeholder="例如：实体关系知识库" /></label>
              <label><span>业务说明</span><input v-model="typeForm.description" placeholder="向业务人员说明这种内容适合做什么" /></label>
              <div class="field-editor">
                <div class="field-heading"><b>输出字段</b><button class="text-button" type="button" @click="typeForm.fields.push({ name: '', type: 'string', required: true })">添加字段</button></div>
                <div v-for="(field, index) in typeForm.fields" :key="index" class="field-row">
                  <input v-model="field.name" placeholder="字段名" />
                  <select v-model="field.type"><option value="string">文本</option><option value="integer">整数</option><option value="array">列表</option><option value="object">对象</option></select>
                  <label class="check-label"><input v-model="field.required" type="checkbox" /> 必填</label>
                  <button class="remove-button" type="button" :disabled="typeForm.fields.length === 1" @click="typeForm.fields.splice(index, 1)">移除</button>
                </div>
              </div>
            </div>
            <div class="editor-actions"><button class="primary-button" type="button" :disabled="busy" @click="createKnowledgeType">保存类型</button></div>
          </article>

          <div class="type-catalog">
            <article v-for="type in knowledgeTypes" :key="type.id" class="panel type-catalog-card">
              <div class="panel-heading">
                <div><span class="eyebrow">动态配置</span><h2>{{ type.name }}</h2></div>
                <span class="status" :class="readyKnowledgeTypes.some(item => item.id === type.id) ? 'validated' : 'configured'">{{ readyKnowledgeTypes.some(item => item.id === type.id) ? '业务可用' : '待发布流程' }}</span>
              </div>
              <p>{{ type.description || '暂无业务说明' }}</p>
              <div class="schema-fields">
                <span v-for="(fieldType, fieldName) in type.schema.properties" :key="fieldName">
                  <b>{{ fieldName }}</b><small>{{ fieldTypeText(fieldType) }}{{ type.schema.required.includes(fieldName) ? ' · 必填' : '' }}</small>
                </span>
              </div>
              <footer>{{ standardPipelines.filter(pipe => pipe.knowledge_type_id === type.id && pipe.validation_status === 'validated').length }} 个已发布流程</footer>
            </article>
          </div>
        </section>

        <section v-if="activePage === 'standard'" class="page developer-page">
          <div class="developer-notice"><b>发布关系</b><span>DataFlow 成功样本 → 按知识类型校验输出 → 发布为业务可用的标准流程。</span></div>
          <div class="standard-layout">
            <article class="panel publish-panel">
              <div class="panel-heading"><div><span class="eyebrow">发布能力</span><h2>从调试结果发布</h2></div></div>
              <div class="publish-form">
                <label><span>1. DataFlow 流程</span><select v-model="publishForm.dataflow_pipeline_id"><option value="">请选择</option><option v-for="pipe in dataflowPipelines" :key="pipe.id" :value="pipe.id" :disabled="pipe.is_draft">{{ pipe.name }}{{ pipe.is_draft ? '（空白草稿）' : '' }}</option></select></label>
                <label><span>2. 成功样本任务</span><select v-model="publishForm.sample_task_id"><option value="">请选择</option><option v-for="task in compatibleTasks" :key="task.task_id" :value="task.task_id">{{ task.name || `任务 ${task.task_id.slice(0, 8)}` }} · {{ formatTime(task.completed_at || task.started_at) }}</option></select><small v-if="selectedPipeline && !compatibleTasks.length">当前流程还没有成功样本，请先进入调试台运行。</small></label>
                <button v-if="selectedPipeline && !compatibleTasks.length" class="ghost-button" type="button" @click="navigate('studio')">去调试台运行样本</button>
                <label><span>3. 输出知识类型</span><select v-model="publishForm.knowledge_type_id"><option value="">请选择</option><option v-for="type in knowledgeTypes" :key="type.id" :value="type.id">{{ type.name }}</option></select></label>
                <label><span>4. 标准流程名称</span><input v-model="publishForm.name" placeholder="例如：文档转问答 V1" /></label>
                <label><span>说明（可选）</span><textarea v-model="publishForm.description" rows="3" placeholder="适用场景、处理特点或限制"></textarea></label>
                <div class="inline-fields">
                  <label><span>版本</span><input v-model.number="publishForm.version" type="number" min="1" /></label>
                  <label class="check-label default-check"><input v-model="publishForm.make_default" type="checkbox" /> 设为该类型的默认流程</label>
                </div>
                <button class="primary-button" type="button" :disabled="busy" @click="publishStandardPipeline">{{ busy ? '正在验证…' : '验证并发布' }}</button>
              </div>
            </article>

            <article class="panel catalog-panel">
              <div class="panel-heading"><div><span class="eyebrow">流程目录</span><h2>{{ standardPipelines.length }} 个标准流程</h2></div></div>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>标准流程</th><th>输出类型</th><th>版本</th><th>状态</th><th>业务默认</th></tr></thead>
                  <tbody>
                    <tr v-for="pipe in standardPipelines" :key="pipe.id">
                      <td><b>{{ pipe.name }}</b><small>{{ pipe.description }}</small></td>
                      <td>{{ pipe.knowledge_type_name }}</td>
                      <td>V{{ pipe.version }}</td>
                      <td><span class="status" :class="pipe.validation_status">{{ statusText(pipe.validation_status) }}</span></td>
                      <td>
                        <span v-if="pipe.is_default" class="default-label">默认</span>
                        <button v-else-if="pipe.validation_status === 'validated'" class="text-button" type="button" @click="setDefaultPipeline(pipe.id)">设为默认</button>
                        <span v-else>—</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </article>
          </div>
        </section>

        <section v-if="activePage === 'studio'" class="studio-page">
          <div class="studio-bar">
            <span><b>技术人员专用</b> 在这里搭建流程并完成样本运行，成功后回到“标准流程”发布。</span>
            <div class="studio-actions">
              <span class="status" :class="studioStatus.available ? 'validated' : 'failed'">{{ studioStatus.available ? '调试台已连接' : '调试台不可用' }}</span>
              <button class="ghost-button" type="button" @click="goBack({ workspace: 'developer', activePage: 'standard' })">完成调试，返回发布</button>
            </div>
          </div>
          <iframe v-if="studioStatus.available" class="studio-frame" src="/studio/#/m/" title="DataFlow 调试台"></iframe>
          <div v-else class="studio-unavailable"><b>DataFlow 调试台未启动</b><span>{{ studioStatus.message || '请检查 DataFlow 运行环境配置。' }}</span></div>
        </section>
      </template>
    </main>

    <Transition name="toast"><div v-if="toast" class="toast" :class="{ error: toast.error }" role="status" aria-live="polite">{{ toast.message }}</div></Transition>
  </div>
</template>
