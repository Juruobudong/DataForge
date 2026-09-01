<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, createClientRequestId } from '../../api/platform'
import GraphBrowser from '../../components/GraphBrowser.vue'
import {
  normalizeQaFilters, qaApiQuery, qaPageCount, qaRouteQuery, resetQaFilters,
} from './knowledgeDetailModel'

const route = useRoute(), router = useRouter()
const library = ref(null), items = ref([]), changes = ref([]), vector = ref(null), sources = ref([])
const reviewSummary = ref(null), selectedIds = ref([]), reviewBusy = ref(false), publishBusy = ref(false)
const deletion = ref(null), deletionJobs = ref([]), tab = ref('content'), error = ref(''), loading = ref(false)
const schemaFacets = ref(null), schemaFacetsLoading = ref(false)
const inputJobs = ref([]), selectedInputJobId = ref(''), inputPreparations = ref([]), inputReviewLoading = ref(false), inputActionBusy = ref(false)
const activeFlowReview = ref(null), selectedFlowChunkIds = ref([])
const qaListing = ref({ items: [], total: 0, page: 1, page_size: 50 })
const qaSearch = ref(''), qaSearchDraft = ref(''), qaStatus = ref('active'), qaPage = ref(1)
const qaLoading = ref(false), qaError = ref('')
const selectedQa = ref(null), drawerSources = ref([]), drawerLoading = ref(false), drawerError = ref('')
const draftQuestion = ref(''), draftAnswer = ref(''), draftContent = ref(''), draftNote = ref('')
const drawerSaving = ref(false)
const drawerPanel = ref(null), drawerCloseButton = ref(null)
let qaRequestId = 0, lastFocusedElement = null

const libraryId = computed(() => String(route.params.libraryId || ''))
const isGraph = computed(() => library.value?.knowledge_type === 'graph')
const isQa = computed(() => library.value?.knowledge_type === 'qa')
const isText = computed(() => library.value?.knowledge_type === 'text')
const reviewRequired = computed(() => reviewSummary.value?.review_required === true)
const reviewCounts = computed(() => reviewSummary.value?.counts || { total: 0, pending: 0, approved: 0, rejected: 0 })
const publishLabel = computed(() => reviewSummary.value?.has_ready_asset ? '重新全量生效入向量库' : (reviewRequired.value ? '全量生效入向量库' : '入向量库'))
const selectableItems = computed(() => (isQa.value ? qaListing.value.items : items.value).filter(item => item.status === 'active'))
const allSelected = computed(() => selectableItems.value.length > 0 && selectableItems.value.every(item => selectedIds.value.includes(item.id)))
const someSelected = computed(() => !allSelected.value && selectableItems.value.some(item => selectedIds.value.includes(item.id)))
const qaPages = computed(() => qaPageCount(qaListing.value.total, qaListing.value.page_size || 50))
const qaHasFilters = computed(() => Boolean(qaSearch.value) || qaStatus.value !== 'active')
const selectedInputJob = computed(() => inputJobs.value.find(item => item.id === selectedInputJobId.value) || null)

function syncQaFiltersFromRoute() {
  const filters = normalizeQaFilters(route.query)
  qaSearch.value = filters.q
  qaSearchDraft.value = filters.q
  qaStatus.value = filters.status
  qaPage.value = filters.page
}

async function loadQaItems() {
  const requestId = ++qaRequestId
  qaLoading.value = true
  qaError.value = ''
  try {
    const response = await api.qaPairs(libraryId.value, qaApiQuery({
      q: qaSearch.value, status: qaStatus.value, page: qaPage.value,
    }))
    if (requestId !== qaRequestId) return
    const lastPage = qaPageCount(response.total, response.page_size)
    if (response.total > 0 && response.page > lastPage) {
      await router.replace({ query: qaRouteQuery({ q: qaSearch.value, status: qaStatus.value, page: lastPage }) })
      return
    }
    if (response.total === 0 && response.page !== 1) {
      await router.replace({ query: qaRouteQuery({ q: qaSearch.value, status: qaStatus.value, page: 1 }) })
      return
    }
    qaListing.value = response
    items.value = response.items
    selectedIds.value = []
  } catch (err) {
    if (requestId === qaRequestId) qaError.value = err.message
  } finally {
    if (requestId === qaRequestId) qaLoading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  library.value = null
  closeDrawer(false)
  try {
    const libraries = await api.knowledgeLibraries()
    library.value = libraries.find(item => item.id === libraryId.value) || null
    if (!library.value) throw new Error('知识库不存在或已不可用')
    ;[changes.value, vector.value, deletionJobs.value, reviewSummary.value] = await Promise.all([
      api.changes(library.value.id), api.vectorStatus(library.value.id), api.deletionJobs(library.value.id),
      api.knowledgeReviewSummary(library.value.id),
    ])
    if (library.value.knowledge_type === 'qa') {
      syncQaFiltersFromRoute()
      await router.replace({ query: qaRouteQuery({ q: qaSearch.value, status: qaStatus.value, page: qaPage.value }) })
      await loadQaItems()
    } else {
      items.value = await api.knowledgeItems(library.value.id)
    }
    tab.value = 'content'
    deletion.value = null
    sources.value = []
    schemaFacets.value = null; schemaFacetsLoading.value = false
    inputJobs.value = []; selectedInputJobId.value = ''; inputPreparations.value = []
    activeFlowReview.value = null; selectedFlowChunkIds.value = []
    selectedIds.value = []
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function loadSchemaFacets() {
  if (!isGraph.value || schemaFacets.value) return
  schemaFacetsLoading.value = true
  try { schemaFacets.value = await api.graphTypeFacets(libraryId.value) }
  catch { schemaFacets.value = null }
  finally { schemaFacetsLoading.value = false }
}

async function applyQaFilters(changes = {}) {
  const next = resetQaFilters({ q: qaSearch.value, status: qaStatus.value, page: qaPage.value }, changes)
  const query = qaRouteQuery(next)
  if (JSON.stringify(query) === JSON.stringify(qaRouteQuery(route.query))) {
    syncQaFiltersFromRoute()
    closeDrawer()
    await loadQaItems()
    return
  }
  await router.replace({ query })
}

async function goQaPage(page) {
  if (page < 1 || page > qaPages.value || page === qaPage.value) return
  closeDrawer()
  await router.replace({ query: qaRouteQuery({ q: qaSearch.value, status: qaStatus.value, page }) })
}

async function openQaDrawer(item, event) {
  lastFocusedElement = event?.currentTarget || document.activeElement
  selectedQa.value = item
  draftQuestion.value = item.data?.question || ''
  draftAnswer.value = item.data?.answer || ''
  draftContent.value = item.canonical_content || ''
  draftNote.value = item.review_note || ''
  drawerSources.value = []
  drawerError.value = ''
  drawerLoading.value = true
  await nextTick()
  drawerCloseButton.value?.focus()
  try {
    const result = await api.knowledgeItemSources(item.id)
    if (selectedQa.value?.id === item.id) drawerSources.value = result
  } catch (err) {
    if (selectedQa.value?.id === item.id) drawerError.value = err.message
  } finally {
    if (selectedQa.value?.id === item.id) drawerLoading.value = false
  }
}

function closeDrawer(restoreFocus = true) {
  const focusTarget = lastFocusedElement
  selectedQa.value = null
  drawerSources.value = []
  drawerError.value = ''
  drawerLoading.value = false
  draftQuestion.value = ''; draftAnswer.value = ''; draftContent.value = ''; draftNote.value = ''
  lastFocusedElement = null
  if (restoreFocus && focusTarget?.focus) nextTick(() => focusTarget.focus())
}

async function loadInputReview() {
  if (inputReviewLoading.value) return
  inputReviewLoading.value = true; error.value = ''
  try {
    const jobs = await api.knowledgeJobs()
    inputJobs.value = jobs.filter(job => Object.values(job.sink_library_ids || job.output_library_ids || {}).includes(libraryId.value))
    if (!inputJobs.value.some(job => job.id === selectedInputJobId.value)) selectedInputJobId.value = inputJobs.value[0]?.id || ''
    inputPreparations.value = selectedInputJobId.value ? (await api.knowledgeJobInputPreparations(selectedInputJobId.value)).inputs : []
  } catch (err) { error.value = err.message }
  finally { inputReviewLoading.value = false }
}
async function chooseInputJob() { inputPreparations.value = selectedInputJobId.value ? (await api.knowledgeJobInputPreparations(selectedInputJobId.value)).inputs : [] }
async function runInputAction(kind) {
  const job = selectedInputJob.value
  if (!job || inputActionBusy.value) return
  const reprepare = kind === 'reprepare'
  const message = reprepare
    ? '重新准备输入会强制创建新的 FlowChunkSet，并要求重新审核；不会复用当前冻结快照。确认继续？'
    : '重新生成知识会复用所选冻结 Snapshot，只执行 Gate 下游，不会重新调用 Chunker。确认继续？'
  if (!window.confirm(message)) return
  inputActionBusy.value = true; error.value = ''
  try {
    await (reprepare ? api.reprepareKnowledgeJobInput(job.id) : api.regenerateKnowledgeJob(job.id))
    await loadInputReview()
  } catch (err) { error.value = err.message }
  finally { inputActionBusy.value = false }
}
async function retryFailedUnits() {
  if (!selectedInputJob.value || inputActionBusy.value) return
  inputActionBusy.value = true; error.value = ''
  try { await api.retryFailedGenerationUnits(selectedInputJob.value.id); await loadInputReview() }
  catch (err) { error.value = err.message }
  finally { inputActionBusy.value = false }
}
async function openFlowReview(setId) { activeFlowReview.value = await api.flowChunkSetReview(setId); selectedFlowChunkIds.value = [] }
async function refreshFlowReview() { if (activeFlowReview.value?.flow_chunk_set?.id) await openFlowReview(activeFlowReview.value.flow_chunk_set.id) }
async function reviewFlowChunk(chunk, status) { await api.reviewFlowChunk(chunk.id, { status, expected_revision_no: chunk.revision_no }); await refreshFlowReview() }
async function editFlowChunk(chunk) { const content = window.prompt('修改 FlowChunk 内容', chunk.content); if (content == null || content === chunk.content) return; await api.updateFlowChunk(chunk.id, { content, expected_revision_no: chunk.revision_no }); await refreshFlowReview() }
async function splitFlowChunk(chunk) { const value = window.prompt('用空行分隔拆分后的片段', chunk.content); const parts = String(value || '').split(/\n\s*\n/).map(item => item.trim()).filter(Boolean); if (parts.length < 2) return; await api.splitFlowChunk(chunk.id, { parts, expected_revision_no: chunk.revision_no }); await refreshFlowReview() }
async function mergeSelectedFlowChunks() { const chunks = activeFlowReview.value.chunks.filter(item => selectedFlowChunkIds.value.includes(item.id)); if (chunks.length < 2) return; await api.mergeFlowChunks({ chunk_ids: chunks.map(item => item.id), expected_revisions: Object.fromEntries(chunks.map(item => [item.id, item.revision_no])) }); await refreshFlowReview() }
async function batchReviewFlowChunks(status) { const chunks = activeFlowReview.value.chunks.filter(item => selectedFlowChunkIds.value.includes(item.id)); if (!chunks.length) return; await api.batchReviewFlowChunks({ chunk_ids: chunks.map(item => item.id), action: status, expected_revisions: Object.fromEntries(chunks.map(item => [item.id, item.revision_no])) }); await refreshFlowReview() }
async function freezeActiveFlowSet() { await api.freezeFlowChunkSet(activeFlowReview.value.flow_chunk_set.id); activeFlowReview.value = null; await loadInputReview() }

function toggleSelectAll(checked) { selectedIds.value = checked ? selectableItems.value.map(item => item.id) : [] }
async function refreshKnowledgeState() {
  reviewSummary.value = await api.knowledgeReviewSummary(libraryId.value)
  vector.value = await api.vectorStatus(libraryId.value)
  if (isQa.value) await loadQaItems()
  else items.value = await api.knowledgeItems(libraryId.value)
  selectedIds.value = []
}
async function batchReview(action) {
  const selected = selectableItems.value.filter(item => selectedIds.value.includes(item.id))
  if (!selected.length || reviewBusy.value) return
  reviewBusy.value = true; error.value = ''
  try {
    await api.batchReviewKnowledgeItems(libraryId.value, {
      item_ids: selected.map(item => item.id), action,
      expected_revisions: Object.fromEntries(selected.map(item => [item.id, item.review_revision])),
    })
    await refreshKnowledgeState()
  } catch (err) { error.value = err.message }
  finally { reviewBusy.value = false }
}
async function publishVectors() {
  const summary = reviewSummary.value
  if (!summary?.can_publish || publishBusy.value) return
  const counts = summary.counts || {}
  const message = reviewRequired.value
    ? `即将生成新的完整知识资产版本。\n\n审核通过：${counts.approved || 0} 条\n不通过：${counts.rejected || 0} 条，不进入本版本\n待审核：${counts.pending || 0} 条\n\n确认继续？`
    : `即将把当前 ${summary.selected_count || 0} 条活动知识冻结为新的完整资产版本。\n\n确认继续？`
  if (!window.confirm(message)) return
  publishBusy.value = true; error.value = ''
  try {
    await api.publishKnowledgeVectors(libraryId.value, {
      scope: summary.scope, expected_snapshot_digest: summary.snapshot_digest,
      idempotency_key: createClientRequestId(),
    })
    await refreshKnowledgeState()
  } catch (err) { error.value = err.message; await refreshKnowledgeState().catch(() => {}) }
  finally { publishBusy.value = false }
}
async function saveDrawerContent() {
  if (!selectedQa.value || drawerSaving.value) return
  drawerSaving.value = true; drawerError.value = ''
  try {
    selectedQa.value = await api.updateKnowledgeItem(selectedQa.value.id, {
      ...(isQa.value ? { data: { question: draftQuestion.value, answer: draftAnswer.value } }
        : { canonical_content: draftContent.value }),
      expected_review_revision: selectedQa.value.review_revision,
    })
    draftQuestion.value = selectedQa.value.data?.question || ''
    draftAnswer.value = selectedQa.value.data?.answer || ''
    draftContent.value = selectedQa.value.canonical_content || ''
    await refreshKnowledgeState()
  } catch (err) { drawerError.value = err.message }
  finally { drawerSaving.value = false }
}
async function reviewDrawer(status) {
  if (!selectedQa.value || drawerSaving.value) return
  drawerSaving.value = true; drawerError.value = ''
  try {
    selectedQa.value = await api.reviewKnowledgeItem(selectedQa.value.id, {
      status, expected_review_revision: selectedQa.value.review_revision, review_note: draftNote.value || null,
    })
    await refreshKnowledgeState()
  } catch (err) { drawerError.value = err.message }
  finally { drawerSaving.value = false }
}

function onDrawerKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeDrawer()
    return
  }
  if (event.key !== 'Tab') return
  const focusable = [...(drawerPanel.value?.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])') || [])]
    .filter(element => !element.disabled && element.getAttribute('aria-hidden') !== 'true')
  if (!focusable.length) return
  const first = focusable[0], last = focusable.at(-1)
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault(); last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first.focus()
  }
}

async function trace(item) {
  try { sources.value = await api.knowledgeItemSources(item.id); tab.value = 'sources' } catch (err) { error.value = err.message }
}
async function checkDelete() { try { deletion.value = await api.knowledgeLibraryDeleteCheck(library.value.id) } catch (err) { error.value = err.message } }
async function remove() { try { if (!deletion.value?.deletable || !window.confirm('将异步清理该知识库的 V7 Partition，确认继续？')) return; await api.deleteKnowledgeLibrary(library.value.id); await load() } catch (err) { error.value = err.message } }
async function retryDeletion(job) { try { await api.retryDeletion(job.id); deletionJobs.value = await api.deletionJobs(library.value.id) } catch (err) { error.value = err.message } }
function shortId(value) { return value?.length > 18 ? `${value.slice(0, 18)}…` : value }
function formatTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—' }
function reviewStatusLabel(value) { return ({ pending: '待审核', approved: '已通过', rejected: '不通过' })[value] || value || '—' }
function reviewStatusClass(value) { return value === 'approved' ? 'green' : value === 'rejected' ? 'red' : 'amber' }
function vectorStateLabel(value) { return ({ not_published: '未入库', building: '构建中', ready: 'Ready', stale: '有更新', failed: '失败' })[value] || '未入库' }
function vectorStateClass(value) { return value === 'ready' ? 'green' : value === 'failed' ? 'red' : 'amber' }
function sourceAnchor(source) {
  const anchor = source.anchor || {}
  const values = []
  if (anchor.page) values.push(`第 ${anchor.page} 页`)
  if (anchor.label || anchor.file) values.push(anchor.label || anchor.file)
  return values.join(' · ') || '来源锚点'
}

watch(libraryId, load, { immediate: true })
watch(tab, value => { if (value === 'schema') loadSchemaFacets(); if (value === 'input-review') loadInputReview() })
watch(
  () => [route.query.q, route.query.status, route.query.page],
  async () => {
    if (!isQa.value || loading.value) return
    syncQaFiltersFromRoute()
    closeDrawer()
    await loadQaItems()
  },
)
</script>

<template>
  <section class="knowledge-detail">
    <button class="back-link" @click="router.push('/business/knowledge')">← 返回知识库</button>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading" class="loading">正在加载知识库…</p>
    <template v-else-if="library">
      <div class="detail-head">
        <div>
          <span class="detail-type">{{ library.graph_mode === 'triple' ? '△ 三元组图谱' : library.graph_mode === 'semantic' ? '⬡ 语义图谱' : library.knowledge_type }}</span>
          <h2>{{ library.name }}</h2>
          <button class="technical-id" :title="library.id" @click="navigator.clipboard?.writeText(library.id)">{{ shortId(library.code || library.id) }} · 点击复制完整 ID</button>
        </div>
        <div class="page-actions">
          <span v-if="library.status === 'deleting'" class="badge amber">正在删除</span>
          <button v-else class="danger" @click="checkDelete">删除检查</button>
        </div>
      </div>
      <div class="detail-metrics">
        <span><b>{{ (library.knowledge_item_count || 0).toLocaleString() }}</b> 条知识</span>
        <span v-if="library.status === 'deleting'" class="badge amber">等待 V7 Partition 清理完成</span>
        <span v-else :class="['badge', vectorStateClass(reviewSummary?.vector_state)]">向量 {{ vectorStateLabel(reviewSummary?.vector_state) }}</span>
        <span>最近更新 {{ new Date(library.updated_at).toLocaleString() }}</span>
      </div>
      <details class="library-technical-details">
        <summary>技术详情</summary>
        <dl>
          <div><dt>Knowledge Library ID</dt><dd><code>{{ library.id }}</code></dd></div>
          <div><dt>Collection</dt><dd><code v-for="name in (library.collection_names || [])" :key="name">{{ name }}</code><span v-if="!library.collection_names?.length">—</span></dd></div>
          <div><dt>Partition</dt><dd><code>{{ library.partition_name || '—' }}</code></dd></div>
        </dl>
      </details>
      <nav class="tabs">
        <button :class="{ active: tab === 'content' }" @click="tab = 'content'">知识内容</button>
        <button :class="{ active: tab === 'input-review' }" @click="tab = 'input-review'">Flow 输入快照</button>
        <button :class="{ active: tab === 'diff' }" @click="tab = 'diff'">Knowledge Diff</button>
        <button :class="{ active: tab === 'vector' }" @click="tab = 'vector'">向量状态</button>
        <button v-if="!isQa" :class="{ active: tab === 'sources' }" @click="tab = 'sources'">来源追踪</button>
        <button v-if="isGraph" :class="{ active: tab === 'graph' }" @click="tab = 'graph'">图谱浏览器</button>
        <button v-if="isGraph" :class="{ active: tab === 'schema' }" @click="tab = 'schema'">图谱 Schema</button>
      </nav>

      <section class="knowledge-review-toolbar" aria-label="知识审核与向量发布">
        <div v-if="reviewRequired" class="review-counts">
          <span>待审核 <b>{{ reviewCounts.pending }}</b></span>
          <span>已通过 <b>{{ reviewCounts.approved }}</b></span>
          <span>不通过 <b>{{ reviewCounts.rejected }}</b></span>
          <span v-if="reviewSummary?.vector_stale" class="badge amber">当前知识有未生效修改</span>
        </div>
        <div v-else class="review-counts"><span>逐条审核 <b>不适用</b></span><span v-if="reviewSummary?.vector_stale" class="badge amber">当前知识有更新</span></div>
        <div class="review-actions">
          <template v-if="reviewRequired">
            <span class="muted">已选 {{ selectedIds.length }} 条</span>
            <button :disabled="!selectedIds.length || reviewBusy" @click="batchReview('approve')">批量通过</button>
            <button :disabled="!selectedIds.length || reviewBusy" @click="batchReview('reject')">批量不通过</button>
          </template>
          <button class="primary" :disabled="!reviewSummary?.can_publish || publishBusy" :title="reviewSummary?.issues?.map(item => item.message).join('；')" @click="publishVectors">{{ publishBusy ? '正在提交…' : publishLabel }}</button>
        </div>
        <p v-if="reviewSummary?.issues?.length" class="review-issues" role="status">{{ reviewSummary.issues.map(item => item.message).join('；') }}</p>
      </section>

      <template v-if="tab === 'content'">
        <section v-if="isQa" class="qa-content" aria-label="问答知识列表">
          <div class="qa-toolbar">
            <div>
              <h3>问答知识 <span>{{ qaListing.total.toLocaleString() }}</span></h3>
              <p>默认展示有效问答；点击任意条目查看完整内容与来源引用。</p>
            </div>
            <form class="qa-filters" @submit.prevent="applyQaFilters({ q: qaSearchDraft })">
              <input v-model="qaSearchDraft" aria-label="搜索问题或答案" placeholder="搜索问题或答案">
              <select v-model="qaStatus" aria-label="筛选问答状态" @change="applyQaFilters({ q: qaSearchDraft, status: qaStatus })">
                <option value="active">有效</option>
                <option value="inactive">已失效</option>
                <option value="all">全部状态</option>
              </select>
              <button type="submit">搜索</button>
            </form>
          </div>
          <p v-if="qaError" class="error">{{ qaError }}</p>
          <div class="table-wrap qa-table-wrap" :aria-busy="qaLoading">
            <table class="qa-table">
              <colgroup><col class="qa-select-col"><col class="qa-question-col"><col class="qa-answer-col"><col class="qa-status-col"><col class="qa-time-col"><col class="qa-action-col"></colgroup>
              <thead><tr><th><input type="checkbox" :checked="allSelected" :indeterminate.prop="someSelected" aria-label="全选当前页问答" @change="toggleSelectAll($event.target.checked)"></th><th>问题</th><th>答案摘要</th><th>审核状态</th><th>更新时间</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-if="qaLoading"><td colspan="6" class="empty-cell">正在加载问答知识…</td></tr>
                <tr
                  v-for="item in qaListing.items" v-else :key="item.id" class="qa-row" tabindex="0"
                  :aria-label="`查看问答：${item.data.question}`" @click="openQaDrawer(item, $event)"
                  @keydown.enter="openQaDrawer(item, $event)" @keydown.space.prevent="openQaDrawer(item, $event)"
                >
                  <td @click.stop><input v-model="selectedIds" type="checkbox" :value="item.id" :aria-label="`选择问答 ${item.data.question}`"></td>
                  <td><div class="qa-cell" :title="item.data.question">{{ item.data.question }}</div></td>
                  <td><div class="qa-cell qa-answer" :title="item.data.answer">{{ item.data.answer }}</div></td>
                  <td><span :class="['badge', reviewStatusClass(item.review_status)]">{{ reviewStatusLabel(item.review_status) }}</span></td>
                  <td class="qa-time">{{ formatTime(item.updated_at) }}</td>
                  <td><button type="button" aria-label="查看完整问答" @click.stop="openQaDrawer(item, $event)">查看</button></td>
                </tr>
                <tr v-if="!qaLoading && !qaListing.items.length">
                  <td colspan="6" class="empty-cell">{{ qaHasFilters ? '没有符合当前搜索或状态条件的问答。' : '暂无有效问答知识。' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="qaListing.total" class="qa-pagination" aria-label="问答知识分页">
            <span>共 {{ qaListing.total.toLocaleString() }} 条 · 第 {{ qaPage }} / {{ qaPages }} 页</span>
            <div><button :disabled="qaPage <= 1 || qaLoading" @click="goQaPage(qaPage - 1)">上一页</button><button :disabled="qaPage >= qaPages || qaLoading" @click="goQaPage(qaPage + 1)">下一页</button></div>
          </div>
        </section>
        <div v-else class="table-wrap">
          <table><thead><tr><th v-if="isText"><input type="checkbox" :checked="allSelected" :indeterminate.prop="someSelected" aria-label="全选文本知识" @change="toggleSelectAll($event.target.checked)"></th><th>内容</th><th>来源</th><th>{{ isText ? '审核状态' : '状态' }}</th><th></th></tr></thead><tbody><tr v-for="item in items" :key="item.id"><td v-if="isText"><input v-model="selectedIds" type="checkbox" :value="item.id" :aria-label="`选择知识 ${item.source_knowledge_id}`"></td><td>{{ item.canonical_content }}</td><td>{{ item.source_count || item.source_version_ids?.length || 0 }}</td><td><span v-if="isText" :class="['badge', reviewStatusClass(item.review_status)]">{{ reviewStatusLabel(item.review_status) }}</span><span v-else>{{ item.status }}</span></td><td><button v-if="isText" @click="openQaDrawer(item, $event)">编辑审核</button><button v-else @click="trace(item)">查看来源</button></td></tr><tr v-if="!items.length"><td :colspan="isText ? 5 : 4" class="empty-cell">暂无知识内容。</td></tr></tbody></table>
        </div>
      </template>
      <section v-else-if="tab === 'input-review'" class="panel input-review-panel">
        <div class="panel-head"><div><h3>Flow 输入快照</h3><p>解析内容人工审阅后，系统按 Flow Revision 自动切分并冻结不可变 Snapshot；Multi 结果库共享同一快照。</p></div><button :disabled="inputReviewLoading" @click="loadInputReview">刷新</button></div>
        <label>Knowledge Job<select v-model="selectedInputJobId" :disabled="inputReviewLoading" @change="chooseInputJob"><option value="">暂无关联任务</option><option v-for="job in inputJobs" :key="job.id" :value="job.id">{{ job.template?.name || job.knowledge_flow_template_id }} · {{ job.id }} · {{ job.status }}</option></select></label>
        <div v-if="selectedInputJob" class="input-actions"><button class="primary" :disabled="inputActionBusy || !inputPreparations.some(item => item.flow_chunk_review_snapshot_id)" @click="runInputAction('regenerate')">重新生成知识</button><button :disabled="inputActionBusy" @click="runInputAction('reprepare')">重新准备输入</button><button :disabled="inputActionBusy || !selectedInputJob.failed_chunk_count" @click="retryFailedUnits">重试失败分支</button></div>
        <div class="input-cards"><article v-for="item in inputPreparations" :key="item.id"><header><b>{{ item.parsed_document?.metadata?.filename || item.parsed_document?.id }}</b><span :class="['badge', item.status === 'ready' ? 'green' : 'amber']">{{ item.status }}</span></header><dl><div><dt>Flow Revision</dt><dd><code>{{ selectedInputJob?.knowledge_flow_template_revision_id }}</code></dd></div><div><dt>Approved ParsedDocument</dt><dd><code>{{ item.parsed_document?.id }}</code></dd></div><div><dt>FlowChunkSet</dt><dd><code>{{ item.flow_chunk_set?.id || '整文直连，无需分块' }}</code></dd></div><div><dt>自动冻结 Snapshot</dt><dd><code>{{ item.flow_chunk_review_snapshot_id || '系统正在准备输入' }}</code></dd></div></dl></article><p v-if="!inputPreparations.length">当前知识库还没有可展示的 Flow 输入任务。</p></div>
      </section>
      <div v-else-if="tab === 'diff'" class="change-list"><article v-for="change in changes" :key="change.id"><b>{{ change.change_type }}</b><p>{{ change.before?.content || change.before_hash || '—' }} → {{ change.after?.content || change.after_hash || '—' }}</p></article><p v-if="!changes.length" class="empty-group">暂无变更记录。</p></div>
      <div v-else-if="tab === 'vector'" class="panel"><p>Vector Ready：<b>{{ vector?.ready ? '已就绪' : '未就绪' }}</b></p><pre>{{ JSON.stringify(vector?.record_states || {}, null, 2) }}</pre></div>
      <div v-else-if="tab === 'sources'">
        <button class="back-link" @click="tab = 'content'">← 返回知识内容</button>
        <div class="source-list"><article v-for="source in sources" :key="source.id"><b>{{ source.source.original_filename || source.source.name }}</b><small>{{ source.anchor.label || source.anchor.file || '来源锚点' }}</small><p>{{ source.evidence_text }}</p></article><p v-if="!sources.length" class="empty-group">从知识内容中选择“查看来源”后会在这里显示 Evidence。</p></div>
      </div>
      <GraphBrowser v-else-if="tab === 'graph'" :library-id="library.id" />
      <div v-else-if="tab === 'schema'" class="panel schema-panel">
        <h3>图谱 Schema（只读）</h3>
        <p class="muted">Schema 由知识流程定义，正式产出时固化到知识库；此处仅查看，不可编辑。</p>
        <p v-if="library.source_template_revision_id">来源模板 Revision：<code>{{ library.source_template_revision_id }}</code></p>
        <p v-if="library.graph_schema_hash">Schema Hash：<code>{{ library.graph_schema_hash }}</code></p>

        <h4>Schema 定义（来自知识流程）</h4>
        <template v-if="library.graph_schema_snapshot">
          <p class="schema-sub">实体类型</p>
          <ul v-if="library.graph_schema_snapshot.entity_types?.length">
            <li v-for="e in library.graph_schema_snapshot.entity_types" :key="e.code"><b>{{ e.label }}</b><code>{{ e.code }}</code><span v-if="e.description"> · {{ e.description }}</span></li>
          </ul>
          <p v-else class="muted">未定义实体类型（不约束）。</p>
          <p class="schema-sub">关系类型</p>
          <ul v-if="library.graph_schema_snapshot.relation_types?.length">
            <li v-for="r in library.graph_schema_snapshot.relation_types" :key="r.code"><b>{{ r.label }}</b><code>{{ r.code }}</code><span>（{{ (r.source_types || []).join('、') || '任意' }} → {{ (r.target_types || []).join('、') || '任意' }}）</span></li>
          </ul>
          <p v-else class="muted">未定义关系类型（不约束）。</p>
        </template>
        <p v-else class="muted">该知识库尚无图谱 Schema 快照（尚未正式产出）。</p>

        <h4>实际抽取类型（来自图谱数据）</h4>
        <p v-if="schemaFacetsLoading" class="muted">正在加载实际抽取类型…</p>
        <p v-else-if="!schemaFacets" class="muted">实际抽取类型暂不可用。</p>
        <template v-else>
          <p class="schema-sub">实体类型</p>
          <ul v-if="schemaFacets.entity_types?.length">
            <li v-for="t in schemaFacets.entity_types" :key="t.code"><b>{{ t.label }}</b><code>{{ t.code }}</code><span class="schema-count">{{ t.count }}</span></li>
          </ul>
          <p v-else class="muted">尚未抽取到实体类型。</p>
          <p class="schema-sub">关系类型</p>
          <ul v-if="schemaFacets.relation_types?.length">
            <li v-for="t in schemaFacets.relation_types" :key="t.code"><b>{{ t.label }}</b><code>{{ t.code }}</code><span class="schema-count">{{ t.count }}</span></li>
          </ul>
          <p v-else class="muted">尚未抽取到关系类型。</p>
        </template>
      </div>
      <section v-if="deletion" class="deletion-panel"><h3>删除检查</h3><p>{{ deletion.deletable ? '当前知识库可删除。' : '当前知识库仍被引用，暂不能删除。' }}</p><pre>{{ JSON.stringify(deletion, null, 2) }}</pre><button v-if="deletion.deletable" class="danger" @click="remove">确认异步删除</button></section>
      <section v-if="deletionJobs.length" class="deletion-panel"><h3>删除任务</h3><div v-for="job in deletionJobs" :key="job.id" class="deletion-job"><span>{{ job.status }}</span><small>{{ job.error || job.id }}</small><button v-if="job.status === 'failed'" @click="retryDeletion(job)">重试</button></div></section>
    </template>

    <div v-if="selectedQa" class="drawer-backdrop" @click.self="closeDrawer()">
      <aside ref="drawerPanel" :class="['qa-drawer', { 'text-review-drawer': isText }]" role="dialog" aria-modal="true" aria-labelledby="qa-drawer-title" @keydown="onDrawerKeydown">
        <header>
          <div><small>{{ isQa ? '问答知识审核' : '文本知识审核' }}</small><h3 id="qa-drawer-title">编辑最终知识与审核</h3></div>
          <button ref="drawerCloseButton" type="button" aria-label="关闭问答详情" @click="closeDrawer()">关闭</button>
        </header>
        <div :class="['drawer-body', { 'text-review-layout': isText }]">
          <section class="drawer-section qa-full-content editor-section">
            <template v-if="isQa"><label>问题<input v-model="draftQuestion"></label><label>答案<textarea v-model="draftAnswer" rows="8"></textarea></label></template>
            <label v-else>最终知识内容<textarea v-model="draftContent" rows="12"></textarea></label>
            <button :disabled="drawerSaving" @click="saveDrawerContent">{{ drawerSaving ? '保存中…' : '保存修改' }}</button>
            <small>内容修改后会自动回到“待审核”；来源与 Evidence 不会被改写。</small>
          </section>
          <section class="drawer-section management-section">
            <h4>管理信息</h4>
            <dl><div><dt>审核状态</dt><dd><span :class="['badge', reviewStatusClass(selectedQa.review_status)]">{{ reviewStatusLabel(selectedQa.review_status) }}</span></dd></div><div><dt>审核 Revision</dt><dd>{{ selectedQa.review_revision }}</dd></div><div><dt>更新时间</dt><dd>{{ new Date(selectedQa.updated_at).toLocaleString() }}</dd></div><div><dt>来源数量</dt><dd>{{ selectedQa.source_count || 0 }}</dd></div><div><dt>知识项 ID</dt><dd><code>{{ selectedQa.id }}</code></dd></div><div><dt>来源知识 ID</dt><dd><code>{{ selectedQa.source_knowledge_id }}</code></dd></div><div><dt>内容 Hash</dt><dd><code>{{ selectedQa.content_hash }}</code></dd></div></dl>
          </section>
          <section class="drawer-section review-section">
            <h4>人工审核</h4>
            <label>审核备注<textarea v-model="draftNote" rows="3" placeholder="可选"></textarea></label>
            <div class="drawer-review-actions"><button class="danger" :disabled="drawerSaving" @click="reviewDrawer('rejected')">不通过</button><button class="primary" :disabled="drawerSaving" @click="reviewDrawer('approved')">通过</button></div>
          </section>
          <section class="drawer-section evidence-section">
            <h4>来源引用</h4>
            <p v-if="drawerLoading" class="muted">正在加载来源…</p>
            <p v-else-if="drawerError" class="error">{{ drawerError }}</p>
            <div v-else-if="drawerSources.length" class="drawer-source-list">
              <article v-for="source in drawerSources" :key="source.id"><div><b>{{ source.source.original_filename || source.source.name }}</b><span>v{{ source.source_version.version_no }} · {{ sourceAnchor(source) }}</span></div><p>{{ source.evidence_text || '暂无 Evidence 文本。' }}</p><a :href="api.sourcePreviewUrl(source.source.id, source.source_version.id)" target="_blank" rel="noopener">查看原文</a></article>
            </div>
            <p v-else class="muted">该问答暂未记录来源引用。</p>
          </section>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.back-link{margin-bottom:18px;border:0;color:var(--blue);background:transparent;padding:0;font-size:14px}.detail-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.detail-head h2{margin:6px 0 0;font-size:26px}.detail-type{color:var(--blue);font-size:14px;font-weight:800}.technical-id{min-height:0;margin-top:8px;padding:0;border:0;color:var(--muted);background:transparent;font-size:12px;font-weight:600}.detail-metrics{display:flex;flex-wrap:wrap;align-items:center;gap:16px;margin:18px 0;color:var(--muted);font-size:14px}.detail-metrics b{color:var(--text);font-size:18px}.library-technical-details{margin:0 0 18px;padding:10px 14px;border:1px solid var(--border);border-radius:9px;background:var(--panel-muted)}.library-technical-details summary{cursor:pointer;color:var(--muted);font-weight:700}.library-technical-details dl{display:grid;gap:8px;margin:12px 0 0}.library-technical-details dl div{display:grid;grid-template-columns:180px minmax(0,1fr);gap:12px}.library-technical-details dd{display:grid;gap:4px;margin:0;overflow-wrap:anywhere}.change-list,.source-list{display:grid;gap:10px}.change-list article,.source-list article,.deletion-panel{padding:16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow)}.change-list p,.source-list p{margin:8px 0 0;line-height:1.65}.source-list b,.source-list small{display:block}.source-list small{margin-top:5px;color:var(--muted);font-size:13px}.deletion-panel{margin-top:18px}.deletion-panel h3{margin:0;font-size:16px}.deletion-panel pre{max-height:240px;margin:12px 0}.deletion-job{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)}.deletion-job small{overflow:hidden;color:var(--muted);text-overflow:ellipsis;white-space:nowrap}.empty-cell,.loading{color:var(--muted);text-align:center}.schema-panel h3{margin:0;font-size:16px}.schema-panel h4{margin:16px 0 6px;font-size:14px}.schema-panel ul{list-style:none;margin:0;padding:0}.schema-panel li{display:flex;align-items:center;gap:8px;padding:7px 0;border-top:1px solid var(--border)}.schema-panel li b{margin-right:8px}.schema-panel code{font-size:12px;color:var(--muted)}.schema-panel .schema-sub{margin:10px 0 4px;color:var(--muted);font-size:12px;font-weight:700}.schema-panel .schema-count{margin-left:auto;color:var(--muted);font-size:12px}
.knowledge-review-toolbar{display:grid;gap:10px;margin:0 0 16px;padding:14px 16px;border:1px solid var(--border);border-radius:12px;background:var(--panel)}.review-counts,.review-actions{display:flex;align-items:center;flex-wrap:wrap;gap:12px}.review-counts span{color:var(--muted)}.review-counts b{color:var(--text)}.review-actions{justify-content:flex-end}.review-issues{margin:0;color:#a16207;font-size:13px}.drawer-review-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}.qa-full-content label,.drawer-section>label{display:grid;gap:6px;margin-bottom:12px;color:var(--muted);font-size:13px;font-weight:700}.qa-full-content input,.qa-full-content textarea,.drawer-section>label textarea{width:100%;resize:vertical}.qa-full-content small{display:block;margin-top:10px;color:var(--muted);line-height:1.5}.qa-select-col{width:42px}
.input-review-panel{display:grid;gap:14px}.input-review-panel>label{display:grid;max-width:720px;gap:6px;color:var(--muted);font-size:13px;font-weight:700}.input-actions{display:flex;flex-wrap:wrap;gap:8px}.input-cards{display:grid;gap:10px}.input-cards article{padding:14px;border:1px solid var(--border);border-radius:10px;background:var(--panel-muted)}.input-cards header{display:flex;align-items:center;justify-content:space-between;gap:12px}.input-cards dl{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:12px 0}.input-cards dt{color:var(--muted);font-size:12px}.input-cards dd{min-width:0;margin:5px 0 0}.input-cards code{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.flow-review-workbench{display:grid;gap:10px;padding:14px;border:1px solid #b8cdf0;border-radius:12px;background:#fff}.flow-review-workbench>header{display:flex;align-items:flex-start;justify-content:space-between}.flow-review-workbench h4,.flow-review-workbench header p{margin:0}.flow-review-workbench header p{margin-top:4px;color:var(--muted)}.flow-chunk-row{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;align-items:start;gap:12px;padding:12px;border:1px solid var(--border);border-radius:9px}.flow-chunk-row p{margin:6px 0;line-height:1.65;white-space:pre-wrap}.flow-chunk-row small{color:var(--muted)}
.qa-content{display:grid;gap:12px}.qa-toolbar{display:flex;align-items:flex-end;justify-content:space-between;gap:20px}.qa-toolbar h3{margin:0;color:var(--text);font-size:20px}.qa-toolbar h3 span{color:var(--blue)}.qa-toolbar p{margin:5px 0 0;color:var(--muted);font-size:13px}.qa-filters{display:grid;grid-template-columns:minmax(280px,420px) 130px auto;align-items:center;margin:0}.qa-filters input,.qa-filters select{width:100%;min-width:0}.qa-table-wrap{border-radius:12px}.qa-table{min-width:920px;table-layout:fixed}.qa-question-col{width:33%}.qa-answer-col{width:41%}.qa-status-col{width:8%}.qa-time-col{width:11%}.qa-action-col{width:7%}.qa-table th{height:38px;padding:0 10px;white-space:nowrap}.qa-table td{height:42px;min-height:42px;padding:0 10px}.qa-row{cursor:pointer}.qa-row:hover td,.qa-row:focus td{background:#f6f9ff}.qa-row:focus{outline:0}.qa-row:focus-visible td:first-child{box-shadow:inset 3px 0 var(--blue)}.qa-cell{overflow:hidden;color:var(--text);font-weight:700;text-overflow:ellipsis;white-space:nowrap}.qa-cell.qa-answer{color:#59677a;font-weight:500}.qa-time{color:var(--muted);font-size:12px;white-space:nowrap}.qa-table td:last-child button{min-height:30px;padding:0 9px;white-space:nowrap}.qa-pagination{display:flex;align-items:center;justify-content:space-between;color:var(--muted);font-size:13px}.qa-pagination>div{display:flex;gap:7px}.qa-pagination button{min-height:34px}
.drawer-backdrop{position:fixed;z-index:70;inset:0;background:rgba(15,23,42,.38)}.qa-drawer{position:absolute;top:0;right:0;bottom:0;display:grid;width:min(560px,100%);grid-template-rows:auto minmax(0,1fr);background:var(--panel);box-shadow:-18px 0 48px rgba(15,23,42,.18)}.qa-drawer.text-review-drawer{width:min(1040px,100%)}.qa-drawer>header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 22px;border-bottom:1px solid var(--border)}.qa-drawer>header small{color:var(--blue);font-weight:800}.qa-drawer>header h3{margin:4px 0 0;font-size:20px}.drawer-body{display:grid;align-content:start;gap:14px;overflow-y:auto;padding:18px 22px 28px;background:var(--bg)}.text-review-layout{grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-areas:'evidence editor' 'evidence management' 'evidence review';align-items:start}.text-review-layout .evidence-section{grid-area:evidence;position:sticky;top:0;max-height:calc(100vh - 120px);overflow:auto}.text-review-layout .editor-section{grid-area:editor}.text-review-layout .management-section{grid-area:management}.text-review-layout .review-section{grid-area:review}.drawer-section{padding:17px;border:1px solid var(--border);border-radius:12px;background:#fff}.drawer-section h4{margin:0 0 12px;font-size:15px}.qa-full-content span{display:block;color:var(--blue);font-size:12px;font-weight:850}.qa-full-content p{margin:6px 0 18px;color:var(--text);font-size:15px;line-height:1.75;white-space:pre-wrap}.qa-full-content p:last-child{margin-bottom:0}.drawer-section dl{display:grid;gap:9px;margin:0}.drawer-section dl>div{display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px;padding-top:9px;border-top:1px solid #edf0f4}.drawer-section dl>div:first-child{padding-top:0;border-top:0}.drawer-section dt{color:var(--muted);font-size:13px}.drawer-section dd{min-width:0;margin:0;color:#46546a;font-size:13px}.drawer-section code{display:block;overflow-wrap:anywhere;font-size:12px}.drawer-source-list{display:grid;gap:10px}.drawer-source-list article{padding:12px;border:1px solid #e5eaf1;border-radius:9px;background:var(--panel-muted)}.drawer-source-list article>div{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.drawer-source-list b{color:var(--text)}.drawer-source-list span{flex:0 0 auto;color:var(--muted);font-size:12px}.drawer-source-list p{margin:9px 0 0;color:#59677a;font-size:13px;line-height:1.65}.drawer-section .error{margin:0}
@media(max-width:1100px){.qa-toolbar{align-items:stretch;flex-direction:column}.qa-filters{grid-template-columns:minmax(240px,1fr) 130px auto}.text-review-layout{grid-template-columns:1fr;grid-template-areas:'editor' 'review' 'evidence' 'management'}.text-review-layout .evidence-section{position:static;max-height:none}.input-cards dl{grid-template-columns:1fr 1fr}}@media(max-width:900px){.detail-head{display:grid}.detail-metrics{align-items:flex-start}}
</style>
