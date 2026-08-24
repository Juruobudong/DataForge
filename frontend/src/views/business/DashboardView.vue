<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/platform'
import { publicationRows, runtimeCards } from './dashboardPresentation'
import { componentAge, componentTone, needsRealCallConfirmation } from './componentObservability'

const router = useRouter()
const overview = ref(null), error = ref(''), loading = ref(true), selected = ref([]), checkRun = ref(null)
let pollTimer = null
const runCards = computed(() => runtimeCards(overview.value || {}))
const releaseRows = computed(() => publicationRows(overview.value?.publication || {}))
const components = computed(() => overview.value?.observability?.components || [])
const checking = computed(() => checkRun.value?.status === 'running')
const productionSteps = computed(() => {
  const values = overview.value?.production || {}
  return [
    { label: '文档库', value: `${values.document_library_count || 0} 个文档库`, to: '/business/documents' },
    { label: '人工审核 Gate', value: 'SourceChunk 批准后放行', to: '/business/documents' },
    { label: '知识流程模板', value: `${values.active_template_binding_count || 0} 个模板绑定`, to: '/developer/flow-templates' },
    { label: 'LLM / Operator', value: `${values.active_job_count || 0} 个待处理任务`, to: '/business/jobs' },
    { label: 'Knowledge Sink', value: `${(values.knowledge_item_count || 0).toLocaleString()} 条知识`, to: '/business/knowledge' },
    { label: 'Embedding / Milvus', value: `${values.vector_ready_count || 0} / ${values.vector_library_count || 0} 已同步`, to: '/business/vector-storage' },
    { label: 'Ready / Routing', value: '项目发布', to: '/business/authorization' },
  ]
})
async function load() { try { overview.value = await api.dashboardOverview(); error.value = '' } catch (e) { error.value = e.message } finally { loading.value = false } }
function openKnowledgeType(key) { router.push({ path: '/business/knowledge', query: { type: key } }) }
function selectAll() { selected.value = components.value.map(item => item.component) }
async function startCheck(values = selected.value) {
  if (!values.length || checking.value) return
  if (needsRealCallConfirmation(values) && !window.confirm('所选检查会真实调用 MinerU、LLM 或 Embedding，可能需要数十秒。继续吗？')) return
  try { checkRun.value = await api.createComponentCheck(values); await pollCheck() } catch (e) { error.value = e.message }
}
async function pollCheck() {
  checkRun.value = await api.componentCheckRun(checkRun.value.id); await load()
  if (checking.value) pollTimer = window.setTimeout(pollCheck, 1000)
}
onMounted(load)
onUnmounted(() => { if (pollTimer) window.clearTimeout(pollTimer) })
</script>

<template><section class="dashboard-page">
  <div class="page-head"><div><h2>工作台</h2><p>将运行状态与知识资产分层呈现，快速定位需要关注的任务与发布工作。</p></div><span class="badge green">V7 运行中</span></div>
  <p v-if="error" class="error">{{ error }}</p><p v-else-if="loading" class="loading">正在加载运行概览…</p>
  <template v-else-if="overview">
    <section class="dashboard-section"><div class="section-heading"><h3>运行概览</h3><p>只显示当前需要关注的运行状态。</p></div><div class="runtime-grid"><article v-for="card in runCards" :key="card.key" class="overview-card"><span>{{ card.label }}</span><b>{{ card.value }}</b><i :class="['status-dot', card.tone]"></i></article></div></section>
    <section class="dashboard-section"><div class="section-heading"><h3>知识资产概览</h3><p>固定知识类型统一使用“知识库数量 · 知识条数”口径。</p></div><div class="asset-grid"><button v-for="asset in overview.knowledge_assets" :key="asset.key" class="asset-card" @click="openKnowledgeType(asset.key)"><span class="asset-icon">{{ asset.icon }}</span><span><b>{{ asset.label }}</b><small>{{ asset.library_count }} 个知识库 · {{ asset.knowledge_item_count.toLocaleString() }} 条知识</small></span><span>→</span></button></div></section>
    <section class="dashboard-section"><div class="section-heading component-heading"><div><h3>系统组件</h3><p>Worker/Runner 自动心跳；其他组件仅在点击检查后执行真实探针。</p></div><div class="component-actions"><button @click="selectAll">全选</button><button @click="selected=[]">取消全选</button><button class="primary" :disabled="!selected.length || checking" @click="startCheck()">{{ checking ? '检查中…' : '检查选中项' }}</button></div></div>
      <div class="component-grid"><article v-for="item in components" :key="item.component" class="component-card"><label><input v-model="selected" type="checkbox" :value="item.component">{{ item.label }}</label><span :class="['badge', componentTone(item.status)]">{{ item.status }}</span><b>{{ item.summary }}</b><small>{{ item.latency_ms != null ? `${item.latency_ms} ms · ` : '' }}{{ componentAge(item) }}</small><button :disabled="checking" @click="startCheck([item.component])">仅检查此项</button></article></div>
      <div v-if="checkRun" class="check-progress"><b>本次检查：{{ checkRun.status }}</b><span v-for="item in checkRun.results" :key="item.component">{{ item.component }}：{{ item.status }}</span></div>
      <div v-if="overview.observability?.diagnoses?.length" class="diagnosis-list"><div v-for="item in overview.observability.diagnoses" :key="item.component"><b>{{ item.message }}</b><span>受影响任务 {{ item.affected_jobs }} 个</span></div></div>
    </section>
    <div class="dashboard-lower-grid"><section class="panel"><h3>知识生产主链</h3><div class="production-flow"><template v-for="(step,index) in productionSteps" :key="step.label"><button class="production-node" @click="router.push(step.to)"><b>{{ step.label }}</b><small>{{ step.value }}</small></button><span v-if="index<productionSteps.length-1" class="flow-arrow">→</span></template></div></section><section class="panel"><h3>{{ overview.publication.mode === 'import' ? '机构导入状态' : '机构发布状态' }}</h3><div class="publication-list"><div v-for="row in releaseRows" :key="row.status"><span><i :class="['status-dot', row.tone]"></i>{{ row.label }}</span><b>{{ row.count }}</b></div></div></section></div>
  </template>
</section></template>

<style scoped>
.dashboard-section{margin-top:28px}.section-heading h3{margin:0;font-size:20px}.section-heading p{margin:6px 0 14px;color:var(--muted);font-size:13px}.runtime-grid,.asset-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.overview-card,.component-card,.asset-card{padding:18px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow)}.overview-card{position:relative}.overview-card span,.overview-card b{display:block}.overview-card span,.component-card small{color:var(--muted)}.overview-card b{margin-top:9px}.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#9aa7b7}.overview-card>.status-dot{position:absolute;top:20px;right:18px}.status-dot.green{background:#1f9d72}.status-dot.blue{background:var(--blue)}.status-dot.amber{background:#d8941b}.status-dot.red{background:#c94a4a}.component-heading{display:flex;justify-content:space-between;gap:16px}.component-actions{display:flex;align-items:center;gap:8px}.component-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.component-card{display:grid;grid-template-columns:1fr auto;gap:10px}.component-card label{font-weight:700}.component-card b,.component-card small,.component-card button{grid-column:1/-1}.component-card button{justify-self:start}.check-progress{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px;padding:12px;border-radius:10px;background:var(--panel-muted)}.diagnosis-list{display:grid;gap:8px;margin-top:12px}.diagnosis-list div{display:flex;justify-content:space-between;padding:12px;border-radius:10px;background:#fff5e5;color:#8a5b06}.asset-card{display:grid;grid-template-columns:42px 1fr 18px;gap:12px;align-items:center;text-align:left}.asset-icon{display:grid;width:42px;height:42px;place-items:center;border-radius:12px;color:var(--blue);background:var(--blue-soft);font-size:20px}.asset-card b,.asset-card small{display:block}.asset-card small{margin-top:5px;color:var(--muted)}.dashboard-lower-grid{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-top:28px}.production-flow{display:flex;align-items:stretch;gap:6px;overflow:auto}.production-node{min-width:130px;padding:14px;border:1px solid var(--border);border-radius:12px;background:var(--panel-muted);text-align:left}.production-node b,.production-node small{display:block}.production-node small{margin-top:8px;color:var(--muted)}.flow-arrow{display:grid;place-items:center}.publication-list div{display:flex;justify-content:space-between;padding:10px;background:var(--panel-muted)}@media(max-width:1100px){.runtime-grid,.asset-grid,.component-grid{grid-template-columns:repeat(2,1fr)}.dashboard-lower-grid{grid-template-columns:1fr}}
</style>
