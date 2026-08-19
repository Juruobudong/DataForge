<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/platform'

const PACKAGE_KIND_LABELS = {
  deployment_seed: 'Deployment Seed（首次初始化包）',
  knowledge_update: 'Knowledge Update（后续知识更新包）',
}
const PACKAGE_KIND_HINTS = {
  deployment_seed: '用于首次初始化本地 Deployment，包含 Project、授权、Routing 基线及所选知识资产。',
  knowledge_update: '用于已完成首次 Seed 的本地环境，仅更新所选知识及其必要依赖，不修改本地授权或 Routing。',
}
const instance = ref(null), projects = ref([]), libraries = ref([]), jobs = ref([])
const projectId = ref(''), deploymentId = ref(''), packageKind = ref('deployment_seed'), selected = ref([])
const scopeMode = ref('authorization'), includeFull = ref(false), plan = ref(null), upload = ref(null)
const inspected = ref(null), resolutions = ref({}), error = ref('')
const local = computed(() => instance.value?.instance_mode === 'local')
const deployments = computed(() => (projects.value.find(item => item.id === projectId.value)?.deployments || [])
  .filter(item => item.scope === 'institution' && item.code !== 'dataforge-central'))
const packageKindHint = computed(() => PACKAGE_KIND_HINTS[packageKind.value] || '')
function packageKindLabel(kind) { return PACKAGE_KIND_LABELS[kind] || kind || '—' }
function ensureTargetDeployment() {
  if (!deployments.value.some(item => item.id === deploymentId.value)) {
    deploymentId.value = deployments.value[0]?.id || ''
  }
}
function requireTargetDeployment() {
  if (deploymentId.value) return true
  error.value = '请先在项目发布页手动新增并绑定目标机构 Deployment。'
  return false
}
async function load() {
  try {
    instance.value = await api.instance()
    if (instance.value.instance_mode !== 'local' || instance.value.bound_deployment_id) {
      ;[projects.value, libraries.value] = await Promise.all([api.projects(), api.knowledgeLibraries()])
    }
    projectId.value ||= projects.value[0]?.id || ''
    if (local.value) deploymentId.value = instance.value.bound_deployment_id
    else ensureTargetDeployment()
    jobs.value = await api.migrations(local.value ? 'import' : 'export')
  } catch (e) { error.value = e.message }
}
function payload() { return { project_deployment_id: deploymentId.value, knowledge_library_ids: scopeMode.value === 'manual' ? selected.value : null, package_kind: packageKind.value, include_full_document_library: includeFull.value } }
async function preview() { if (!requireTargetDeployment()) return; try { plan.value = await api.migrationPlan(payload()) } catch (e) { error.value = e.message } }
async function generate() { if (!requireTargetDeployment()) return; try { await api.exportMigration(payload()); await load() } catch (e) { error.value = e.message } }
async function inspect() { try { const form = new FormData(); form.append('file', upload.value.files[0]); inspected.value = await api.inspectMigration(form); for (const id of Object.keys(inspected.value.conflicts || {})) resolutions.value[id] = 'keep_local'; await load() } catch (e) { error.value = e.message } }
async function importPackage() { try { await api.importMigration({ job_id: inspected.value.id, conflict_resolutions: resolutions.value }); await load() } catch (e) { error.value = e.message } }
async function retry(job) { try { await api.retryMigration(job.id); await load() } catch (e) { error.value = e.message } }
watch(projectId, () => {
  if (!local.value) {
    ensureTargetDeployment()
    plan.value = null
  }
})
onMounted(load)
</script>

<template>
  <section><div class="page-head"><div><h2>知识库迁移</h2><p>{{ local ? '导入中心 Seed 或自包含知识更新包。' : '生成唯一 Deployment 的离线知识资产包。' }}</p></div><span class="badge blue">{{ local ? instance?.instance_code : 'Central' }}</span></div>
    <section v-if="!local" class="panel"><div class="panel-head"><div><h3>新建本地化迁移</h3><p>Collection 与 Partition 由冻结 Profile 自动计算，只读展示。</p></div></div><form class="stack" @submit.prevent="preview"><label>项目<select v-model="projectId"><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select></label><label>目标机构 Deployment<select v-model="deploymentId" :disabled="!deployments.length" required><option v-if="!deployments.length" disabled value="">请先在项目发布页手动新增并绑定目标机构</option><option v-for="deployment in deployments" :key="deployment.id" :value="deployment.id">{{ deployment.name }}</option></select><small class="muted">仅显示手动配置的目标机构，不包含 DataForge 中心环境。</small></label><label>包类型<select v-model="packageKind"><option value="deployment_seed">{{ packageKindLabel('deployment_seed') }}</option><option value="knowledge_update">{{ packageKindLabel('knowledge_update') }}</option></select><small class="muted">{{ packageKindHint }}</small></label><label>迁移范围<select v-model="scopeMode"><option value="authorization">按当前授权</option><option value="manual">手工选择知识库</option></select></label><label v-if="scopeMode==='manual'">知识库<select v-model="selected" multiple><option v-for="library in libraries" :key="library.id" :value="library.id">{{ library.name }}</option></select></label><label><input v-model="includeFull" type="checkbox"> 迁移整个关联文档库（高级）</label><div class="actions"><button>预检查</button><button type="button" class="primary" :disabled="!plan" @click="generate">生成迁移包</button></div></form><pre v-if="plan">{{ JSON.stringify(plan,null,2) }}</pre></section>
    <section v-else class="panel"><h3>导入迁移包</h3><form @submit.prevent="inspect"><input ref="upload" type="file" accept=".dfm" required><button>检查签名与范围</button></form><template v-if="inspected"><pre>{{ JSON.stringify(inspected,null,2) }}</pre><div v-for="(_, libraryId) in inspected.conflicts" :key="libraryId"><label>{{ libraryId }}<select v-model="resolutions[libraryId]"><option value="keep_local">保留本地</option><option value="replace_with_central">使用中心新版</option><option value="import_as_new">另存为新知识库</option></select></label></div><button class="primary" @click="importPackage">开始导入</button></template></section>
    <section class="panel"><h3>{{ local ? '导入记录' : '迁移记录' }}</h3><table><thead><tr><th>时间</th><th>类型</th><th>阶段</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="job in jobs" :key="job.id"><td>{{ job.created_at }}</td><td>{{ packageKindLabel(job.package_kind) }}</td><td>{{ job.stage }}</td><td><span class="badge" :class="job.status==='completed'||job.status==='ready'?'green':job.status==='failed'?'red':'amber'">{{ job.status }}</span></td><td><a v-if="job.status==='ready'&&!local" :href="api.migrationPackageUrl(job.id)">下载</a><button v-if="job.status==='failed'" @click="retry(job)">重试</button></td></tr></tbody></table></section><p v-if="error" class="error">{{ error }}</p>
  </section>
</template>
