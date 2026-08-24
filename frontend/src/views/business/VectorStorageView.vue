<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/platform'
import {
  defaultVectorStorageFilters, formatInventoryCount, knowledgeTypeLabel, sortCollections, sortPartitions,
  vectorStatusClass, vectorStatusLabel,
} from './vectorStorageModel'

const router = useRouter()
const overview = ref(null), collections = ref([]), collectionDetails = reactive({})
const expanded = ref(new Set()), loading = ref(false), error = ref(''), notice = ref('')
const filters = reactive(defaultVectorStorageFilters())
const selectedPartition = ref(null), drawer = ref(null), drawerClose = ref(null), returnFocus = ref(null)
const gcPlan = ref(null), gcOpen = ref(false), gcConfirmed = ref(false)
const sortedCollections = computed(() => sortCollections(collections.value))

async function refresh() {
  loading.value = true; error.value = ''; notice.value = ''; collectionDetails && Object.keys(collectionDetails).forEach(key => delete collectionDetails[key]); expanded.value = new Set()
  try {
    overview.value = await api.vectorStorageOverview()
    if (overview.value.configured && overview.value.healthy) await loadCollections()
    else collections.value = []
  } catch (err) { error.value = err.message }
  finally { loading.value = false }
}
async function loadCollections() {
  error.value = ''
  try { collections.value = await api.vectorStorageCollections(filters) }
  catch (err) { collections.value = []; error.value = err.message }
}
async function toggleCollection(item) {
  const next = new Set(expanded.value)
  if (next.has(item.collection_name)) next.delete(item.collection_name)
  else {
    next.add(item.collection_name)
    try { collectionDetails[item.collection_name] = await api.vectorStorageCollection(item.collection_name) }
    catch (err) { error.value = err.message }
  }
  expanded.value = next
}
function partitionsFor(item) { return sortPartitions((collectionDetails[item.collection_name] || item).partitions || []) }
async function openPartition(collection, partition, event) {
  returnFocus.value = event?.currentTarget || null
  try {
    selectedPartition.value = await api.vectorStoragePartition(collection, partition)
    await nextTick(); drawerClose.value?.focus()
  } catch (err) { error.value = err.message }
}
function closeDrawer() { selectedPartition.value = null; nextTick(() => returnFocus.value?.focus()) }
async function refreshCollection(name) {
  collectionDetails[name] = await api.vectorStorageCollection(name)
  collections.value = collections.value.map(item => item.collection_name === name ? collectionDetails[name] : item)
}
async function verifyPartition(item) {
  notice.value = ''; error.value = ''
  try {
    const result = await api.verifyVectorPartition(item.collection_name, item.partition_name)
    notice.value = result.consistent ? '深度一致性校验通过。' : '深度一致性校验发现 count 或 digest 不一致。'
    await refreshCollection(item.collection_name)
    selectedPartition.value = await api.vectorStoragePartition(item.collection_name, item.partition_name)
  } catch (err) { error.value = err.message }
}
async function operate(item, action) {
  notice.value = ''; error.value = ''
  try {
    if (action === 'load') await api.loadVectorPartition(item.collection_name, item.partition_name)
    else await api.releaseVectorPartition(item.collection_name, item.partition_name)
    notice.value = action === 'load' ? 'Partition 已加载。' : 'Partition 已释放。'
  } catch (err) { error.value = err.message }
}
async function openGc() {
  error.value = ''; gcConfirmed.value = false
  try { gcPlan.value = await api.knowledgeAssetGcPlan(); gcOpen.value = true }
  catch (err) { error.value = err.message }
}
async function submitGc() {
  if (!gcConfirmed.value || !gcPlan.value) return
  try {
    const result = await api.createKnowledgeAssetGcJob({ execute: true, confirmation: gcPlan.value.confirmation })
    gcOpen.value = false
    notice.value = `Asset GC 任务 ${result.id} 已入队。`
    await refresh()
  } catch (err) { error.value = err.message }
}
function onDrawerKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); closeDrawer(); return }
  if (event.key !== 'Tab') return
  const focusable = [...(drawer.value?.querySelectorAll('button, a, [tabindex]:not([tabindex="-1"])') || [])].filter(item => !item.disabled)
  if (!focusable.length) return
  const first = focusable[0], last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
onMounted(refresh)
</script>

<template>
  <section class="vector-storage-page">
    <div class="page-head"><div><h2>向量存储</h2><p>查看逻辑知识资产与 Milvus Collection / Partition 的实时关系，并执行受控运维。</p></div><div class="page-actions"><button @click="openGc">清理历史资产</button><button class="primary" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新' }}</button></div></div>

    <section v-if="overview" class="vector-overview panel">
      <div><small>Milvus Target</small><b><code>{{ overview.target || '未配置' }}</code></b></div>
      <div><small>连接状态</small><b><span :class="['badge', overview.healthy ? 'green' : 'red']">{{ overview.healthy ? '● 正常' : '● 不可用' }}</span></b></div>
      <div><small>Managed Collection</small><b>{{ overview.managed_collection_count }}</b></div>
      <div><small>Collection 总数</small><b>{{ overview.collection_count }}</b></div>
      <div><small>Partition 总数</small><b>{{ overview.partition_count }}</b></div>
      <div><small>向量总数</small><b>{{ formatInventoryCount(overview.entity_count) }}</b></div>
      <div><small>异常</small><b>{{ overview.inconsistent_count }}</b></div>
      <div><small>未托管</small><b>{{ overview.unmanaged_count }}</b></div>
    </section>

    <section v-if="overview && !overview.configured" class="empty-guidance"><b>Milvus 未配置</b><p>当前实例没有配置 DATAFORGE_MILVUS_URI，无法读取 Collection / Partition 实时状态。</p></section>
    <section v-else-if="overview && !overview.healthy" class="empty-guidance"><b>Milvus 连接异常</b><p>{{ overview.error_message }}</p></section>

    <template v-else-if="overview?.healthy">
      <section class="panel vector-filters">
        <label>搜索<input v-model="filters.q" placeholder="Collection / Partition / 知识库" @keyup.enter="loadCollections"></label>
        <label>知识类型<select v-model="filters.knowledge_type" @change="loadCollections"><option value="">全部</option><option value="text">文本</option><option value="qa">问答</option><option value="graph:triple">三元组图谱</option><option value="graph:semantic">语义图谱</option></select></label>
        <label>状态<select v-model="filters.status" @change="loadCollections"><option value="">全部</option><option v-for="value in ['USING','PENDING','HISTORY','GC_ELIGIBLE','INCONSISTENT','UNMANAGED']" :key="value" :value="value">{{ vectorStatusLabel(value) }}</option></select></label>
        <label class="check"><input v-model="filters.only_managed" type="checkbox" @change="loadCollections">只看已托管</label>
        <label class="check"><input v-model="filters.only_anomaly" type="checkbox" @change="loadCollections">只看异常</label>
        <label class="check"><input v-model="filters.only_unused" type="checkbox" @change="loadCollections">只看未使用</label>
        <button @click="loadCollections">查询</button>
      </section>

      <section class="panel vector-table-panel">
        <div class="table-wrap"><table class="vector-collection-table"><thead><tr><th></th><th>Collection</th><th>类型</th><th>管理状态</th><th>Partition</th><th>向量数</th><th>维度</th><th>Metric</th><th>状态</th></tr></thead>
          <tbody v-for="item in sortedCollections" :key="item.collection_name">
            <tr><td><button class="expand-button" :aria-expanded="expanded.has(item.collection_name)" @click="toggleCollection(item)">{{ expanded.has(item.collection_name) ? '−' : '+' }}</button></td><td><code>{{ item.collection_name }}</code></td><td>{{ item.knowledge_types.map(knowledgeTypeLabel).join(' / ') || '未知' }}</td><td>{{ item.managed ? 'DataForge' : '未托管' }}</td><td>{{ item.partition_count }}</td><td>{{ formatInventoryCount(item.entity_count) }}</td><td>{{ item.dimension || '—' }}</td><td>{{ item.metric_type || '—' }}</td><td><span :class="['badge', vectorStatusClass(item.status)]">{{ vectorStatusLabel(item.status) }}</span></td></tr>
            <tr v-if="expanded.has(item.collection_name)" class="partition-container"><td colspan="9"><div class="table-wrap"><table class="partition-table"><thead><tr><th>Partition</th><th>知识库</th><th>Asset</th><th>实际数量</th><th>预期数量</th><th>Routing</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="part in partitionsFor(item)" :key="part.partition_name"><td><code>{{ part.partition_name }}</code></td><td><button v-if="part.knowledge_library_id" class="text-link" @click="router.push(`/business/knowledge/${part.knowledge_library_id}`)">{{ part.knowledge_library_name }}</button><span v-else>—</span></td><td>{{ part.asset_version_no ? `v${part.asset_version_no}` : '—' }}</td><td>{{ formatInventoryCount(part.actual_count) }}</td><td>{{ formatInventoryCount(part.expected_count) }}</td><td>{{ part.routing_ref_count }}</td><td><span :class="['badge', vectorStatusClass(part.status)]">{{ vectorStatusLabel(part.status) }}</span></td><td><button @click="openPartition(part.collection_name,part.partition_name,$event)">{{ part.asset_version_id ? '详情' : '查看' }}</button></td></tr></tbody></table></div></td></tr>
          </tbody>
          <tbody v-if="!sortedCollections.length"><tr><td colspan="9" class="empty-cell">当前筛选没有 Collection。</td></tr></tbody>
        </table></div>
      </section>
    </template>
    <p v-if="notice" class="notice">{{ notice }}</p><p v-if="error" class="error">{{ error }}</p>

    <div v-if="selectedPartition" class="drawer-backdrop" @click.self="closeDrawer">
      <aside ref="drawer" class="vector-drawer" role="dialog" aria-modal="true" aria-labelledby="vector-drawer-title" @keydown="onDrawerKeydown">
        <header><div><small>Partition</small><h3 id="vector-drawer-title">{{ selectedPartition.partition_name }}</h3></div><button ref="drawerClose" @click="closeDrawer">关闭</button></header>
        <div class="drawer-body"><section class="drawer-section"><dl><div><dt>Collection</dt><dd><code>{{ selectedPartition.collection_name }}</code></dd></div><div><dt>Knowledge Library</dt><dd><button v-if="selectedPartition.knowledge_library_id" class="text-link" @click="router.push(`/business/knowledge/${selectedPartition.knowledge_library_id}`)">{{ selectedPartition.knowledge_library_name }}</button><span v-else>—</span></dd></div><div><dt>knowledge_library_id</dt><dd><code>{{ selectedPartition.knowledge_library_id || '—' }}</code></dd></div><div><dt>AssetVersion</dt><dd>{{ selectedPartition.asset_version_no ? `v${selectedPartition.asset_version_no}` : '—' }}</dd></div><div><dt>AssetVersion 状态</dt><dd>{{ selectedPartition.asset_status || '—' }}</dd></div><div><dt>实际 / 预期向量</dt><dd>{{ formatInventoryCount(selectedPartition.actual_count) }} / {{ formatInventoryCount(selectedPartition.expected_count) }}</dd></div><div><dt>Digest</dt><dd>{{ selectedPartition.verification?.status || '未校验' }}<small v-if="selectedPartition.verification?.verified_at"> · {{ new Date(selectedPartition.verification.verified_at).toLocaleString() }}</small></dd></div><div><dt>当前引用</dt><dd>{{ selectedPartition.routing_ref_count }}</dd></div></dl></section>
          <section class="drawer-section"><h4>Routing 引用</h4><article v-for="ref in selectedPartition.routing_refs" :key="`${ref.route_version_id}-${ref.task_code}-${ref.org_code}`" class="route-reference"><b>{{ ref.project_name || ref.project_code }}</b><span>{{ ref.deployment_name || ref.deployment_code }} · {{ ref.task_code || '—' }} · {{ ref.org_code || '—' }}</span><small>RouteVersion {{ ref.route_version_no }} · {{ ref.release_stage }} · {{ ref.route_version_status }}</small></article><p v-if="!selectedPartition.routing_refs.length" class="muted">暂无 Routing 引用。</p></section>
          <section class="drawer-section"><h4>受控操作</h4><div class="actions"><button v-if="selectedPartition.actions.verify" @click="verifyPartition(selectedPartition)">一致性校验</button><button v-if="selectedPartition.actions.load" @click="operate(selectedPartition,'load')">加载 Partition</button><button v-if="selectedPartition.actions.release" @click="operate(selectedPartition,'release')">释放 Partition</button><span v-if="!selectedPartition.actions.verify" class="muted">未托管资源仅可查看。</span></div></section>
        </div>
      </aside>
    </div>

    <div v-if="gcOpen" class="menu-dialog-backdrop" @click.self="gcOpen=false"><section class="gc-dialog panel" role="dialog" aria-modal="true"><h3>清理可回收历史 Asset</h3><p>当前清单包含 {{ gcPlan?.eligible?.length || 0 }} 个 AssetVersion。任务提交后由现有 GC Worker 再次复验并删除对应版本化 Partition。</p><div class="gc-list"><code v-for="item in gcPlan?.eligible" :key="item.asset_version_id">{{ item.collection_name }}/{{ item.partition_name }}</code></div><label><input v-model="gcConfirmed" type="checkbox"> 我已核对当前全部 eligible 清单</label><div class="actions"><button @click="gcOpen=false">取消</button><button class="danger" :disabled="!gcConfirmed || !gcPlan?.eligible?.length" @click="submitGc">提交 GC 任务</button></div></section></div>
  </section>
</template>

<style scoped>
.vector-storage-page{display:grid;gap:18px}.vector-overview{display:grid;grid-template-columns:repeat(8,minmax(120px,1fr));gap:10px}.vector-overview>div{display:grid;gap:7px;padding:12px;border-radius:10px;background:var(--panel-muted)}.vector-overview small{color:var(--muted)}.vector-overview b{font-size:18px}.vector-overview code{font-size:12px}.vector-filters{display:grid;grid-template-columns:minmax(260px,2fr) repeat(2,minmax(150px,1fr)) repeat(4,auto);align-items:end;gap:10px}.vector-filters label{display:grid;gap:6px;color:var(--muted);font-size:12px}.vector-filters .check{display:flex;align-items:center;align-self:center;gap:6px;color:var(--text)}.vector-collection-table{min-width:1080px}.expand-button{width:30px;min-height:30px;padding:0}.partition-container>td{padding:12px;background:#f8faff}.partition-table{min-width:960px}.text-link{padding:0;border:0;background:transparent;color:var(--blue);font-weight:750}.drawer-backdrop{position:fixed;z-index:70;inset:0;background:rgba(15,23,42,.38)}.vector-drawer{position:absolute;top:0;right:0;bottom:0;display:grid;width:min(590px,100%);grid-template-rows:auto minmax(0,1fr);background:var(--panel);box-shadow:-18px 0 48px rgba(15,23,42,.18)}.vector-drawer>header{display:flex;align-items:center;justify-content:space-between;padding:18px 22px;border-bottom:1px solid var(--border)}.vector-drawer h3{margin:4px 0 0}.route-reference{display:grid;gap:4px;padding:10px 0;border-top:1px solid var(--border)}.route-reference:first-of-type{border-top:0}.route-reference span,.route-reference small{color:var(--muted)}.gc-dialog{width:min(680px,calc(100vw - 40px));max-height:80vh;overflow:auto}.gc-list{display:grid;gap:5px;max-height:280px;overflow:auto;margin:14px 0;padding:12px;background:var(--panel-muted)}@media(max-width:1500px){.vector-overview{grid-template-columns:repeat(4,minmax(0,1fr))}.vector-filters{grid-template-columns:2fr 1fr 1fr}}
</style>
