<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/platform'
import { publicationRows, runtimeCards } from './dashboardPresentation'

const router = useRouter()
const overview = ref(null), error = ref(''), loading = ref(true)
const runCards = computed(() => runtimeCards(overview.value || {}))
const releaseRows = computed(() => publicationRows(overview.value?.publication || {}))
const productionSteps = computed(() => {
  const values = overview.value?.production || {}
  return [
    { label: '文档库', value: `${values.document_library_count || 0} 个文档库`, to: '/business/documents' },
    { label: '知识流程模板', value: `${values.active_template_binding_count || 0} 个模板绑定`, to: '/developer/flow-templates' },
    { label: '处理任务', value: `${values.active_job_count || 0} 个待处理任务`, to: '/business/jobs' },
    { label: 'Knowledge Sink', value: `${(values.knowledge_item_count || 0).toLocaleString()} 条知识`, to: '/business/knowledge' },
    { label: '向量同步', value: `${values.vector_ready_count || 0} / ${values.vector_library_count || 0} Vector Ready`, to: '/business/knowledge' },
  ]
})
async function load() {
  try { overview.value = await api.dashboardOverview(); error.value = '' }
  catch (e) { error.value = e.message }
  finally { loading.value = false }
}
function openKnowledgeType(key) { router.push({ path: '/business/knowledge', query: { type: key } }) }
onMounted(load)
</script>

<template>
  <section class="dashboard-page">
    <div class="page-head"><div><h2>工作台</h2><p>将运行状态与知识资产分层呈现，快速定位需要关注的任务与发布工作。</p></div><div class="page-actions"><span class="badge green">V7 运行中</span></div></div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading" class="loading">正在加载运行概览…</p>
    <template v-else-if="overview">
      <section class="dashboard-section" aria-labelledby="runtime-title">
        <div class="section-heading"><h3 id="runtime-title">运行概览</h3><p>只显示当前需要关注的运行状态。</p></div>
        <div class="runtime-grid"><article v-for="card in runCards" :key="card.key" class="overview-card"><span>{{ card.label }}</span><b>{{ card.value }}</b><i :class="['status-dot', card.tone]"></i></article></div>
      </section>
      <section class="dashboard-section" aria-labelledby="asset-title">
        <div class="section-heading"><h3 id="asset-title">知识资产概览</h3><p>固定知识类型统一使用“知识库数量 · 知识条数”口径。</p></div>
        <div class="asset-grid"><button v-for="asset in overview.knowledge_assets" :key="asset.key" class="asset-card" type="button" @click="openKnowledgeType(asset.key)"><span class="asset-icon">{{ asset.icon }}</span><span><b>{{ asset.label }}</b><small>{{ asset.library_count }} 个知识库 · {{ asset.knowledge_item_count.toLocaleString() }} 条知识</small></span><span class="card-arrow">→</span></button></div>
      </section>
      <div class="dashboard-lower-grid">
        <section class="panel production-panel"><div class="panel-head"><div><h3>知识生产主链</h3><p>从文档进入受控流程，最终形成正式知识并完成向量同步。</p></div></div><div class="production-flow"><template v-for="(step, index) in productionSteps" :key="step.label"><button class="production-node" type="button" @click="router.push(step.to)"><b>{{ step.label }}</b><small>{{ step.value }}</small></button><span v-if="index < productionSteps.length - 1" class="flow-arrow">→</span></template></div></section>
        <section class="panel publication-panel"><div class="panel-head"><div><h3>{{ overview.publication.mode === 'import' ? '机构导入状态' : '机构发布状态' }}</h3><p>{{ overview.publication.mode === 'import' ? '导入等待与恢复独立于知识生产主链。' : '冻结、构建与失败状态独立于知识生产主链。' }}</p></div></div><div class="publication-list"><div v-for="row in releaseRows" :key="row.status"><span><i :class="['status-dot', row.tone]"></i>{{ row.label }}</span><b>{{ row.count }}</b></div></div><button type="button" @click="router.push('/institution-deployments/new')">打开机构发布部署</button></section>
      </div>
    </template>
  </section>
</template>

<style scoped>
.dashboard-section{margin-top:28px}.section-heading h3{margin:0;font-size:20px}.section-heading p{margin:6px 0 14px;color:var(--muted);font-size:13px}.runtime-grid,.asset-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.overview-card{position:relative;min-width:0;padding:18px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow)}.overview-card span,.overview-card b{display:block}.overview-card span{color:var(--muted);font-size:13px}.overview-card b{margin-top:9px;padding-right:16px;font-size:18px}.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#9aa7b7}.overview-card>.status-dot{position:absolute;top:20px;right:18px}.status-dot.green{background:#1f9d72}.status-dot.blue{background:var(--blue)}.status-dot.amber{background:#d8941b}.status-dot.red{background:#c94a4a}.asset-card{display:grid;grid-template-columns:42px minmax(0,1fr) 18px;gap:12px;align-items:center;min-width:0;padding:18px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow);text-align:left}.asset-card:hover{border-color:#b9cff7;box-shadow:0 10px 28px rgba(47,111,237,.12)}.asset-icon{display:grid;width:42px;height:42px;place-items:center;border-radius:12px;color:var(--blue);background:var(--blue-soft);font-size:20px;font-weight:800}.asset-card b,.asset-card small{display:block}.asset-card b{font-size:16px}.asset-card small{margin-top:5px;color:var(--muted);font-size:13px}.card-arrow{color:var(--blue);font-weight:800}.dashboard-lower-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:16px;margin-top:28px}.production-flow{display:grid;grid-template-columns:minmax(110px,1fr) 20px minmax(110px,1fr) 20px minmax(110px,1fr) 20px minmax(110px,1fr) 20px minmax(110px,1fr);align-items:stretch;gap:6px;overflow-x:auto;padding:8px 0 4px}.production-node{display:grid;min-height:92px;align-content:center;gap:8px;padding:14px;border:1px solid var(--border);border-radius:12px;background:var(--panel-muted);text-align:left}.production-node:hover{border-color:#b9cff7;background:var(--blue-soft)}.production-node b{font-size:14px}.production-node small{color:var(--muted);font-size:12px}.flow-arrow{display:grid;place-items:center;color:#8fa0b5;font-size:18px}.publication-list{display:grid;gap:8px;margin:8px 0 16px}.publication-list div{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-radius:9px;background:var(--panel-muted)}.publication-list span{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}.publication-list b{font-size:16px}@media(max-width:1100px){.runtime-grid,.asset-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.dashboard-lower-grid{grid-template-columns:1fr}}@media(max-width:720px){.runtime-grid,.asset-grid{grid-template-columns:1fr}.production-flow{grid-template-columns:1fr}.flow-arrow{transform:rotate(90deg)}.overview-card b{font-size:16px}}
</style>
