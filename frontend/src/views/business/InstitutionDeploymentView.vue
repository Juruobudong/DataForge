<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'
import { frozenRoutesForStage, groupedAssetOptions, institutionReleaseTarget, releaseCanFreeze, releaseSelectionSummary } from './institutionReleaseModel'

const route = useRoute(), router = useRouter()
const instance = ref(null), deployments = ref([]), projects = ref([]), libraries = ref([])
const releases = ref([]), jobs = ref([]), frozenRoutes = ref([])
const deploymentId = ref(''), packageKind = ref('deployment_seed'), selectedStage = ref('test'), selectedRoutes = ref([]), selectedLibraries = ref([])
const extraAssetVersionIds = ref([]), assetOptions = ref({ collections: [] })
const includeFullDocuments = ref(false)
const newInstitutionName = ref(''), newInstitutionCode = ref(''), newInstitutionProjectIds = ref([])
const boundProjectIds = ref([])
const step = ref(1), draft = ref(null), plan = ref(null), release = ref(null), upload = ref(null)
const inspected = ref(null), resolutions = ref({}), error = ref(''), busy = ref(false)
const local = computed(() => instance.value?.instance_mode === 'local')
let saveTimer = null
const institutionDeployments = computed(() => deployments.value.filter(item => item.scope === 'institution'))
const selectedDeployment = computed(() => institutionDeployments.value.find(item => item.id === deploymentId.value))
const targetInstitutionCode = computed(() => selectedDeployment.value?.institution_code || '')
const updateOnly = computed(() => packageKind.value === 'knowledge_update')
const assetGroups = computed(() => groupedAssetOptions(assetOptions.value))
const selectionSummary = computed(() => releaseSelectionSummary(plan.value))
const canFreeze = computed(() => releaseCanFreeze(plan.value))

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
  const bindings = await api.deploymentProjects(deploymentId.value)
  boundProjectIds.value = bindings.map(item => item.project.id)
  const rows = await Promise.all(bindings.map(async binding => ({
    project: binding.project, binding, versions: await api.routeVersions(binding.project.id, selectedStage.value),
  })))
  frozenRoutes.value = rows.flatMap(row => frozenRoutesForStage(row.versions, selectedStage.value)
    .map(version => ({ ...version, project: row.project, binding: row.binding })))
}
async function load() {
  try {
    error.value = ''
    instance.value = await api.instance()
    if (!route.params.draftId && !route.params.releaseId) {
      selectedStage.value = instance.value?.default_release_stage === 'production' ? 'production' : 'test'
    }
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
      selectedStage.value = draft.value.release_stage || draft.value.selection?.release_stage || 'test'
      selectedRoutes.value = draft.value.selection?.route_version_ids || []
      selectedLibraries.value = draft.value.selection?.knowledge_library_ids || []
      extraAssetVersionIds.value = draft.value.selection?.extra_asset_version_ids || []
      includeFullDocuments.value = Boolean(draft.value.selection?.include_full_document_library)
      await loadFrozenRoutes()
      selectedRoutes.value = draft.value.selection?.route_version_ids || []
      await refreshPlan()
    }
    if (route.params.releaseId) {
      release.value = await api.institutionRelease(route.params.releaseId)
      plan.value = release.value.snapshot
      step.value = 5
    }
  } catch (e) { error.value = e.message }
}
async function createDraft() {
  try {
    busy.value = true; error.value = ''
    draft.value = await api.createInstitutionReleaseDraft({
      ...institutionReleaseTarget(selectedDeployment.value), package_kind: packageKind.value,
      release_stage: selectedStage.value,
      route_version_ids: updateOnly.value ? [] : selectedRoutes.value,
      knowledge_library_ids: updateOnly.value ? selectedLibraries.value : [], base_release_id: null,
      extra_asset_version_ids: updateOnly.value ? [] : extraAssetVersionIds.value,
      include_full_document_library: includeFullDocuments.value,
    })
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
      release_stage: selectedStage.value,
      knowledge_library_ids: updateOnly.value ? selectedLibraries.value : [],
      extra_asset_version_ids: updateOnly.value ? [] : extraAssetVersionIds.value,
      base_release_id: draft.value.base_release_id,
      include_full_document_library: includeFullDocuments.value,
    })
    await refreshPlan()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function refreshPlan() {
  if (!draft.value) return
  try {
    ;[plan.value, assetOptions.value] = await Promise.all([
      api.institutionReleasePlan(draft.value.id), api.institutionReleaseAssetOptions(draft.value.id),
    ])
  } catch (e) { error.value = e.message }
}
async function freezeRelease() {
  try {
    busy.value = true
    release.value = await api.freezeInstitutionRelease(draft.value.id)
    await router.push(`/institution-deployments/releases/${release.value.id}`)
    step.value = 5
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function buildRelease() {
  try { busy.value = true; await api.buildInstitutionRelease(release.value.id); await load() }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function createInstitutionDeployment() {
  try {
    busy.value = true; error.value = ''
    const created = await api.createSharedDeployment({
      institution_name: newInstitutionName.value.trim(),
      institution_code: newInstitutionCode.value.trim(),
      project_ids: newInstitutionProjectIds.value,
    })
    newInstitutionName.value = ''; newInstitutionCode.value = ''; newInstitutionProjectIds.value = []
    await load(); deploymentId.value = created.id; await loadFrozenRoutes()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function saveInstitutionProjects() {
  try { busy.value = true; await api.replaceDeploymentProjects(deploymentId.value, boundProjectIds.value); await loadFrozenRoutes() }
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
watch(selectedStage, loadFrozenRoutes)
watch(packageKind, () => { selectedRoutes.value = []; selectedLibraries.value = []; extraAssetVersionIds.value = []; includeFullDocuments.value = false; plan.value = null; assetOptions.value = { collections: [] } })
watch([selectedRoutes, selectedLibraries, extraAssetVersionIds, includeFullDocuments], () => {
  if (!draft.value || release.value || (!updateOnly.value && !selectedRoutes.value.length) ||
      (updateOnly.value && !selectedLibraries.value.length)) return
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
      <section class="panel stack">
        <div class="panel-head"><div><h3>机构 Deployment 管理</h3><p>机构 Deployment 只表达机构身份及项目归属；机构 Milvus 由 local 初始化配置。</p></div></div>
        <form class="grid2" @submit.prevent="createInstitutionDeployment">
          <label>机构名称<input v-model="newInstitutionName" required></label>
          <label>机构代码<input v-model="newInstitutionCode" required></label>
          <label>绑定项目<select v-model="newInstitutionProjectIds" multiple required><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }} · {{ project.code }}</option></select></label>
          <button class="primary" :disabled="busy||!newInstitutionProjectIds.length">创建机构 Deployment</button>
        </form>
        <form v-if="selectedDeployment" class="grid2" @submit.prevent="saveInstitutionProjects">
          <label>当前机构<input :value="`${selectedDeployment.institution_name} · ${selectedDeployment.institution_code}`" readonly></label>
          <label>已绑定项目<select v-model="boundProjectIds" multiple><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }} · {{ project.code }}</option></select></label>
          <button :disabled="busy">保存项目绑定</button>
        </form>
      </section>
      <nav class="tabs"><button v-for="value in [1,2,3,4,5]" :key="value" :class="{active:step===value}" :disabled="value>1&&!draft" @click="step=value">{{ value }}. {{ ['发布范围','知识资产','判重与就绪','资产差异','冻结与构建'][value-1] }}</button></nav>
      <section v-if="step===1" class="panel stack">
        <div class="panel-head"><div><h3>发布范围</h3><p>发布工作台不会修改任何项目授权；项目授权必须先在“项目发布”中冻结。</p></div><span class="badge amber">自动保存草稿</span></div>
        <label>目标机构<select v-model="deploymentId" :disabled="Boolean(draft)"><option v-for="item in institutionDeployments" :key="item.id" :value="item.id">{{ item.institution_name || item.name }} · {{ item.institution_code }}</option></select></label>
        <label>机构发布目标（institution_code）<input :value="targetInstitutionCode" readonly></label>
        <label>发布模式<select v-model="packageKind"><option value="deployment_seed">首次部署 Seed</option><option value="institution_release">机构多项目发布</option><option value="knowledge_update">知识资产更新</option></select></label>
        <div class="tabs" role="tablist" aria-label="机构发布环境"><button type="button" role="tab" :aria-selected="selectedStage==='test'" :class="{active:selectedStage==='test'}" @click="selectedStage='test'">测试环境</button><button type="button" role="tab" :aria-selected="selectedStage==='production'" :class="{active:selectedStage==='production'}" @click="selectedStage='production'">生产环境</button></div>
        <p class="notice" v-if="updateOnly">本操作只迁移知识资产，不会改变机构本地任何项目的当前路由。</p>
        <div v-if="!updateOnly" class="card-grid">
          <label v-for="version in frozenRoutes" :key="version.id" class="stat-card"><span><input v-model="selectedRoutes" type="checkbox" :value="version.id"> {{ version.project.name }}</span><b>Route v{{ version.version_no }}</b><small>{{ version.binding.code }} · {{ version.release_stage }}</small></label>
          <p v-if="!frozenRoutes.length" class="muted">该机构还没有已冻结的项目版本。请先进入“项目发布”完成项目级就绪检查与冻结。</p>
        </div>
        <label v-else>知识库（完整当前快照）<select v-model="selectedLibraries" multiple><option v-for="library in libraries" :key="library.id" :value="library.id">{{ library.name }} · {{ library.knowledge_type }}</option></select></label>
        <label v-if="updateOnly"><input v-model="includeFullDocuments" type="checkbox"> 同时携带完整关联文档库与模板运行闭包</label>
        <p class="notice">中心不保存或连接机构 Milvus；发布包导入后，由机构 local 的 verified Current Target 承接向量资产。</p>
        <div class="actions"><button class="primary" :disabled="busy||!targetInstitutionCode||(!updateOnly&&!selectedRoutes.length)||(updateOnly&&!selectedLibraries.length)" @click="createDraft">创建并检查草稿</button></div>
      </section>

      <section v-else-if="step===2" class="panel">
        <div class="panel-head"><div><h3>知识资产</h3><p>项目必需资产自动锁定；额外选择只提交 AssetVersion ID。</p></div><span class="badge blue">项目必选 {{ plan?.asset_versions?.filter(item=>item.locked).length||0 }} · 额外 {{ extraAssetVersionIds.length }}</span></div>
        <details v-for="group in assetGroups" :key="group.collection_name" open>
          <summary><code>{{ group.collection_name }}</code>（{{ group.assets.length }}）</summary>
          <div class="stack">
            <label v-for="asset in group.assets" :key="asset.asset_version_id" class="stat-card">
              <span>
                <input v-if="asset.locked" type="checkbox" checked disabled>
                <input v-else-if="!updateOnly" v-model="extraAssetVersionIds" type="checkbox" :value="asset.asset_version_id">
                <span v-else>•</span>
                {{ asset.knowledge_library_name }} · v{{ asset.asset_version_no }}
              </span>
              <code>{{ asset.partition_name }}</code>
              <small v-if="asset.locked">项目必选：{{ asset.projectNames.join('、') }}</small>
              <small v-else>{{ asset.item_count }} 条 · {{ asset.status }}</small>
            </label>
          </div>
        </details>
        <p v-if="updateOnly" class="notice">Knowledge Update 继续按知识库解析 Ready 多 Profile 资产，不创建或引用 ProjectRouteVersion。</p>
        <div class="actions"><button @click="step=1">返回范围</button><button @click="saveDraft">保存并重新检查</button><button class="primary" :disabled="!plan" @click="step=3">查看判重与就绪</button></div>
      </section>

      <section v-else-if="step===3" class="panel">
        <div class="panel-head"><div><h3>判重与就绪</h3><p>后端对项目闭包、资产状态、逻辑版本、Collection Contract 与 Partition 内容执行最终门禁。</p></div><span class="badge" :class="plan?.preflight?.blocked?'red':'green'">{{ plan?.preflight?.blocked||0 }} Blocked</span></div>
        <div class="metrics"><div><b>{{ selectionSummary.projectRequiredRefs }}</b><span>项目资产引用</span></div><div><b>{{ selectionSummary.manualRefs }}</b><span>额外选择</span></div><div><b>{{ selectionSummary.rawRefs }}</b><span>原始引用</span></div><div><b>-{{ selectionSummary.duplicatesRemoved }}</b><span>自动去重</span></div><div><b>{{ selectionSummary.resolvedAssets }}</b><span>最终资产</span></div></div>
        <table><thead><tr><th>检查</th><th>状态</th><th>Expected</th><th>Observed</th><th>说明</th></tr></thead><tbody><tr v-for="check in plan?.preflight?.checks||[]" :key="`${check.code}-${JSON.stringify(check.subject)}`"><td><code>{{ check.code }}</code></td><td><span class="badge" :class="check.status==='passed'?'green':'red'">{{ check.status }}</span></td><td><code>{{ JSON.stringify(check.expected) }}</code></td><td><code>{{ JSON.stringify(check.observed) }}</code></td><td>{{ check.message }}</td></tr></tbody></table>
        <h3>项目就绪矩阵</h3>
        <table><thead><tr><th>项目</th><th>RouteVersion</th><th>知识库</th><th>AssetVersion</th></tr></thead><tbody><tr v-for="project in plan?.projects||[]" :key="project.project_deployment_id"><td>{{ project.project.name }}</td><td>v{{ project.route_version }}</td><td>{{ project.route_snapshot?.routes?.reduce((sum,row)=>sum+(row.libraries?.length||0),0) }}</td><td>{{ project.route_snapshot?.routes?.flatMap(row=>row.libraries||[]).map(item=>`v${item.asset_version_no}`).join('、') }}</td></tr></tbody></table>
        <div class="actions"><button @click="step=2">返回知识资产</button><button class="primary" :disabled="!plan" @click="step=4">查看资产差异</button></div>
      </section>

      <section v-else-if="step===4" class="panel">
        <div class="panel-head"><div><h3>发布资产差异</h3><p>数量与大小按 Asset ID 去重，Tombstone 只收敛同源 central_import 资产。</p></div><span class="badge blue">{{ plan?.counts?.partitions || 0 }} 个 Partition</span></div>
        <div class="metrics"><div><b>{{ plan?.counts?.projects||0 }}</b><span>项目</span></div><div><b>{{ plan?.counts?.knowledge_libraries||0 }}</b><span>知识库</span></div><div><b>{{ plan?.counts?.collections||0 }}</b><span>Collections</span></div><div><b>{{ plan?.counts?.partitions||0 }}</b><span>Partitions</span></div><div><b>{{ plan?.counts?.vector_item_count||0 }}</b><span>Vector Rows</span></div><div><b>{{ ((plan?.counts?.object_size_bytes||0)/1048576).toFixed(1) }} MB</b><span>对象大小</span></div></div>
        <table><thead><tr><th>知识库</th><th>AssetVersion</th><th>Collection</th><th>物理 Partition</th><th>条目</th></tr></thead><tbody><tr v-for="asset in plan?.asset_versions||[]" :key="asset.asset_version_id"><td>{{ asset.knowledge_library_name }}</td><td>v{{ asset.asset_version_no }}</td><td><code>{{ asset.collection_name }}</code></td><td><code>{{ asset.partition_name }}</code></td><td>{{ asset.item_count }}</td></tr></tbody></table>
        <details><summary>Tombstone（{{ plan?.tombstones?.length||0 }}）</summary><pre>{{ JSON.stringify(plan?.tombstones||[],null,2) }}</pre></details>
        <div class="actions"><button @click="step=3">返回检查</button><button class="primary" @click="step=5">进入冻结与构建</button></div>
      </section>

      <section v-else-if="step===5" class="panel">
        <div class="panel-head"><div><h3>{{ release ? kindLabel(release.package_kind) : '冻结与构建' }}</h3><p>Snapshot 冻结后不可变；构建失败可从同一 Snapshot 重试。</p></div><span class="badge" :class="statusClass(release?.status)">{{ release?.status || '尚未冻结' }}</span></div>
        <p v-if="release"><code>{{ release.id }}</code> · digest <code>{{ release.manifest_digest }}</code></p>
        <div class="actions"><button v-if="!release" class="primary" :disabled="busy||!canFreeze" @click="freezeRelease">冻结 Release Snapshot</button><button v-else class="primary" :disabled="busy||release.status==='building'" @click="buildRelease">生成签名 .dfm 包</button></div>
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
