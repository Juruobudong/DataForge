<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/platform'
import PublishTargetPanel from '../../components/project-publishing/PublishTargetPanel.vue'
import EnvironmentTabs from '../../components/project-publishing/EnvironmentTabs.vue'
import ProjectTaskPanel from '../../components/project-publishing/ProjectTaskPanel.vue'
import KnowledgeScopePanel from '../../components/project-publishing/KnowledgeScopePanel.vue'
import RoutingPublishPanel from '../../components/project-publishing/RoutingPublishPanel.vue'
import RetrievalDebugPanel from '../../components/project-publishing/RetrievalDebugPanel.vue'
import RetrievalTaskSettings from '../../components/project-publishing/RetrievalTaskSettings.vue'
import RouteVersionTable from '../../components/project-publishing/RouteVersionTable.vue'
import { statusLabel } from '../../constants/statusLabels'
import { availableOrgCodePresets, compatibleProfilesForTask, defaultKnowledgeType, movePriority, newOrgScopeDefaults, normalizeDefaultReleaseStage, orgRoutesForTask, reorderPriority, resolveOrgCodePreset, routingPublishReadiness, routingValidationView, sortKnowledgeTypes, sortProjectChoices } from './projectPublishingModel'

const instance = ref(null), projects = ref([]), libraries = ref([]), knowledgeTypes = ref([]), milvusTargets = ref([])
const projectId = ref(''), selectedStage = ref('test'), tab = ref('config'), releaseTarget = ref(null)
const deploymentTasks = ref([]), authorizations = ref([]), versions = ref([])
const deploymentTaskId = ref(''), orgRouteId = ref(''), orgPresetCode = ref(''), orgCode = ref(''), orgName = ref(''), chosen = ref([])
const selectedMilvusTargetId = ref('')
const showCreateProject = ref(false), newProjectName = ref('')
const newTaskCode = ref(''), newTaskName = ref(''), newTaskKnowledgeType = ref(''), newTaskDescription = ref('')
const newProjectTaskId = ref(''), newIndexProfileId = ref(''), newTopK = ref(10), newDeploymentTaskEnabled = ref(true)
const result = ref(null), diffResult = ref(null), preview = ref(null), validatedContext = ref('')
const expandedTaskId = ref(''), deletingTaskId = ref(''), error = ref(''), notice = ref(''), releaseBusy = ref(false)

const selectedProject = computed(() => projects.value.find(project => project.id === projectId.value))
const local = computed(() => instance.value?.instance_mode === 'local')
const central = computed(() => instance.value?.instance_mode === 'central')
const freezesForInstitution = computed(() => false)
const currentStageTarget = computed(() => releaseTarget.value?.milvus_target?.revision?.milvus_url || releaseTarget.value?.uri || '')
const currentStageTargetId = computed(() => releaseTarget.value?.milvus_target?.id || '')
const verifiedMilvusTargets = computed(() => milvusTargets.value.filter(item => item.current_revision?.verification_status === 'verified'))
const projectChoices = computed(() => sortProjectChoices(projects.value))
const isKgConsultation = computed(() => selectedProject.value?.code === 'kg-for-consultation')
const activeKnowledgeTypes = computed(() => sortKnowledgeTypes(knowledgeTypes.value.filter(item => item.status === 'active')))
const unboundProjectTasks = computed(() => {
  const bound = new Set(deploymentTasks.value.map(item => item.project_task_id))
  return (selectedProject.value?.tasks || []).filter(item => !bound.has(item.id))
})
const selectedProjectTask = computed(() => (selectedProject.value?.tasks || []).find(item => item.id === newProjectTaskId.value))
const compatibleProfiles = computed(() => compatibleProfilesForTask(selectedProjectTask.value, knowledgeTypes.value))
const selectedDeploymentTask = computed(() => deploymentTasks.value.find(item => item.id === deploymentTaskId.value))
const taskAuthorizations = computed(() => orgRoutesForTask(authorizations.value, deploymentTaskId.value))
const orgCodePresets = computed(() => availableOrgCodePresets(instance.value?.org_code_presets))
const publishState = computed(() => routingPublishReadiness(
  deploymentTasks.value, authorizations.value, currentStageTarget.value,
))
const validationView = computed(() => routingValidationView(result.value))
const contextKey = computed(() => `${projectId.value}:${selectedStage.value}`)
const releaseReady = computed(() => publishState.value.ready && validationView.value.valid && validatedContext.value === contextKey.value)
const releaseActionLabel = computed(() => `冻结并发布${statusLabel(selectedStage.value)}版本`)
const availableLibraries = computed(() => {
  const task = selectedDeploymentTask.value
  return libraries.value.filter(library => {
    if (library.status !== 'active' || library.migration_status !== 'ready') return false
    if (!task?.task?.knowledge_type) return true
    if (library.knowledge_type !== task.task.knowledge_type) return false
    if (task.index_profile?.code === 'graph-triple') return library.graph_mode === 'triple'
    if (task.index_profile?.code === 'graph-semantic') return library.graph_mode === 'semantic'
    return true
  })
})
const selectedTaskRoutes = computed(() => orgRoutesForTask(authorizations.value, deploymentTaskId.value))

function invalidateReleaseEvidence() {
  result.value = null; diffResult.value = null; validatedContext.value = ''; releaseBusy.value = false
}

function configuredDefaultStage() {
  return normalizeDefaultReleaseStage(instance.value?.default_release_stage)
}

async function loadReleaseTarget() {
  releaseTarget.value = null
  if (local.value) {
    const configs = await api.localMilvusConfigurations()
    const current = configs.find(item => item.slot === 'current_target' && item.status === 'verified')
    releaseTarget.value = current ? { uri: current.uri } : null
  } else {
    try { releaseTarget.value = await api.instanceReleaseTarget(selectedStage.value) }
    catch { releaseTarget.value = null }
  }
  selectedMilvusTargetId.value = currentStageTargetId.value
}
async function load() {
  try {
    error.value = ''
    ;[instance.value, projects.value, libraries.value, knowledgeTypes.value] = await Promise.all([
      api.instance(), api.projects(), api.knowledgeLibraries(), api.knowledgeTypes(),
    ])
    milvusTargets.value = instance.value?.instance_mode === 'central' ? await api.milvusTargets() : []
    selectedStage.value = configuredDefaultStage()
    projectId.value ||= projectChoices.value[0]?.id || ''
    newTaskKnowledgeType.value ||= defaultKnowledgeType(activeKnowledgeTypes.value)
    await loadReleaseTarget()
  } catch (e) { error.value = e.message }
}
async function loadDeployment(preferredOrgCode = '') {
  invalidateReleaseEvidence(); preview.value = null
  if (!projectId.value) { deploymentTasks.value = []; authorizations.value = []; versions.value = []; return }
  try {
    ;[deploymentTasks.value, authorizations.value, versions.value] = await Promise.all([
      api.deploymentTasks(projectId.value), api.authorizations(projectId.value), api.routeVersions(projectId.value, selectedStage.value),
    ])
    if (!deploymentTasks.value.some(item => item.id === deploymentTaskId.value)) {
      deploymentTaskId.value = deploymentTasks.value.find(item => item.enabled)?.id || ''
    }
    if (!deploymentTasks.value.some(item => item.id === expandedTaskId.value)) {
      expandedTaskId.value = deploymentTaskId.value
    }
    selectDefaultOrgRoute(typeof preferredOrgCode === 'string' ? preferredOrgCode : '')
  } catch (e) { error.value = e.message }
}
function startNewOrgRoute() {
  const defaults = newOrgScopeDefaults(null, taskAuthorizations.value)
  orgRouteId.value = '__new__'; orgPresetCode.value = ''; orgCode.value = defaults.orgCode; orgName.value = defaults.orgName; chosen.value = []
}
function toggleTask(taskId) {
  expandedTaskId.value = expandedTaskId.value === taskId ? '' : taskId
  if (expandedTaskId.value) {
    deploymentTaskId.value = taskId
    selectDefaultOrgRoute()
  }
}
function syncOrgScope() {
  const route = taskAuthorizations.value.find(item => item.id === orgRouteId.value)
  if (!route) {
    if (orgRouteId.value !== '__new__') startNewOrgRoute()
    return
  }
  orgPresetCode.value = ''
  orgCode.value = route.org_code; orgName.value = route.org_name || ''
  chosen.value = [...(route.knowledge_library_ids || [])].filter(id => availableLibraries.value.some(item => item.id === id))
}
function selectDefaultOrgRoute(preferredOrgCode = '') {
  const route = taskAuthorizations.value.find(item => item.org_code === preferredOrgCode) || taskAuthorizations.value[0]
  if (!route) { startNewOrgRoute(); return }
  orgRouteId.value = route.id; syncOrgScope()
}
function applyOrgPreset() {
  const resolvedPreset = resolveOrgCodePreset(orgCodePresets.value, orgPresetCode.value, taskAuthorizations.value)
  if (!resolvedPreset) return
  if (resolvedPreset.existingRoute) {
    orgRouteId.value = resolvedPreset.existingRoute.id
    syncOrgScope()
    return
  }
  orgRouteId.value = '__new__'
  orgCode.value = resolvedPreset.preset.org_code
  orgName.value = resolvedPreset.preset.name
  chosen.value = []
}
function markCustomOrgCode() { orgPresetCode.value = '' }
async function createProject() {
  try {
    const created = await api.createProject({ name: newProjectName.value.trim() })
    newProjectName.value = ''; showCreateProject.value = false
    await load(); projectId.value = created.id; await loadDeployment()
    tab.value = 'config'; notice.value = `项目「${created.name}」已创建。`
  } catch (e) { error.value = e.message }
}
async function createProjectTask() {
  try {
    const created = await api.createProjectTask(projectId.value, { code: newTaskCode.value.trim(), name: newTaskName.value.trim(), knowledge_type: newTaskKnowledgeType.value, description: newTaskDescription.value.trim() })
    newTaskCode.value = ''; newTaskName.value = ''; newTaskDescription.value = ''
    await load(); newProjectTaskId.value = created.id; notice.value = `业务任务「${created.name}」已创建，请继续配置检索通道。`
  } catch (e) { error.value = e.message }
}
async function createDeploymentTask() {
  try {
    await api.createDeploymentTask(projectId.value, { project_task_id: newProjectTaskId.value, index_profile_id: newIndexProfileId.value, top_k: Number(newTopK.value), enabled: newDeploymentTaskEnabled.value })
    newProjectTaskId.value = ''; newIndexProfileId.value = ''; newTopK.value = 10; newDeploymentTaskEnabled.value = true
    await loadDeployment(); notice.value = '检索通道已创建。'
  } catch (e) { error.value = e.message }
}
async function deleteDeploymentTask(task) {
  if (deletingTaskId.value) return
  const taskName = task.task?.name || task.task?.code || '该检索任务'
  const taskCode = task.task?.code || task.id
  const routeCount = orgRoutesForTask(authorizations.value, task.id).length
  const confirmed = window.confirm(
    `确认删除检索任务「${taskName}」(${taskCode})？\n\n将删除当前草稿中的运行任务、${routeCount} 个授权范围及业务任务定义。已发布和历史版本不受影响；下次发布后正式检索才会生效。`,
  )
  if (!confirmed) return
  deletingTaskId.value = task.id; error.value = ''
  try {
    await api.deleteDeploymentTask(projectId.value, task.id)
    if (deploymentTaskId.value === task.id || expandedTaskId.value === task.id) {
      deploymentTaskId.value = ''; expandedTaskId.value = ''
      orgRouteId.value = ''; orgPresetCode.value = ''; orgCode.value = ''; orgName.value = ''; chosen.value = []
    }
    await load(); await loadDeployment()
    notice.value = `检索任务「${taskName}」已从当前草稿删除；下次发布后正式检索生效。`
  } catch (e) { error.value = e.message }
  finally { deletingTaskId.value = '' }
}
function toggleLibrary(id) { chosen.value = chosen.value.includes(id) ? chosen.value.filter(value => value !== id) : [...chosen.value, id] }
function moveLibrary(id, offset) {
  chosen.value = movePriority(chosen.value, id, offset)
}
function reorderLibrary({ id, targetId, after }) {
  chosen.value = reorderPriority(chosen.value, id, targetId, after)
}
async function saveRoute() {
  try {
    const normalizedOrgCode = orgCode.value.trim()
    if (!normalizedOrgCode) { error.value = 'org_code 不能为空'; return }
    await api.putDeploymentRoute(projectId.value, deploymentTaskId.value, {
      org_code: normalizedOrgCode, org_name: orgName.value.trim(), knowledge_library_ids: chosen.value,
    })
    await loadDeployment(normalizedOrgCode); notice.value = '知识范围已保存，列表顺序即检索优先级。'
  } catch (e) { error.value = e.message }
}
async function saveCentralTarget() {
  const target = verifiedMilvusTargets.value.find(item => item.id === selectedMilvusTargetId.value)
  if (!target) return
  const production = selectedStage.value === 'production'
  const revision = target.current_revision
  if (production && !window.confirm(`确认首次绑定中心生产环境 Milvus 服务？\nMilvus：${revision.milvus_url}\n绑定后本期不可切换。`)) return
  try {
    await api.putInstanceReleaseTarget(selectedStage.value, { milvus_target_id: target.id, milvus_target_revision_id: revision.id, confirm_production: production, expected_target_uri: production ? revision.milvus_url : null })
    await loadReleaseTarget(); await loadDeployment(); notice.value = `${statusLabel(selectedStage.value)} Milvus 服务已绑定。`
  } catch (e) { error.value = e.message }
}
async function preflight() {
  const key = contextKey.value
  releaseBusy.value = true; error.value = ''; preview.value = null; invalidateReleaseEvidence()
  try {
    const [validation, changes] = await Promise.all([
      api.validateRouting(projectId.value, selectedStage.value),
      api.routingDiff(projectId.value, selectedStage.value),
    ])
    if (key !== contextKey.value) return
    result.value = validation; diffResult.value = changes; validatedContext.value = key
  } catch (e) { if (key === contextKey.value) error.value = e.message }
  finally { if (key === contextKey.value) releaseBusy.value = false }
}
function releaseBody(production = false) { return { release_stage: selectedStage.value, expected_target_uri: currentStageTarget.value, confirm_production: production } }
async function freezeCurrentProject() {
  try {
    const frozen = await api.freezeRouting(projectId.value, selectedStage.value)
    await loadDeployment(); notice.value = `项目版本 v${frozen.version_no} 已冻结，可用于中心发布或机构 Release。`
    return frozen
  } catch (e) { error.value = e.message; return null }
}
async function releaseCurrentStage() {
  if (!releaseReady.value) { error.value = '请先完成并通过当前环境的发布检查。'; return }
  if (isKgConsultation.value && selectedStage.value !== 'test') { error.value = 'kg_for_consultation 第一阶段只允许发布测试环境'; return }
  const production = selectedStage.value === 'production'
  if (production && !window.confirm(`确认发布生产环境？\nMilvus：${currentStageTarget.value}\n发布后中心生产 Runtime 将立即使用新版本。`)) return
  const frozen = await freezeCurrentProject()
  if (!frozen) return
  try { await api.publishRouting(projectId.value, { ...releaseBody(production), route_version_id: frozen.id }); await loadDeployment(); tab.value = 'history' }
  catch (e) { error.value = e.message }
}
async function showVersion(version) { try { preview.value = await api.routeVersion(projectId.value, version, selectedStage.value); tab.value = 'history' } catch (e) { error.value = e.message } }
async function rollback(version) {
  const production = selectedStage.value === 'production'
  const message = production ? `确认回滚生产环境到 v${version} 的配置并发布新版本？\nMilvus：${currentStageTarget.value}` : `恢复测试环境 v${version} 的配置并发布新版本？`
  if (!window.confirm(message)) return
  try { await api.rollbackRouting(projectId.value, version, releaseBody(production)); await loadDeployment() } catch (e) { error.value = e.message }
}

watch(projectId, () => { selectedStage.value = configuredDefaultStage(); newProjectTaskId.value = ''; newIndexProfileId.value = ''; tab.value = 'config'; loadDeployment() })
watch(selectedStage, async () => { invalidateReleaseEvidence(); preview.value = null; await loadReleaseTarget(); if (projectId.value) versions.value = await api.routeVersions(projectId.value, selectedStage.value) })
watch(deploymentTaskId, () => selectDefaultOrgRoute())
watch(orgRouteId, syncOrgScope)
watch(newProjectTaskId, () => { newIndexProfileId.value = compatibleProfiles.value[0]?.id || '' })
onMounted(load)
</script>

<template>
  <section class="publishing-workbench">
    <div class="page-head"><div><h2>项目发布</h2><p>按项目配置检索任务，并发布到当前环境全项目共用的 Milvus。</p></div><div class="page-actions"><span class="badge blue">{{ local ? '机构本地' : '中心控制面' }}</span><button v-if="central" class="primary" @click="showCreateProject=!showCreateProject">{{ showCreateProject?'取消新增':'新增项目' }}</button></div></div>
    <form v-if="showCreateProject" class="panel stack" @submit.prevent="createProject"><h3>新增项目</h3><label>项目名称<input v-model="newProjectName" required maxlength="255"></label><button class="primary">创建项目</button></form>
    <section class="panel publishing-context"><div><small>项目</small><select v-model="projectId"><option v-for="project in projectChoices" :key="project.id" :value="project.id">{{ project.name }} · {{ project.code }}</option></select></div><div><small>环境</small><EnvironmentTabs :model-value="selectedStage" :target-uri="currentStageTarget" @update:model-value="selectedStage=$event" /></div><div><small>中心发布 Milvus</small><strong>{{ currentStageTarget || '尚未绑定' }}</strong></div></section>
    <nav class="tabs primary-tabs"><button :class="{active:tab==='config'}" @click="tab='config'">配置</button><button :class="{active:tab==='validation'}" @click="tab='validation'">验证</button><button :class="{active:tab==='release'}" @click="tab='release'">发布</button><button :class="{active:tab==='history'}" @click="tab='history'">版本记录</button></nav>

    <section v-if="tab==='config'" class="stack workbench-section">
      <PublishTargetPanel :target="releaseTarget" :selected-stage="selectedStage" :target-uri="currentStageTarget" :show-environment="false">
        <details class="configuration-details"><summary>管理发布目标</summary>
          <form v-if="central&&!releaseTarget" class="stack" @submit.prevent="saveCentralTarget"><label>{{ statusLabel(selectedStage) }} Milvus 服务<select v-model="selectedMilvusTargetId" required><option value="">选择已验证服务</option><option v-for="target in verifiedMilvusTargets" :key="target.id" :value="target.id">{{ target.name }} · {{ target.current_revision.milvus_url }}</option></select></label><p class="notice">该环境的 Target 首次绑定后本期不可切换，且由所有项目共用。</p><button>绑定 Milvus 服务</button></form>
          <p v-else-if="releaseTarget" class="notice">当前环境 Target 已固定；Registry 新 Revision 不会自动改绑。</p>
          <RouterLink to="/business/milvus-targets">管理 Milvus 服务注册表</RouterLink>
          <button type="button" :disabled="!projectId" @click="freezeCurrentProject">仅冻结项目版本</button>
        </details>
      </PublishTargetPanel>
      <ProjectTaskPanel :count="deploymentTasks.length">
        <details class="configuration-details"><summary>新增检索任务</summary><div class="grid2"><form class="stack" @submit.prevent="createProjectTask"><h4>1. 定义业务任务</h4><label>任务标识<input v-model="newTaskCode" required></label><label>任务名称<input v-model="newTaskName" required></label><label>知识类型<select v-model="newTaskKnowledgeType" required><option v-for="item in activeKnowledgeTypes" :key="item.id" :value="item.code">{{ item.name }}</option></select></label><label>任务说明<textarea v-model="newTaskDescription" rows="3"></textarea></label><button class="primary">创建业务任务</button></form><form class="stack" @submit.prevent="createDeploymentTask"><h4>2. 建立运行任务</h4><label>业务任务<select v-model="newProjectTaskId" required><option value="">选择业务任务</option><option v-for="task in unboundProjectTasks" :key="task.id" :value="task.id">{{ task.name }}</option></select></label><label>索引配置<select v-model="newIndexProfileId" required><option value="">选择索引配置</option><option v-for="profile in compatibleProfiles" :key="profile.id" :value="profile.id">{{ profile.code }}</option></select></label><label>候选召回数<input v-model.number="newTopK" type="number" min="1" required></label><label><input v-model="newDeploymentTaskEnabled" type="checkbox"> 启用任务</label><button class="primary">保存运行任务</button></form></div></details>
        <div class="task-list"><article v-for="task in deploymentTasks" :key="task.id" class="task-card"><header><div><h4>{{ task.task?.name }}</h4><p><code>{{ task.task?.code }}</code> · {{ task.task?.knowledge_type }} · {{ task.index_profile?.code }}</p></div><div class="task-summary"><span class="badge" :class="task.enabled?'green':'amber'">{{ task.enabled?'已启用':'未启用' }}</span><span>{{ orgRoutesForTask(authorizations,task.id).length }} 个授权</span><span>召回 {{ task.top_k }} / 最终 {{ task.final_top_k }}</span><button type="button" :disabled="!!deletingTaskId" @click="toggleTask(task.id)">{{ expandedTaskId===task.id?'收起':'配置任务' }}</button><button type="button" :disabled="!!deletingTaskId" @click="deleteDeploymentTask(task)">{{ deletingTaskId===task.id?'正在删除…':'删除任务' }}</button></div></header><div v-if="expandedTaskId===task.id" class="task-editor"><section class="task-contract"><h4>基本信息</h4><p>任务标识 <code>{{ task.task?.code }}</code></p><p>知识类型 {{ task.task?.knowledge_type }}</p><p>索引配置 {{ task.index_profile?.code }}</p></section><RetrievalTaskSettings :project-id="projectId" :task="task" @saved="loadDeployment" /><section class="authorization-editor stack"><div class="panel-head"><div><h4>知识授权</h4><p>当前任务按 org_code 隔离可访问知识库。</p></div><button type="button" @click="startNewOrgRoute">新增授权范围</button></div><label>授权范围<select v-model="orgRouteId"><option value="__new__">新增 org_code 范围</option><option v-for="route in selectedTaskRoutes" :key="route.id" :value="route.id">{{ route.org_name || route.org_code }} · {{ route.org_code }}</option></select></label><label v-if="orgRouteId==='__new__'&&orgCodePresets.length">预配置机构<select v-model="orgPresetCode" @change="applyOrgPreset"><option value="">自定义 org_code</option><option v-for="preset in orgCodePresets" :key="preset.org_code" :value="preset.org_code">{{ preset.name }} · {{ preset.org_code }}</option></select></label><div class="grid2"><label>组织授权编码<input v-model="orgCode" required maxlength="120" :readonly="orgRouteId!=='__new__'" @input="markCustomOrgCode"></label><label>授权范围名称<input v-model="orgName" maxlength="255"></label></div><p class="muted">org_code 可与 institution_code 相同，也可独立配置。</p><KnowledgeScopePanel :libraries="availableLibraries" :chosen="chosen" @toggle="toggleLibrary" @move="moveLibrary" @reorder="reorderLibrary" /><button class="primary" :disabled="!orgCode.trim()||!chosen.length" @click="saveRoute">保存知识授权</button></section></div></article><p v-if="!deploymentTasks.length" class="muted">当前项目还没有检索任务。</p></div>
      </ProjectTaskPanel>
    </section>

    <RetrievalDebugPanel v-else-if="tab==='validation'" :key="`${projectId}:${selectedStage}`" :project-id="projectId" :release-stage="selectedStage" :institution="false" :project-code="selectedProject?.code" deployment-code="dataforge-central" />
    <RoutingPublishPanel v-else-if="tab==='release'" :validation="validationView" :result="result" :diff="diffResult" :institution="freezesForInstitution" :ready="releaseReady" :busy="releaseBusy" :problems="publishState.problems" :action-label="releaseActionLabel" :libraries="libraries" @validate="preflight" @release="releaseCurrentStage" />
    <section v-else class="stack"><section class="panel"><div class="panel-head"><div><h3>{{ statusLabel(selectedStage) }}版本记录</h3><p>测试与生产版本独立编号、独立回滚；机构中心侧只记录冻结版本。</p></div></div><RouteVersionTable :versions="versions" :allow-rollback="!freezesForInstitution" @preview="showVersion" @rollback="rollback" /></section><section v-if="preview" class="panel stack version-detail"><div class="panel-head"><div><h3>版本 V{{ preview.version_no }}</h3><p>{{ preview.status }} · 相对 V{{ preview.previous_version_no || 0 }} 共 {{ preview.change_summary?.total || 0 }} 项变化</p></div><button type="button" @click="preview=null">关闭</button></div><div class="stats"><article class="stat-card"><small>任务</small><b>{{ preview.snapshot?.tasks?.length || 0 }}</b></article><article class="stat-card"><small>授权范围</small><b>{{ preview.snapshot?.routes?.length || 0 }}</b></article><article class="stat-card"><small>资产</small><b>{{ preview.assets?.length || 0 }}</b></article></div><article v-for="task in preview.snapshot?.tasks || []" :key="task.task_code" class="version-task"><h4>{{ task.task_name }} · <code>{{ task.task_code }}</code></h4><p>召回 {{ task.top_k }} · 最终 {{ task.final_top_k }} · {{ task.reranker_serving_code || '关闭重排' }}</p><p v-for="route in task.org_routes" :key="route.org_code">{{ route.org_name || route.org_code }} · {{ route.knowledge_library_ids.length }} 个知识库</p></article><details><summary>变更明细</summary><pre>{{ JSON.stringify(preview.changes,null,2) }}</pre></details><details><summary>RoutingSnapshot JSON</summary><pre>{{ JSON.stringify(preview.snapshot,null,2) }}</pre></details></section></section>
    <p v-if="notice" class="notice">{{ notice }}</p><p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.publishing-workbench{display:grid;gap:16px}.publishing-context{display:grid;grid-template-columns:minmax(220px,1fr) minmax(220px,1fr) minmax(300px,1.4fr);gap:18px;align-items:start}.publishing-context>div{display:grid;gap:7px}.publishing-context small{color:var(--muted);font-weight:750}.primary-tabs{margin:0}.workbench-section{gap:16px}.configuration-details{padding-top:8px;border-top:1px solid var(--border)}.configuration-details>summary{padding:8px 0;color:var(--blue);font-weight:750;cursor:pointer}.task-list{display:grid;gap:14px;margin-top:16px}.task-card{border:1px solid var(--border);border-radius:12px;background:#fbfcfe}.task-card>header{display:flex;justify-content:space-between;gap:18px;align-items:center;padding:16px}.task-card h4{margin:0 0 5px}.task-summary{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.task-editor{display:grid;grid-template-columns:minmax(180px,.7fr) minmax(260px,1fr) minmax(320px,1.3fr);gap:16px;padding:16px;border-top:1px solid var(--border);background:#fff}.task-contract,.authorization-editor{padding:14px;border:1px solid var(--border);border-radius:10px}.version-detail{margin-top:0}.version-task{padding:14px;border:1px solid var(--border);border-radius:10px}@media(max-width:1200px){.publishing-context,.task-editor{grid-template-columns:1fr}.task-card>header{align-items:flex-start;flex-direction:column}}
</style>
