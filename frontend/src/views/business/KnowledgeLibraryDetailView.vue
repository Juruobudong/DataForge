<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'
import GraphBrowser from '../../components/GraphBrowser.vue'
import {
  normalizeQaFilters, qaApiQuery, qaPageCount, qaRouteQuery, qaStatusLabel, resetQaFilters,
} from './knowledgeDetailModel'

const route = useRoute(), router = useRouter()
const library = ref(null), items = ref([]), changes = ref([]), vector = ref(null), sources = ref([])
const deletion = ref(null), deletionJobs = ref([]), tab = ref('content'), error = ref(''), loading = ref(false)
const qaListing = ref({ items: [], total: 0, page: 1, page_size: 50 })
const qaSearch = ref(''), qaSearchDraft = ref(''), qaStatus = ref('active'), qaPage = ref(1)
const qaLoading = ref(false), qaError = ref('')
const selectedQa = ref(null), drawerSources = ref([]), drawerLoading = ref(false), drawerError = ref('')
const drawerPanel = ref(null), drawerCloseButton = ref(null)
let qaRequestId = 0, lastFocusedElement = null

const libraryId = computed(() => String(route.params.libraryId || ''))
const isGraph = computed(() => library.value?.knowledge_type === 'graph')
const isQa = computed(() => library.value?.knowledge_type === 'qa')
const qaPages = computed(() => qaPageCount(qaListing.value.total, qaListing.value.page_size || 50))
const qaHasFilters = computed(() => Boolean(qaSearch.value) || qaStatus.value !== 'active')

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
    ;[changes.value, vector.value, deletionJobs.value] = await Promise.all([
      api.changes(library.value.id), api.vectorStatus(library.value.id), api.deletionJobs(library.value.id),
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
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
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
  lastFocusedElement = null
  if (restoreFocus && focusTarget?.focus) nextTick(() => focusTarget.focus())
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
function sourceAnchor(source) {
  const anchor = source.anchor || {}
  const values = []
  if (anchor.page) values.push(`第 ${anchor.page} 页`)
  if (anchor.label || anchor.file) values.push(anchor.label || anchor.file)
  return values.join(' · ') || '来源锚点'
}

watch(libraryId, load, { immediate: true })
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
        <span v-else :class="['badge', library.vector_ready ? 'green' : 'amber']">向量 {{ library.vector_ready ? '就绪' : '未就绪' }}</span>
        <span>最近更新 {{ new Date(library.updated_at).toLocaleString() }}</span>
      </div>
      <nav class="tabs">
        <button :class="{ active: tab === 'content' }" @click="tab = 'content'">知识内容</button>
        <button :class="{ active: tab === 'diff' }" @click="tab = 'diff'">Knowledge Diff</button>
        <button :class="{ active: tab === 'vector' }" @click="tab = 'vector'">向量状态</button>
        <button v-if="!isQa" :class="{ active: tab === 'sources' }" @click="tab = 'sources'">来源追踪</button>
        <button v-if="isGraph" :class="{ active: tab === 'graph' }" @click="tab = 'graph'">图谱浏览器</button>
        <button v-if="isGraph" :class="{ active: tab === 'schema' }" @click="tab = 'schema'">图谱 Schema</button>
      </nav>

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
              <colgroup><col class="qa-question-col"><col class="qa-answer-col"><col class="qa-status-col"><col class="qa-time-col"><col class="qa-action-col"></colgroup>
              <thead><tr><th>问题</th><th>答案摘要</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-if="qaLoading"><td colspan="5" class="empty-cell">正在加载问答知识…</td></tr>
                <tr
                  v-for="item in qaListing.items" v-else :key="item.id" class="qa-row" tabindex="0"
                  :aria-label="`查看问答：${item.data.question}`" @click="openQaDrawer(item, $event)"
                  @keydown.enter="openQaDrawer(item, $event)" @keydown.space.prevent="openQaDrawer(item, $event)"
                >
                  <td><div class="qa-cell" :title="item.data.question">{{ item.data.question }}</div></td>
                  <td><div class="qa-cell qa-answer" :title="item.data.answer">{{ item.data.answer }}</div></td>
                  <td><span :class="['badge', item.status === 'active' ? 'green' : 'amber']">{{ qaStatusLabel(item.status) }}</span></td>
                  <td class="qa-time">{{ formatTime(item.updated_at) }}</td>
                  <td><button type="button" aria-label="查看完整问答" @click.stop="openQaDrawer(item, $event)">查看</button></td>
                </tr>
                <tr v-if="!qaLoading && !qaListing.items.length">
                  <td colspan="5" class="empty-cell">{{ qaHasFilters ? '没有符合当前搜索或状态条件的问答。' : '暂无有效问答知识。' }}</td>
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
          <table><thead><tr><th>内容</th><th>来源</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="item in items" :key="item.id"><td>{{ item.canonical_content }}</td><td>{{ item.source_count || item.source_version_ids?.length || 0 }}</td><td>{{ item.status }}</td><td><button @click="trace(item)">查看来源</button></td></tr><tr v-if="!items.length"><td colspan="4" class="empty-cell">暂无知识内容。</td></tr></tbody></table>
        </div>
      </template>
      <div v-else-if="tab === 'diff'" class="change-list"><article v-for="change in changes" :key="change.id"><b>{{ change.change_type }}</b><p>{{ change.before?.content || change.before_hash || '—' }} → {{ change.after?.content || change.after_hash || '—' }}</p></article><p v-if="!changes.length" class="empty-group">暂无变更记录。</p></div>
      <div v-else-if="tab === 'vector'" class="panel"><p>Vector Ready：<b>{{ vector?.ready ? '已就绪' : '未就绪' }}</b></p><pre>{{ JSON.stringify(vector?.record_states || {}, null, 2) }}</pre></div>
      <div v-else-if="tab === 'sources'" class="source-list"><article v-for="source in sources" :key="source.id"><b>{{ source.source.original_filename || source.source.name }}</b><small>{{ source.anchor.label || source.anchor.file || '来源锚点' }}</small><p>{{ source.evidence_text }}</p></article><p v-if="!sources.length" class="empty-group">从知识内容中选择“查看来源”后会在这里显示 Evidence。</p></div>
      <GraphBrowser v-else-if="tab === 'graph'" :library-id="library.id" />
      <div v-else-if="tab === 'schema'" class="panel schema-panel"><h3>图谱 Schema（只读）</h3><p class="muted">Schema 由知识流程定义，正式产出时固化到知识库；此处仅查看，不可编辑。</p><p v-if="library.source_template_revision_id">来源模板 Revision：<code>{{ library.source_template_revision_id }}</code></p><p v-if="library.graph_schema_hash">Schema Hash：<code>{{ library.graph_schema_hash }}</code></p><template v-if="library.graph_schema_snapshot"><h4>实体类型</h4><ul v-if="library.graph_schema_snapshot.entity_types?.length"><li v-for="e in library.graph_schema_snapshot.entity_types" :key="e.code"><b>{{ e.label }}</b><code>{{ e.code }}</code><span v-if="e.description"> · {{ e.description }}</span></li></ul><p v-else class="muted">未定义实体类型。</p><h4>关系类型</h4><ul v-if="library.graph_schema_snapshot.relation_types?.length"><li v-for="r in library.graph_schema_snapshot.relation_types" :key="r.code"><b>{{ r.label }}</b><code>{{ r.code }}</code><span>（{{ (r.source_types || []).join('、') || '任意' }} → {{ (r.target_types || []).join('、') || '任意' }}）</span></li></ul><p v-else class="muted">未定义关系类型。</p></template><p v-else class="muted">该知识库尚无图谱 Schema 快照（尚未正式产出）。</p></div>
      <section v-if="deletion" class="deletion-panel"><h3>删除检查</h3><p>{{ deletion.deletable ? '当前知识库可删除。' : '当前知识库仍被引用，暂不能删除。' }}</p><pre>{{ JSON.stringify(deletion, null, 2) }}</pre><button v-if="deletion.deletable" class="danger" @click="remove">确认异步删除</button></section>
      <section v-if="deletionJobs.length" class="deletion-panel"><h3>删除任务</h3><div v-for="job in deletionJobs" :key="job.id" class="deletion-job"><span>{{ job.status }}</span><small>{{ job.error || job.id }}</small><button v-if="job.status === 'failed'" @click="retryDeletion(job)">重试</button></div></section>
    </template>

    <div v-if="selectedQa" class="drawer-backdrop" @click.self="closeDrawer()">
      <aside ref="drawerPanel" class="qa-drawer" role="dialog" aria-modal="true" aria-labelledby="qa-drawer-title" @keydown="onDrawerKeydown">
        <header>
          <div><small>问答详情</small><h3 id="qa-drawer-title">完整内容与来源</h3></div>
          <button ref="drawerCloseButton" type="button" aria-label="关闭问答详情" @click="closeDrawer()">关闭</button>
        </header>
        <div class="drawer-body">
          <section class="drawer-section qa-full-content"><span>问题</span><p>{{ selectedQa.data.question }}</p><span>答案</span><p>{{ selectedQa.data.answer }}</p></section>
          <section class="drawer-section">
            <h4>管理信息</h4>
            <dl><div><dt>状态</dt><dd><span :class="['badge', selectedQa.status === 'active' ? 'green' : 'amber']">{{ qaStatusLabel(selectedQa.status) }}</span></dd></div><div><dt>更新时间</dt><dd>{{ new Date(selectedQa.updated_at).toLocaleString() }}</dd></div><div><dt>来源数量</dt><dd>{{ selectedQa.source_count || 0 }}</dd></div><div><dt>知识项 ID</dt><dd><code>{{ selectedQa.id }}</code></dd></div><div><dt>来源知识 ID</dt><dd><code>{{ selectedQa.source_knowledge_id }}</code></dd></div><div><dt>内容 Hash</dt><dd><code>{{ selectedQa.content_hash }}</code></dd></div></dl>
          </section>
          <section class="drawer-section">
            <h4>来源引用</h4>
            <p v-if="drawerLoading" class="muted">正在加载来源…</p>
            <p v-else-if="drawerError" class="error">{{ drawerError }}</p>
            <div v-else-if="drawerSources.length" class="drawer-source-list">
              <article v-for="source in drawerSources" :key="source.id"><div><b>{{ source.source.original_filename || source.source.name }}</b><span>v{{ source.source_version.version_no }} · {{ sourceAnchor(source) }}</span></div><p>{{ source.evidence_text || '暂无 Evidence 文本。' }}</p></article>
            </div>
            <p v-else class="muted">该问答暂未记录来源引用。</p>
          </section>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.back-link{margin-bottom:18px;border:0;color:var(--blue);background:transparent;padding:0;font-size:14px}.detail-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.detail-head h2{margin:6px 0 0;font-size:26px}.detail-type{color:var(--blue);font-size:14px;font-weight:800}.technical-id{min-height:0;margin-top:8px;padding:0;border:0;color:var(--muted);background:transparent;font-size:12px;font-weight:600}.detail-metrics{display:flex;flex-wrap:wrap;align-items:center;gap:16px;margin:18px 0;color:var(--muted);font-size:14px}.detail-metrics b{color:var(--text);font-size:18px}.change-list,.source-list{display:grid;gap:10px}.change-list article,.source-list article,.deletion-panel{padding:16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow)}.change-list p,.source-list p{margin:8px 0 0;line-height:1.65}.source-list b,.source-list small{display:block}.source-list small{margin-top:5px;color:var(--muted);font-size:13px}.deletion-panel{margin-top:18px}.deletion-panel h3{margin:0;font-size:16px}.deletion-panel pre{max-height:240px;margin:12px 0}.deletion-job{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)}.deletion-job small{overflow:hidden;color:var(--muted);text-overflow:ellipsis;white-space:nowrap}.empty-cell,.loading{color:var(--muted);text-align:center}.schema-panel h3{margin:0;font-size:16px}.schema-panel h4{margin:16px 0 6px;font-size:14px}.schema-panel ul{list-style:none;margin:0;padding:0}.schema-panel li{padding:7px 0;border-top:1px solid var(--border)}.schema-panel li b{margin-right:8px}.schema-panel code{font-size:12px;color:var(--muted)}
.qa-content{display:grid;gap:12px}.qa-toolbar{display:flex;align-items:flex-end;justify-content:space-between;gap:20px}.qa-toolbar h3{margin:0;color:var(--text);font-size:20px}.qa-toolbar h3 span{color:var(--blue)}.qa-toolbar p{margin:5px 0 0;color:var(--muted);font-size:13px}.qa-filters{display:grid;grid-template-columns:minmax(280px,420px) 130px auto;align-items:center;margin:0}.qa-filters input,.qa-filters select{width:100%;min-width:0}.qa-table-wrap{border-radius:12px}.qa-table{min-width:920px;table-layout:fixed}.qa-question-col{width:33%}.qa-answer-col{width:41%}.qa-status-col{width:8%}.qa-time-col{width:11%}.qa-action-col{width:7%}.qa-table th{height:38px;padding:0 10px;white-space:nowrap}.qa-table td{height:42px;min-height:42px;padding:0 10px}.qa-row{cursor:pointer}.qa-row:hover td,.qa-row:focus td{background:#f6f9ff}.qa-row:focus{outline:0}.qa-row:focus-visible td:first-child{box-shadow:inset 3px 0 var(--blue)}.qa-cell{overflow:hidden;color:var(--text);font-weight:700;text-overflow:ellipsis;white-space:nowrap}.qa-cell.qa-answer{color:#59677a;font-weight:500}.qa-time{color:var(--muted);font-size:12px;white-space:nowrap}.qa-table td:last-child button{min-height:30px;padding:0 9px;white-space:nowrap}.qa-pagination{display:flex;align-items:center;justify-content:space-between;color:var(--muted);font-size:13px}.qa-pagination>div{display:flex;gap:7px}.qa-pagination button{min-height:34px}
.drawer-backdrop{position:fixed;z-index:70;inset:0;background:rgba(15,23,42,.38)}.qa-drawer{position:absolute;top:0;right:0;bottom:0;display:grid;width:min(560px,100%);grid-template-rows:auto minmax(0,1fr);background:var(--panel);box-shadow:-18px 0 48px rgba(15,23,42,.18)}.qa-drawer>header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 22px;border-bottom:1px solid var(--border)}.qa-drawer>header small{color:var(--blue);font-weight:800}.qa-drawer>header h3{margin:4px 0 0;font-size:20px}.drawer-body{display:grid;align-content:start;gap:14px;overflow-y:auto;padding:18px 22px 28px;background:var(--bg)}.drawer-section{padding:17px;border:1px solid var(--border);border-radius:12px;background:#fff}.drawer-section h4{margin:0 0 12px;font-size:15px}.qa-full-content span{display:block;color:var(--blue);font-size:12px;font-weight:850}.qa-full-content p{margin:6px 0 18px;color:var(--text);font-size:15px;line-height:1.75;white-space:pre-wrap}.qa-full-content p:last-child{margin-bottom:0}.drawer-section dl{display:grid;gap:9px;margin:0}.drawer-section dl>div{display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px;padding-top:9px;border-top:1px solid #edf0f4}.drawer-section dl>div:first-child{padding-top:0;border-top:0}.drawer-section dt{color:var(--muted);font-size:13px}.drawer-section dd{min-width:0;margin:0;color:#46546a;font-size:13px}.drawer-section code{display:block;overflow-wrap:anywhere;font-size:12px}.drawer-source-list{display:grid;gap:10px}.drawer-source-list article{padding:12px;border:1px solid #e5eaf1;border-radius:9px;background:var(--panel-muted)}.drawer-source-list article>div{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.drawer-source-list b{color:var(--text)}.drawer-source-list span{flex:0 0 auto;color:var(--muted);font-size:12px}.drawer-source-list p{margin:9px 0 0;color:#59677a;font-size:13px;line-height:1.65}.drawer-section .error{margin:0}
@media(max-width:1100px){.qa-toolbar{align-items:stretch;flex-direction:column}.qa-filters{grid-template-columns:minmax(240px,1fr) 130px auto}}@media(max-width:900px){.detail-head{display:grid}.detail-metrics{align-items:flex-start}}
</style>
