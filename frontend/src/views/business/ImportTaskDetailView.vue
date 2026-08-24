<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api/platform'
import { activationCanRun } from './institutionReleaseModel'

const route = useRoute()
const job = ref(null), candidates = ref([]), configs = ref([]), preflight = ref(null)
const selectedTarget = ref('candidate_target'), error = ref(''), batch = ref(null), verifying = ref(false)
const stages = ['uploaded','verified','contracts_metadata_imported','documents_objects_imported','waiting_for_milvus_configuration','waiting_for_milvus_verification','waiting_for_vector_capacity','ready_for_vector_import','importing_vectors','verified_vectors','assets_ready','route_candidates_ready','completed']
const currentIndex = computed(() => stages.indexOf(job.value?.stage))
const packageAdmission = computed(() => job.value?.checkpoint?.package_admission || {})
const partitionRows = computed(() => preflight.value?.partitions || job.value?.items || [])
const canActivate = computed(() => activationCanRun(preflight.value))
const prepareSummary = computed(() => ({
  collections: new Set((job.value?.items || []).map(item => item.collection_name)).size,
  partitions: (job.value?.items || []).length,
  sourceRows: (job.value?.items || []).reduce((sum, item) => sum + Number(item.source_count || 0), 0),
  targetRows: (job.value?.items || []).reduce((sum, item) => sum + Number(item.target_count || 0), 0),
}))

async function verifyActivation() {
  if (!job.value || job.value.package_kind === 'knowledge_update' || job.value.status !== 'completed') return
  try {
    verifying.value = true
    preflight.value = await api.migrationActivationPreflight(job.value.id)
  } catch (e) { error.value = e.message } finally { verifying.value = false }
}
async function load() {
  try {
    error.value = ''
    ;[job.value, candidates.value, configs.value] = await Promise.all([
      api.migrationJob(route.params.jobId), api.importedRouteCandidates(route.params.jobId), api.localMilvusConfigurations(),
    ])
    await verifyActivation()
  } catch (e) { error.value = e.message }
}
async function resume() { try { await api.resumeMigration(job.value.id, { selected_import_target: selectedTarget.value }); await load() } catch (e) { error.value = e.message } }
async function retry() { try { await api.retryMigration(job.value.id); await load() } catch (e) { error.value = e.message } }
async function activate(candidate) {
  if (!window.confirm('本操作不会重新导入文档、知识或向量，只使 Routing 配置生效。')) return
  try { await api.activateImportedRouteCandidate(candidate.id); await load() } catch (e) { error.value = e.message }
}
async function activateAll() {
  if (!window.confirm(`本次将使 ${preflight.value?.summary?.ready_candidates || 0} 个项目 Routing 生效。\n\n文档、知识、Collection 和 Partition 已在 Prepare 阶段完成。\n本操作不会重新导入任何数据。`)) return
  try { batch.value = await api.activateMigrationReadyRoutes(job.value.id); await load() } catch (e) { error.value = e.message }
}
onMounted(load)
</script>

<template>
  <section>
    <div class="page-head"><div><h2>机构 Release 详情</h2><p><code>{{ job?.id }}</code> · Import completed 只表示 Prepare 完成，Routing 需独立激活。</p></div><span class="badge" :class="job?.status==='completed'?'green':job?.status==='failed'?'red':'amber'">{{ job?.status }}</span></div>
    <section class="panel">
      <div class="panel-head"><div><h3>Package</h3><p>静态准入在写入业务数据前完成。</p></div><span class="badge" :class="job?.signature_status==='verified'?'green':'red'">{{ job?.signature_status }}</span></div>
      <table><tbody><tr><th>Signature</th><td>{{ packageAdmission.signature || job?.signature_status }}</td><th>Checksum</th><td>{{ packageAdmission.checksum || (job?.package_sha256 ? 'verified' : 'unknown') }}</td></tr><tr><th>Package Kind</th><td>{{ packageAdmission.package_kind || job?.package_kind }}</td><th>Schema</th><td>v{{ packageAdmission.manifest_schema_version || '-' }}</td></tr><tr><th>Institution</th><td>{{ packageAdmission.deployment?.institution_name || packageAdmission.deployment?.name || '-' }}</td><th>Base Release</th><td><code>{{ packageAdmission.base_release_id || '无' }}</code></td></tr></tbody></table>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h3>数据准备（Prepare）</h3><p>文档、知识、对象和向量在本阶段完成；激活不会重复执行。</p></div><span class="badge" :class="job?.status==='completed'?'green':'amber'">{{ job?.stage }}</span></div>
      <div class="metrics"><div><b>{{ job?.checkpoint?.objects_imported ? '✓' : '—' }}</b><span>文档</span></div><div><b>{{ job?.checkpoint?.metadata_imported ? '✓' : '—' }}</b><span>正式知识</span></div><div><b>{{ job?.checkpoint?.objects_imported ? '✓' : '—' }}</b><span>Objects</span></div><div><b>{{ prepareSummary.collections }}</b><span>Collections</span></div><div><b>{{ prepareSummary.partitions }}</b><span>Partitions</span></div><div><b>{{ prepareSummary.targetRows }}/{{ prepareSummary.sourceRows }}</b><span>Vector Rows</span></div></div>
      <div v-if="job?.status==='waiting'" class="notice"><p>当前运行路由保持不变。补充并验证配置后可从此检查点继续。</p><label>向量导入目标<select v-model="selectedTarget"><option v-for="item in configs.filter(value=>['current_target','candidate_target'].includes(value.slot))" :key="item.slot" :value="item.slot">{{ item.slot }} · {{ item.status }} · {{ item.uri }}</option></select></label><button class="primary" @click="resume">继续导入</button></div>
      <p v-if="job?.error" class="error">{{ job.error }}</p><button v-if="job?.status==='failed'" @click="retry">从最近检查点重试</button>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h3>Partition 验证矩阵</h3><p>重新读取 Milvus，并比较 Prepare 保存的 source/target count 与 digest。</p></div><button :disabled="verifying||job?.status!=='completed'" @click="verifyActivation">{{ verifying ? '验证中…' : '重新验证' }}</button></div>
      <table><thead><tr><th>知识库</th><th>Collection</th><th>Partition</th><th>包内条数</th><th>Milvus 条数</th><th>Digest</th><th>状态</th></tr></thead><tbody><tr v-for="item in partitionRows" :key="`${item.collection_name}-${item.partition_name}`"><td>{{ item.knowledge_library_name || item.knowledge_library_id }}</td><td><code>{{ item.collection_name }}</code></td><td><code>{{ item.partition_name }}</code></td><td>{{ item.source_count }}</td><td>{{ item.target_count }}</td><td>{{ item.source_digest && item.source_digest===item.target_digest ? '一致' : '不一致' }}</td><td><span class="badge" :class="item.status==='verified'?'green':'red'">{{ item.status }}</span><small v-if="item.error" class="error">{{ item.error }}</small></td></tr></tbody></table>
    </section>
    <section v-if="job?.package_kind!=='knowledge_update'" class="panel">
      <div class="panel-head"><div><h3>Activation Preflight</h3><p>必须通过 target、资产、候选和全部 Partition 门禁。</p></div><span class="badge" :class="preflight?.ready?'green':'red'">{{ preflight?.blocked ?? '-' }} Blocked</span></div>
      <div class="metrics"><div><b>{{ preflight?.target?.reachable ? '✓' : '✗' }}</b><span>Milvus</span></div><div><b>{{ preflight?.summary?.verified_partitions||0 }}/{{ preflight?.summary?.partitions||0 }}</b><span>Partitions</span></div><div><b>{{ preflight?.summary?.target_rows||0 }}/{{ preflight?.summary?.source_rows||0 }}</b><span>Rows</span></div><div><b>{{ preflight?.summary?.ready_candidates||0 }}/{{ preflight?.summary?.candidates||0 }}</b><span>Candidates</span></div></div>
      <table><thead><tr><th>检查</th><th>状态</th><th>Expected</th><th>Observed</th></tr></thead><tbody><tr v-for="check in preflight?.checks||[]" :key="`${check.code}-${JSON.stringify(check.subject)}`"><td><code>{{ check.code }}</code></td><td><span class="badge" :class="check.status==='passed'?'green':'red'">{{ check.status }}</span></td><td><code>{{ JSON.stringify(check.expected) }}</code></td><td><code>{{ JSON.stringify(check.observed) }}</code></td></tr></tbody></table>
      <div class="panel-head"><div><h3>项目候选激活</h3><p>默认逐项目；批量按顺序非原子执行。</p></div><button class="primary" :disabled="!canActivate" @click="activateAll">批量激活 {{ preflight?.summary?.ready_candidates||0 }} 个项目</button></div>
      <table><thead><tr><th>ProjectDeployment</th><th>源 Route</th><th>资产</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in candidates" :key="item.id"><td>{{ item.project_deployment_id }}</td><td>v{{ item.source_route_version }}</td><td>{{ item.readiness.ready_asset_version_ids?.length||0 }}/{{ item.readiness.asset_version_ids?.length||0 }}</td><td><span class="badge" :class="item.status==='ready'||item.status==='activated'?'green':'red'">{{ item.status }}</span></td><td><button v-if="item.status==='ready'" :disabled="!preflight?.ready" @click="activate(item)">激活此项目</button></td></tr></tbody></table>
      <pre v-if="batch">{{ JSON.stringify(batch,null,2) }}</pre>
    </section>
    <details class="panel"><summary>运行详情</summary><ol class="timeline"><li v-for="(stage,index) in stages" :key="stage" :class="{done:index<=currentIndex,failed:job?.status==='failed'&&index===currentIndex}"><b>{{ stage }}</b><span>{{ index<currentIndex?'已完成':index===currentIndex?'当前':'待执行' }}</span></li></ol></details>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>
