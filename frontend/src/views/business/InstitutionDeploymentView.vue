<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'

const route = useRoute(), router = useRouter()
const instance = ref(null), deployments = ref([]), projects = ref([]), libraries = ref([])
const releases = ref([]), jobs = ref([]), frozenRoutes = ref([])
const deploymentId = ref(''), packageKind = ref('deployment_seed'), selectedRoutes = ref([]), selectedLibraries = ref([])
const includeFullDocuments = ref(false)
const overrideUri = ref(''), overrideReason = ref('')
const step = ref(1), draft = ref(null), plan = ref(null), release = ref(null), upload = ref(null)
const inspected = ref(null), resolutions = ref({}), error = ref(''), busy = ref(false)
const local = computed(() => instance.value?.instance_mode === 'local')
let saveTimer = null
const institutionDeployments = computed(() => deployments.value.filter(item => item.scope === 'institution'))
const selectedDeployment = computed(() => institutionDeployments.value.find(item => item.id === deploymentId.value))
const updateOnly = computed(() => packageKind.value === 'knowledge_update')

function kindLabel(kind) {
  return ({ deployment_seed: '首次部署 Seed', institution_release: '机构多项目发布', knowledge_update: '知识资产更新' })[kind] || kind
}
function statusClass(status) {
  if (['ready', 'completed', 'frozen'].includes(status)) return 'green'
  if (['failed', 'conflict'].includes(status)) return 'red'
  return 'amber'
}
async function loadFrozenRoutes() {
  frozenRoutes.value = []
  selectedRoutes.value = []
  if (!deploymentId.value || local.value) return
  const bindings = projects.value.flatMap(project => (project.deployments || [])
    .filter(binding => binding.deployment_id === deploymentId.value)
    .map(binding => ({ project, binding })))
  const rows = await Promise.all(bindings.map(async ({ project, binding }) => ({
    project, binding, versions: await api.routeVersions(binding.id, binding.release_stage || 'test'),
  })))
  frozenRoutes.value = rows.flatMap(row => row.versions.filter(version => version.status === 'frozen')
    .map(version => ({ ...version, project: row.project, binding: row.binding })))
}
async function load() {
  try {
    error.value = ''
    instance.value = await api.instance()
    if (local.value) {
      jobs.value = await api.migrations('import')
    } else {
      ;[deployments.value, projects.value, libraries.value, releases.value, jobs.value] = await Promise.all([
        api.sharedDeployments(), api.projects(), api.knowledgeLibraries(), api.institutionReleases(), api.migrations('export'),
      ])
      deploymentId.value ||= institutionDeployments.value[0]?.id || ''
      await loadFrozenRoutes()
    }
    if (route.params.draftId) {
      draft.value = await api.institutionReleaseDraft(route.params.draftId)
      deploymentId.value = draft.value.target_deployment_id
      packageKind.value = draft.value.package_kind
      selectedRoutes.value = draft.value.selection?.route_version_ids || []
      selectedLibraries.value = draft.value.selection?.knowledge_library_ids || []
      includeFullDocuments.value = Boolean(draft.value.selection?.include_full_document_library)
      overrideUri.value = draft.value.milvus_override?.uri || ''
      overrideReason.value = draft.value.milvus_override_reason || ''
      await loadFrozenRoutes()
      selectedRoutes.value = draft.value.selection?.route_version_ids || []
      await refreshPlan()
    }
    if (route.params.releaseId) {
      release.value = await api.institutionRelease(route.params.releaseId)
      plan.value = release.value.snapshot
      step.value = 4
    }
  } catch (e) { error.value = e.message }
}
async function createDraft() {
  try {
    busy.value = true; error.value = ''
    draft.value = await api.createInstitutionReleaseDraft({
      target_deployment_id: deploymentId.value, package_kind: packageKind.value,
      route_version_ids: updateOnly.value ? [] : selectedRoutes.value,
      knowledge_library_ids: updateOnly.value ? selectedLibraries.value : [], base_release_id: null,
      include_full_document_library: includeFullDocuments.value,
    })
    if (overrideUri.value) {
      draft.value = await api.updateInstitutionReleaseDraft(draft.value.id, {
        route_version_ids: updateOnly.value ? [] : selectedRoutes.value,
        knowledge_library_ids: updateOnly.value ? selectedLibraries.value : [],
        base_release_id: null, include_full_document_library: includeFullDocuments.value,
        milvus_override: { uri: overrideUri.value }, milvus_override_reason: overrideReason.value,
      })
    }
    await router.push(`/institution-deployments/drafts/${draft.value.id}`)
    await refreshPlan(); step.value = 2
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function saveDraft() {
  if (!draft.value) return createDraft()
  try {
    busy.value = true
    draft.value = await api.updateInstitutionReleaseDraft(draft.value.id, {
      route_version_ids: updateOnly.value ? [] : selectedRoutes.value,
      knowledge_library_ids: updateOnly.value ? selectedLibraries.value : [],
      base_release_id: draft.value.base_release_id,
      include_full_document_library: includeFullDocuments.value,
      milvus_override: overrideUri.value ? { uri: overrideUri.value } : null,
      milvus_override_reason: overrideReason.value || null,
    })
    await refreshPlan()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function refreshPlan() {
  if (!draft.value) return
  try { plan.value = await api.institutionReleasePlan(draft.value.id) } catch (e) { error.value = e.message }
}
async function freezeRelease() {
  try {
    busy.value = true
    release.value = await api.freezeInstitutionRelease(draft.value.id)
    await router.push(`/institution-deployments/releases/${release.value.id}`)
    step.value = 4
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function buildRelease() {
  try { busy.value = true; await api.buildInstitutionRelease(release.value.id); await load() }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function inspectPackage() {
  try {
    const form = new FormData(); form.append('file', upload.value.files[0])
    inspected.value = await api.inspectMigration(form)
    for (const id of Object.keys(inspected.value.conflicts || {})) resolutions.value[id] = 'keep_local'
    await load()
  } catch (e) { error.value = e.message }
}
async function startImport() {
  try { await api.importMigration({ job_id: inspected.value.id, conflict_resolutions: resolutions.value }); await router.push(`/local/imports/${inspected.value.id}`) }
  catch (e) { error.value = e.message }
}
watch(deploymentId, loadFrozenRoutes)
watch(packageKind, () => { selectedRoutes.value = []; selectedLibraries.value = []; includeFullDocuments.value = false; plan.value = null })
watch([selectedRoutes, selectedLibraries, includeFullDocuments, overrideUri, overrideReason], () => {
  if (!draft.value || release.value || (!updateOnly.value && !selectedRoutes.value.length) ||
      (updateOnly.value && !selectedLibraries.value.length) || (overrideUri.value && !overrideReason.value.trim())) return
  clearTimeout(saveTimer)
  saveTimer = setTimeout(saveDraft, 600)
}, { deep: true })
onBeforeUnmount(() => clearTimeout(saveTimer))
onMounted(load)
</script>

<template>
  <section>
    <div class="page-head"><div><h2>机构发布部署</h2><p>{{ local ? '导入共享资产、验证候选 Partition，并逐项目激活路由。' : '冻结多个项目版本，去重共享资产，生成机构离线发布包。' }}</p></div><span class="badge blue">{{ instance?.display_name || 'DataForge' }}</span></div>

    <template v-if="!local">
      <nav class="tabs"><button v-for="value in [1,2,3,4]" :key="value" :class="{active:step===value}" :disabled="value>1&&!draft" @click="step=value">{{ value }}. {{ ['选择范围','就绪矩阵','资产差异','冻结与构建'][value-1] }}</button></nav>
      <section v-if="step===1" class="panel stack">
        <div class="panel-head"><div><h3>发布范围</h3><p>发布工作台不会修改任何项目授权；项目授权必须先在“项目发布”中冻结。</p></div><span class="badge amber">自动保存草稿</span></div>
        <label>目标机构<select v-model="deploymentId"><option v-for="item in institutionDeployments" :key="item.id" :value="item.id">{{ item.institution_name || item.name }} · {{ item.institution_code }}</option></select></label>
        <label>发布模式<select v-model="packageKind"><option value="deployment_seed">首次部署 Seed</option><option value="institution_release">机构多项目发布</option><option value="knowledge_update">知识资产更新</option></select></label>
        <p class="notice" v-if="updateOnly">本操作只迁移知识资产，不会改变机构本地任何项目的当前路由。</p>
        <div v-if="!updateOnly" class="card-grid">
          <label v-for="version in frozenRoutes" :key="version.id" class="stat-card"><span><input v-model="selectedRoutes" type="checkbox" :value="version.id"> {{ version.project.name }}</span><b>Route v{{ version.version_no }}</b><small>{{ version.binding.code }} · {{ version.release_stage }}</small></label>
          <p v-if="!frozenRoutes.length" class="muted">该机构还没有已冻结的项目版本。请先进入“项目发布”完成项目级就绪检查与冻结。</p>
        </div>
        <label v-else>知识库（完整当前快照）<select v-model="selectedLibraries" multiple><option v-for="library in libraries" :key="library.id" :value="library.id">{{ library.name }} · {{ library.knowledge_type }}</option></select></label>
        <label v-if="updateOnly"><input v-model="includeFullDocuments" type="checkbox"> 同时携带完整关联文档库与模板运行闭包</label>
        <div class="grid2"><label>机构 Milvus 默认预设<input :value="selectedDeployment?.stage_targets?.[selectedDeployment?.release_stage]?.milvus_url||'未配置'" readonly></label><label>本次临时覆盖（可选）<input v-model="overrideUri" placeholder="不回写机构默认预设"></label></div>
        <label v-if="overrideUri">临时覆盖原因<textarea v-model="overrideReason" required rows="2"></textarea></label>
        <div class="actions"><button class="primary" :disabled="busy||(!updateOnly&&!selectedRoutes.length)||(updateOnly&&!selectedLibraries.length)||(overrideUri&&!overrideReason.trim())" @click="createDraft">创建并检查草稿</button></div>
      </section>

      <section v-else-if="step===2" class="panel">
        <div class="panel-head"><div><h3>项目就绪矩阵</h3><p>项目级冻结、资产 Ready、Contract 与共享物理契约必须同时通过。</p></div><button @click="saveDraft">保存并重新检查</button></div>
        <table><thead><tr><th>项目</th><th>RouteVersion</th><th>知识库</th><th>AssetVersion</th><th>可发布</th></tr></thead><tbody><tr v-for="project in plan?.projects||[]" :key="project.project_deployment_id"><td>{{ project.project.name }}</td><td>v{{ project.route_version }}</td><td>{{ project.route_snapshot?.routes?.reduce((sum,row)=>sum+(row.libraries?.length||0),0) }}</td><td>{{ project.route_snapshot?.routes?.flatMap(row=>row.libraries||[]).map(item=>`v${item.asset_version_no}`).join('、') }}</td><td><span class="badge green">通过</span></td></tr></tbody></table>
        <p v-if="updateOnly" class="notice">Knowledge Update 不创建或引用 ProjectRouteVersion。</p>
        <div class="actions"><button @click="step=1">返回范围</button><button class="primary" :disabled="!plan" @click="step=3">查看资产差异</button></div>
      </section>

      <section v-else-if="step===3" class="panel">
        <div class="panel-head"><div><h3>发布资产差异</h3><p>数量与大小按 Asset ID 去重，Tombstone 只收敛同源 central_import 资产。</p></div><span class="badge blue">{{ plan?.counts?.partitions || 0 }} 个 Partition</span></div>
        <div class="metrics"><div><b>{{ plan?.diff_summary?.asset_versions?.added||0 }}</b><span>新增</span></div><div><b>{{ plan?.diff_summary?.asset_versions?.removed||0 }}</b><span>删除清单</span></div><div><b>{{ plan?.diff_summary?.asset_versions?.reused||0 }}</b><span>共享复用</span></div><div><b>{{ ((plan?.counts?.object_size_bytes||0)/1048576).toFixed(1) }} MB</b><span>对象增量</span></div></div>
        <table><thead><tr><th>知识库</th><th>AssetVersion</th><th>Collection</th><th>物理 Partition</th><th>条目</th></tr></thead><tbody><tr v-for="asset in plan?.asset_versions||[]" :key="asset.asset_version_id"><td>{{ asset.knowledge_library_name }}</td><td>v{{ asset.asset_version_no }}</td><td><code>{{ asset.collection_name }}</code></td><td><code>{{ asset.partition_name }}</code></td><td>{{ asset.item_count }}</td></tr></tbody></table>
        <details><summary>Tombstone（{{ plan?.tombstones?.length||0 }}）</summary><pre>{{ JSON.stringify(plan?.tombstones||[],null,2) }}</pre></details>
        <div class="actions"><button @click="step=2">返回矩阵</button><button class="primary" :disabled="busy" @click="freezeRelease">冻结 Release Snapshot</button></div>
      </section>

      <section v-else class="panel">
        <div class="panel-head"><div><h3>{{ release ? kindLabel(release.package_kind) : '冻结与构建' }}</h3><p>Snapshot 冻结后不可变；构建失败可从同一 Snapshot 重试。</p></div><span class="badge" :class="statusClass(release?.status)">{{ release?.status || '尚未冻结' }}</span></div>
        <p v-if="release"><code>{{ release.id }}</code> · digest <code>{{ release.manifest_digest }}</code></p>
        <div class="actions"><button class="primary" :disabled="!release||busy||release.status==='building'" @click="buildRelease">生成签名 .dfm 包</button></div>
        <table><thead><tr><th>构建任务</th><th>类型</th><th>阶段</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="job in jobs.filter(item=>!release||item.release_snapshot_id===release.id)" :key="job.id"><td><code>{{ job.id }}</code></td><td>{{ kindLabel(job.package_kind) }}</td><td>{{ job.stage }}</td><td><span class="badge" :class="statusClass(job.status)">{{ job.status }}</span></td><td><a v-if="job.status==='ready'" :href="api.migrationPackageUrl(job.id)">下载</a></td></tr></tbody></table>
      </section>

      <section class="panel"><div class="panel-head"><div><h3>机构发布记录</h3><p>中心离线状态最多确认包已生成，不伪造机构已激活。</p></div></div><table><thead><tr><th>Release</th><th>机构</th><th>类型</th><th>状态</th><th>时间</th></tr></thead><tbody><tr v-for="item in releases" :key="item.id"><td><RouterLink :to="`/institution-deployments/releases/${item.id}`">{{ item.id }}</RouterLink></td><td>{{ deployments.find(value=>value.id===item.target_deployment_id)?.institution_name||item.target_deployment_id }}</td><td>{{ kindLabel(item.package_kind) }}</td><td><span class="badge" :class="statusClass(item.status)">{{ item.status }}</span></td><td>{{ item.created_at }}</td></tr></tbody></table></section>
    </template>

    <template v-else>
      <section class="panel stack"><div class="panel-head"><div><h3>导入离线发布包</h3><p>未配置 Milvus 也可先完成契约、元数据、文档与对象导入。</p></div><RouterLink class="button" to="/local/initialization">打开初始化向导</RouterLink></div><form @submit.prevent="inspectPackage"><input ref="upload" type="file" accept=".dfm" required><button>检查签名、版本与范围</button></form><template v-if="inspected"><p><b>{{ kindLabel(inspected.package_kind) }}</b> · {{ inspected.stage }}</p><div v-for="(_, libraryId) in inspected.conflicts" :key="libraryId"><label>{{ libraryId }}<select v-model="resolutions[libraryId]"><option value="keep_local">保留本地 Fork</option><option value="replace_with_central">使用中心版本</option><option value="import_as_new">另存为本地副本</option></select></label></div><button class="primary" @click="startImport">开始可恢复导入</button></template></section>
      <section class="panel"><h3>导入任务</h3><table><thead><tr><th>时间</th><th>包类型</th><th>检查点</th><th>状态</th><th>恢复入口</th></tr></thead><tbody><tr v-for="job in jobs" :key="job.id"><td>{{ job.created_at }}</td><td>{{ kindLabel(job.package_kind) }}</td><td>{{ job.stage }}</td><td><span class="badge" :class="statusClass(job.status)">{{ job.status }}</span></td><td><RouterLink :to="`/local/imports/${job.id}`">任务详情</RouterLink></td></tr></tbody></table></section>
    </template>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>
