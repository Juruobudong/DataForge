<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, createClientRequestId } from '../../api/platform'
import { buildKnowledgeCards, filterKnowledgeLibraries, normalizeKnowledgeTypeFilter } from './knowledgeOverviewModel'

const route = useRoute(), router = useRouter()
const libraries = ref([]), types = ref([]), keyword = ref(''), error = ref(''), loading = ref(true)
const deletingId = ref('')
const activeType = computed(() => normalizeKnowledgeTypeFilter(String(route.query.type || '')))
const typeCards = computed(() => buildKnowledgeCards(libraries.value))
const activeCard = computed(() => typeCards.value.find(card => card.key === activeType.value) || null)
const visibleLibraries = computed(() => filterKnowledgeLibraries(libraries.value, activeType.value, keyword.value))
const selectedIds = ref([]), batchDeleting = ref(false), batchPublishing = ref(false), batchPublishResults = ref([])
const selectableLibraries = computed(() => visibleLibraries.value.filter(l => l.status === 'active'))
const batchPublishLibraries = computed(() => selectableLibraries.value.filter(library => library.vector_state !== 'ready'))
const allSelected = computed(() => selectableLibraries.value.length > 0 && selectableLibraries.value.every(l => selectedIds.value.includes(l.id)))
const someSelected = computed(() => !allSelected.value && selectableLibraries.value.some(l => selectedIds.value.includes(l.id)))
function toggleSelectAll(checked) { selectedIds.value = checked ? selectableLibraries.value.map(l => l.id) : [] }
watch(visibleLibraries, () => { selectedIds.value = [] })

async function load() {
  try {
    ;[libraries.value, types.value] = await Promise.all([api.knowledgeLibraries(), api.knowledgeTypes()])
    error.value = ''
  } catch (err) { error.value = err.message }
  finally { loading.value = false }
}

function selectType(key) {
  if (activeType.value === key) {
    clearType()
    return
  }
  router.push({ path: '/business/knowledge', query: { ...route.query, type: key } })
}
function clearType() {
  const query = { ...route.query }
  delete query.type
  router.push({ path: '/business/knowledge', query })
}
function openLibrary(library) { router.push(`/business/knowledge/${library.id}`) }
async function copyId(value) { await navigator.clipboard?.writeText(value) }
async function requestDelete(library) {
  if (deletingId.value) return
  deletingId.value = library.id; error.value = ''
  try {
    const check = await api.knowledgeLibraryDeleteCheck(library.id)
    if (!check.deletable) {
      const routes = check.references || []
      const jobs = check.active_job_references || []
      if (routes.length) window.alert('该知识库仍被项目路由引用。请先从项目路由移除并重新发布，再删除知识库。')
      else if (jobs.length) window.alert(`该知识库仍有 ${jobs.length} 个排队或运行中的处理任务。请等待任务结束或先停止任务，再删除知识库。`)
      else window.alert('该知识库仍被引用，暂不能删除。')
      return
    }
    const bindings = check.template_binding_references || []
    let message = '将异步清理该知识库的知识内容和 V7 Partition，确认继续？'
    if (bindings.length) {
      const sources = [...new Set(bindings.map(item => `${item.document_library_name} / ${item.template_name || item.template_code}`))]
      message += `\n\n该知识库关联：${sources.join('、')}。删除不会解除模板绑定；清理完成后，下次主动处理文档库将创建新知识库并全量重跑该模板。若模板还有其他输出，它们也会按正常 Diff 刷新。`
    }
    if (!window.confirm(message)) return
    await api.deleteKnowledgeLibrary(library.id)
    await load()
  } catch (err) { error.value = err.message }
  finally { deletingId.value = '' }
}
async function batchDelete() {
  const ids = selectedIds.value
  if (!ids.length || batchDeleting.value) return
  batchDeleting.value = true; error.value = ''
  try {
    const preflight = await api.knowledgeLibraryDeletionPreflight({ library_ids: ids })
    if (!preflight.deletable) {
      const blocked = preflight.results.filter(r => !r.deletable)
      const lines = blocked.map(r => {
        const name = libraries.value.find(l => l.id === r.knowledge_library_id)?.name || r.knowledge_library_id
        const reason = r.references?.length ? '仍被项目路由引用'
          : r.active_job_references?.length ? '仍有排队或运行中的处理任务'
          : (r.error || '暂不能删除')
        return `· ${name}：${reason}`
      })
      window.alert(`以下知识库暂不能删除，请取消勾选后再试：\n\n${lines.join('\n')}`)
      return
    }
    const withBindings = preflight.results.filter(r => (r.template_binding_references || []).length)
    let message = `将异步清理所选 ${ids.length} 个知识库的知识内容和 V7 Partition，确认继续？`
    if (withBindings.length) {
      const sources = [...new Set(withBindings.flatMap(r => (r.template_binding_references || [])
        .map(item => `${item.document_library_name} / ${item.template_name || item.template_code}`)))]
      message += `\n\n其中关联的文档库模板绑定：${sources.join('、')}。删除不会解除模板绑定；清理完成后，下次主动处理文档库将创建新知识库并全量重跑该模板。`
    }
    if (!window.confirm(message)) return
    await api.requestKnowledgeLibraryDeletions({ library_ids: ids })
    selectedIds.value = []
    await load()
  } catch (err) { error.value = err.message }
  finally { batchDeleting.value = false }
}
function errorMessage(error) { return error?.problem?.message || error?.detail || error?.message || '请求失败' }
function publishIssues(summary) {
  const issues = summary.review_required ? summary.auto_publish_issues : summary.issues
  return (issues || []).map(item => item.message || item.code).filter(Boolean).join('；') || '当前知识库不满足入库条件'
}
async function runWithConcurrency(values, action, limit = 3) {
  const results = new Array(values.length)
  let cursor = 0
  const worker = async () => {
    while (cursor < values.length) {
      const index = cursor++
      results[index] = await action(values[index])
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, values.length) }, worker))
  return results
}
async function publishAllVisibleLibraries() {
  const targets = batchPublishLibraries.value
  const readyLibraries = selectableLibraries.value.filter(library => library.vector_state === 'ready')
  if (!targets.length || batchPublishing.value) return
  batchPublishing.value = true; batchPublishResults.value = []; error.value = ''
  try {
    const preflight = await runWithConcurrency(targets, async library => {
      try {
        const summary = await api.knowledgeReviewSummary(library.id)
        const canPublish = summary.review_required
          ? summary.can_publish_after_auto_approval === true : summary.can_publish === true
        return canPublish
          ? { library, summary, status: 'ready_to_publish' }
          : { library, status: 'blocked', message: publishIssues(summary) }
      } catch (err) { return { library, status: 'blocked', message: errorMessage(err) } }
    })
    const publishable = preflight.filter(item => item.status === 'ready_to_publish')
    const autoApproveCount = publishable.reduce((total, item) => total + (
      item.summary.review_required ? (item.summary.auto_approve_pending_count || 0) : 0
    ), 0)
    const blocked = preflight.filter(item => item.status === 'blocked')
    if (!publishable.length) {
      batchPublishResults.value = [...readyLibraries.map(library => ({ library, status: 'skipped_ready' })), ...blocked]
      window.alert(`当前筛选结果中没有可提交入库的知识库。\n\n未提交 ${blocked.length} 个：\n${blocked.map(item => `· ${item.library.name}：${item.message}`).join('\n')}`)
      return
    }
    const message = [
      `将为当前筛选结果中的 ${publishable.length} 个知识库创建新的完整知识资产版本。`,
      autoApproveCount ? `其中 Text/QA 将自动通过 ${autoApproveCount} 条待审核知识。` : '',
      readyLibraries.length ? `将跳过 ${readyLibraries.length} 个 Vector Ready 知识库。` : '',
      blocked.length ? `当前有 ${blocked.length} 个知识库不满足条件，本次不会提交；其余知识库仍会继续。` : '',
      '', '确认继续？',
    ].filter(Boolean).join('\n')
    if (!window.confirm(message)) return
    const submitted = await runWithConcurrency(publishable, async item => {
      const { library, summary } = item
      try {
        await api.publishKnowledgeVectors(library.id, {
          scope: summary.scope,
          expected_snapshot_digest: summary.review_required
            ? summary.auto_approval_snapshot_digest : summary.snapshot_digest,
          idempotency_key: createClientRequestId(), approve_pending: summary.review_required === true,
        })
        return { library, status: 'submitted' }
      } catch (err) { return { library, status: 'failed', message: errorMessage(err) } }
    })
    batchPublishResults.value = [...submitted, ...readyLibraries.map(library => ({ library, status: 'skipped_ready' })), ...blocked]
    await load()
  } catch (err) { error.value = errorMessage(err) }
  finally { batchPublishing.value = false }
}
function publishResultLabel(item) {
  return ({ submitted: '已提交构建', skipped_ready: '已跳过（Vector Ready）', blocked: '未提交', failed: '提交失败' })[item.status] || item.status
}
function publishResultClass(item) { return item.status === 'submitted' ? 'green' : item.status === 'skipped_ready' ? 'blue' : 'red' }
function typeName(library) {
  if (library.graph_mode === 'triple') return '三元组图谱'
  if (library.graph_mode === 'semantic') return '语义图谱'
  return library.display_type || types.value.find(item => item.code === library.knowledge_type)?.name || library.knowledge_type
}
function sourceLibraryLabel(library) {
  const values = library.source_document_libraries || []
  return values.length ? values.map(item => item.name).join('、') : '—（迁入或手工创建）'
}
function formatTime(value) { return value ? new Date(value).toLocaleString() : '—' }
function reviewLabel(library) {
  if (!library.review_required) return '不适用'
  const counts = library.review_counts || {}
  return counts.pending ? `待审核 ${counts.pending}` : `已审核 ${(counts.approved || 0) + (counts.rejected || 0)}/${counts.total || 0}`
}
function vectorLabel(value) { return ({ not_published: '未入库', building: '构建中', ready: 'Vector Ready', stale: '有更新', failed: '失败' })[value] || '未入库' }
function vectorClass(value) { return value === 'ready' ? 'green' : value === 'failed' ? 'red' : 'amber' }
onMounted(load)
</script>

<template>
  <section class="knowledge-overview">
    <div class="page-head"><div><h2>知识库</h2><p>统一查看知识库数量、知识条数、来源与向量物理位置。</p></div></div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading" class="loading">正在加载知识库…</p>
    <template v-else>
      <section class="type-summary" aria-label="知识类型总览">
        <button v-for="card in typeCards" :key="card.key" :class="['type-card', { active: activeType === card.key }]" type="button" :aria-pressed="activeType === card.key" @click="selectType(card.key)"><span class="type-icon">{{ card.icon }}</span><span><b>{{ card.name }}</b><small>{{ card.libraryCount }} 个知识库 · {{ card.itemCount.toLocaleString() }} 条知识</small></span></button>
      </section>
      <div class="knowledge-toolbar"><div><label class="sr-only" for="knowledge-search">搜索知识库</label><input id="knowledge-search" v-model="keyword" placeholder="搜索名称、技术 ID、来源文档库或 Collection"></div><button v-if="activeType" type="button" @click="clearType">查看全部知识库</button><span v-else class="muted">未筛选时包含扩展知识类型</span></div>

      <section v-if="activeCard?.libraryCount === 0" class="empty-guidance type-empty"><span class="type-icon">{{ activeCard.icon }}</span><b>当前类型暂无知识库</b><p>该知识类型目前为 0 个知识库 · 0 条知识</p></section>
      <section v-else-if="!libraries.length" class="empty-guidance"><b>尚无知识库</b><p>请前往文档管理，为文档库绑定已发布的知识流程；处理文档后会自动生成结果知识库。</p><button class="primary" type="button" @click="router.push('/business/documents')">前往文档管理</button></section>
      <section v-else class="knowledge-list-panel">
        <div class="section-heading"><div><h3>{{ activeCard?.name || '全部知识库' }}</h3><p>{{ activeCard ? `${activeCard.libraryCount} 个知识库 · ${activeCard.itemCount.toLocaleString()} 条知识` : `共 ${libraries.length} 个知识库` }}</p></div></div>
        <div class="bulk-bar">
          <span class="badge amber">已选 {{ selectedIds.length }} 个</span>
          <button class="primary batch-publish-button" type="button" :disabled="!batchPublishLibraries.length || batchPublishing" @click="publishAllVisibleLibraries">{{ batchPublishing ? '正在提交…' : `一键审核并入库（${batchPublishLibraries.length}）` }}</button>
          <button class="danger" type="button" :disabled="!selectedIds.length || batchDeleting || batchPublishing" @click="batchDelete">{{ batchDeleting ? '删除中…' : '批量删除' }}</button>
          <button v-if="selectedIds.length" type="button" @click="selectedIds = []">取消选择</button>
        </div>
        <section v-if="batchPublishResults.length" class="batch-publish-results" aria-live="polite">
          <b>本次一键审核并入库结果</b>
          <ul><li v-for="item in batchPublishResults" :key="item.library.id"><span :class="['badge', publishResultClass(item)]">{{ publishResultLabel(item) }}</span> {{ item.library.name }}<small v-if="item.message">：{{ item.message }}</small></li></ul>
        </section>
        <div v-if="visibleLibraries.length" class="table-wrap"><table class="knowledge-table"><thead><tr><th class="select-col"><input type="checkbox" :checked="allSelected" :indeterminate.prop="someSelected" :disabled="!selectableLibraries.length" aria-label="全选当前筛选结果" @change="toggleSelectAll($event.target.checked)"></th><th>知识库名称</th><th>技术 ID</th><th>知识类型</th><th>来源文档库</th><th>知识数量</th><th>审核状态</th><th>向量状态</th><th>最近更新时间</th><th>操作</th></tr></thead><tbody><tr v-for="library in visibleLibraries" :key="library.id"><td class="select-col"><input type="checkbox" v-model="selectedIds" :value="library.id" :disabled="library.status !== 'active'"></td><td><button class="library-link" type="button" @click="openLibrary(library)">{{ library.name }}</button><span v-if="library.status === 'deleting'" class="badge amber">正在删除</span><span v-else-if="library.origin_state === 'forked'" class="badge amber">已本地修改</span><span v-else :class="['badge', library.origin_type === 'central_import' ? 'blue' : 'green']">{{ library.origin_type === 'central_import' ? '中心迁入' : '本地创建' }}</span></td><td><button class="technical-id" type="button" :title="library.id" @click="copyId(library.id)"><code>{{ library.id }}</code><small>点击复制</small></button></td><td>{{ typeName(library) }}</td><td>{{ sourceLibraryLabel(library) }}</td><td><b>{{ (library.knowledge_item_count || 0).toLocaleString() }}</b> 条</td><td><span :class="['badge', library.review_required && library.review_counts?.pending ? 'amber' : 'green']">{{ reviewLabel(library) }}</span></td><td><span v-if="library.status === 'deleting'" class="badge amber">等待清理</span><span v-else :class="['badge', vectorClass(library.vector_state)]">{{ vectorLabel(library.vector_state) }}</span></td><td>{{ formatTime(library.updated_at) }}</td><td><div class="row-actions"><button type="button" @click="openLibrary(library)">详情</button><button v-if="library.status !== 'deleting'" class="danger" type="button" :disabled="deletingId === library.id" @click="requestDelete(library)">{{ deletingId === library.id ? '检查中…' : '删除' }}</button></div></td></tr></tbody></table></div>
        <div v-else class="empty-guidance search-empty"><b>未找到匹配的知识库</b><p>请调整搜索关键词，或清除当前知识类型筛选。</p></div>
      </section>
    </template>
  </section>
</template>

<style scoped>
  .batch-publish-results{margin:0 0 12px;padding:12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel-muted)}.batch-publish-results ul{display:grid;gap:7px;margin:10px 0 0;padding:0;list-style:none}.batch-publish-results li{display:flex;gap:8px;align-items:flex-start}.batch-publish-results small{margin:0;color:var(--muted)}
  .type-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:24px}.type-card{display:flex;min-width:0;align-items:center;gap:12px;padding:18px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow);text-align:left}.type-card:hover,.type-card.active{border-color:#8fb3f5;box-shadow:0 10px 28px rgba(47,111,237,.12)}.type-card.active{background:var(--blue-soft);box-shadow:0 0 0 2px rgba(47,111,237,.12)}.type-card b,.type-card small{display:block}.type-card b{font-size:16px}.type-card small{margin-top:5px;color:var(--muted);font-size:13px}.type-icon{display:grid;width:42px;height:42px;flex:0 0 auto;place-items:center;border-radius:12px;color:var(--blue);background:var(--blue-soft);font-size:20px;font-weight:800}.knowledge-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:22px}.knowledge-toolbar>div{width:min(640px,100%)}.knowledge-toolbar input{width:100%}.knowledge-list-panel{margin-top:8px}.section-heading h3{margin:0;font-size:20px}.section-heading p{margin:6px 0 12px;color:var(--muted);font-size:13px}.bulk-bar{display:flex;align-items:center;gap:12px;padding:10px 12px;margin-bottom:12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel)}.knowledge-table{min-width:1160px}.knowledge-table th,.knowledge-table td{vertical-align:top}.knowledge-table th{white-space:nowrap}.knowledge-table th.select-col,.knowledge-table td.select-col{width:40px;text-align:center}.knowledge-table td:nth-child(2){min-width:210px}.knowledge-table td:nth-child(3){max-width:240px}.library-link{display:block;max-width:260px;padding:0;border:0;background:transparent;color:var(--text);font-weight:800;text-align:left}.library-link:hover{color:var(--blue)}.knowledge-table td:nth-child(2) .badge{display:inline-block;margin-top:8px}.technical-id{display:grid;max-width:230px;gap:3px;padding:0;border:0;background:transparent;text-align:left}.technical-id code{overflow:hidden;text-overflow:ellipsis}.technical-id small{color:var(--blue)}.row-actions{display:flex;gap:6px}.empty-guidance{display:grid;justify-items:start;gap:10px;margin:0;padding:26px;border:1px dashed var(--border);border-radius:var(--radius);color:var(--muted);background:var(--panel-muted)}.empty-guidance b{color:var(--text);font-size:17px}.empty-guidance p{margin:0}.type-empty{justify-items:center;padding:46px;text-align:center}.type-empty .type-icon{margin-bottom:4px}.search-empty{margin-top:12px}@media(max-width:1440px){.type-summary{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:900px){.type-summary{grid-template-columns:1fr}.knowledge-toolbar{align-items:stretch;flex-direction:column}.knowledge-toolbar>div{width:100%}}
</style>
