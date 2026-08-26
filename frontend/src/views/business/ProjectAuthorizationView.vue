<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/platform'
import PublishTargetPanel from '../../components/project-publishing/PublishTargetPanel.vue'
import ProjectTaskPanel from '../../components/project-publishing/ProjectTaskPanel.vue'
import KnowledgeScopePanel from '../../components/project-publishing/KnowledgeScopePanel.vue'
import RoutingPublishPanel from '../../components/project-publishing/RoutingPublishPanel.vue'
import RouteVersionTable from '../../components/project-publishing/RouteVersionTable.vue'
import { statusLabel } from '../../constants/statusLabels'
import { compatibleProfilesForTask, movePriority, preferredDeployment, qaEmbeddingMode, routingPublishReadiness, routingValidationView } from './projectPublishingModel'

const instance = ref(null), projects = ref([]), libraries = ref([]), sharedDeployments = ref([]), knowledgeTypes = ref([])
const projectId = ref(''), deploymentId = ref(''), selectedStage = ref('test'), tab = ref('target')
const deploymentTasks = ref([]), authorizations = ref([]), versions = ref([])
const deploymentTaskId = ref(''), chosen = ref([])
const institutionName = ref(''), institutionCode = ref(''), institutionTargetUri = ref('')
const newInstitutionName = ref(''), newInstitutionCode = ref(''), bindDeploymentId = ref('')
const showCreateProject = ref(false), newProjectName = ref('')
const newTaskCode = ref(''), newTaskName = ref(''), newTaskKnowledgeType = ref(''), newTaskDescription = ref('')
const newProjectTaskId = ref(''), newIndexProfileId = ref(''), newTopK = ref(10), newDeploymentTaskEnabled = ref(true)
const result = ref(null), preview = ref(null), error = ref(''), notice = ref('')

const selectedProject = computed(() => projects.value.find(project => project.id === projectId.value))
const deployments = computed(() => selectedProject.value?.deployments || [])
const local = computed(() => instance.value?.instance_mode === 'local')
const central = computed(() => instance.value?.instance_mode === 'central')
const selectedDeployment = computed(() => deployments.value.find(item => item.id === deploymentId.value))
const freezesForInstitution = computed(() => central.value && selectedDeployment.value?.scope === 'institution')
const currentStageTarget = computed(() => selectedDeployment.value?.stage_targets?.[selectedStage.value]?.milvus_url || '')
const isKgConsultation = computed(() => selectedProject.value?.code === 'kg-for-consultation')
const unboundDeployments = computed(() => {
  const bound = new Set(deployments.value.map(item => item.deployment_id))
  return sharedDeployments.value.filter(item => !bound.has(item.id) && item.scope === 'institution')
})
const isQaAgent = computed(() => [selectedProject.value?.code, selectedProject.value?.name]
  .map(value => String(value || '').trim().toLowerCase().replaceAll('_', '-').replaceAll(' ', '-'))
  .some(value => value === 'qa-agent' || value.startsWith('qa-agent-')))
const activeKnowledgeTypes = computed(() => knowledgeTypes.value.filter(item => item.status === 'active'))
const unboundProjectTasks = computed(() => {
  const bound = new Set(deploymentTasks.value.map(item => item.project_task_id))
  return (selectedProject.value?.tasks || []).filter(item => !bound.has(item.id))
})
const selectedProjectTask = computed(() => (selectedProject.value?.tasks || []).find(item => item.id === newProjectTaskId.value))
const compatibleProfiles = computed(() => compatibleProfilesForTask(selectedProjectTask.value, knowledgeTypes.value, isQaAgent.value))
const selectedIndexProfile = computed(() => compatibleProfiles.value.find(item => item.id === newIndexProfileId.value))
const selectedQaEmbeddingMode = computed(() => qaEmbeddingMode(selectedIndexProfile.value))
const selectedDeploymentTask = computed(() => deploymentTasks.value.find(item => item.id === deploymentTaskId.value))
const publishState = computed(() => routingPublishReadiness(deploymentTasks.value, authorizations.value, currentStageTarget.value))
const validationView = computed(() => routingValidationView(result.value))
const releaseActionLabel = computed(() => `${freezesForInstitution.value ? '冻结' : '发布'}${statusLabel(selectedStage.value)}版本`)
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

function chooseBoundDeployment() {
  if (deployments.value.some(item => item.id === deploymentId.value)) return
  deploymentId.value = preferredDeployment(
    deployments.value, local.value ? instance.value?.bound_deployment_id : null,
  )?.id || ''
}
async function load() {
  try {
    error.value = ''
    ;[instance.value, projects.value, libraries.value, sharedDeployments.value, knowledgeTypes.value] = await Promise.all([
      api.instance(), api.projects(), api.knowledgeLibraries(), api.sharedDeployments(), api.knowledgeTypes(),
    ])
    projectId.value ||= projects.value[0]?.id || ''
    newTaskKnowledgeType.value ||= activeKnowledgeTypes.value[0]?.code || ''
    chooseBoundDeployment()
  } catch (e) { error.value = e.message }
}
async function loadDeployment() {
  result.value = null; preview.value = null
  if (!deploymentId.value) { deploymentTasks.value = []; authorizations.value = []; versions.value = []; return }
  try {
    institutionName.value = selectedDeployment.value?.institution_name || ''
    institutionCode.value = selectedDeployment.value?.institution_code || ''
    institutionTargetUri.value = currentStageTarget.value
    ;[deploymentTasks.value, authorizations.value, versions.value] = await Promise.all([
      api.deploymentTasks(deploymentId.value), api.authorizations(deploymentId.value), api.routeVersions(deploymentId.value, selectedStage.value),
    ])
    deploymentTaskId.value ||= deploymentTasks.value.find(item => item.enabled)?.id || ''
    syncChosen()
  } catch (e) { error.value = e.message }
}
function syncChosen() {
  const route = authorizations.value.find(item => item.project_deployment_task_id === deploymentTaskId.value)
  chosen.value = [...(route?.knowledge_library_ids || [])].filter(id => availableLibraries.value.some(item => item.id === id))
}
async function createProject() {
  try {
    const created = await api.createProject({ name: newProjectName.value.trim() })
    newProjectName.value = ''; showCreateProject.value = false
    await load(); projectId.value = created.id; chooseBoundDeployment(); await loadDeployment()
    tab.value = 'tasks'; notice.value = `项目「${created.name}」已创建，并已绑定 DataForge 中心。`
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
    await api.createDeploymentTask(deploymentId.value, { project_task_id: newProjectTaskId.value, index_profile_id: newIndexProfileId.value, qa_embedding_mode: selectedQaEmbeddingMode.value, top_k: Number(newTopK.value), enabled: newDeploymentTaskEnabled.value })
    newProjectTaskId.value = ''; newIndexProfileId.value = ''; newTopK.value = 10; newDeploymentTaskEnabled.value = true
    await loadDeployment(); notice.value = '检索通道已创建。'
  } catch (e) { error.value = e.message }
}
function toggleLibrary(id) { chosen.value = chosen.value.includes(id) ? chosen.value.filter(value => value !== id) : [...chosen.value, id] }
function moveLibrary(id, offset) {
  chosen.value = movePriority(chosen.value, id, offset)
}
async function saveRoute() {
  try {
    const institution = selectedDeployment.value?.scope === 'institution'
    result.value = await api.putDeploymentRoute(deploymentId.value, deploymentTaskId.value, {
      org_code: institution ? selectedDeployment.value.institution_code : 'general',
      org_name: institution ? selectedDeployment.value.institution_name : '', knowledge_library_ids: chosen.value,
    })
    await loadDeployment(); notice.value = '知识范围已保存，列表顺序即检索优先级。'
  } catch (e) { error.value = e.message }
}
async function saveInstitutionIdentity() {
  try { await api.patchSharedDeployment(selectedDeployment.value.deployment_id, { institution_name: institutionName.value, institution_code: institutionCode.value }); await load(); await loadDeployment(); notice.value = '机构身份已保存。' }
  catch (e) { error.value = e.message }
}
async function saveInstitutionTarget() {
  const uri = institutionTargetUri.value.trim()
  if (!uri) return
  const production = selectedStage.value === 'production'
  if (production && !window.confirm(`确认保存生产环境 Milvus 服务地址？\n发布目标：${selectedDeployment.value?.name}\nMilvus：${uri}`)) return
  try {
    await api.putDeploymentTarget(selectedDeployment.value.deployment_id, selectedStage.value, { milvus_uri: uri, confirm_production: production, expected_target_uri: production ? uri : null })
    await load(); await loadDeployment(); notice.value = `${statusLabel(selectedStage.value)} Milvus 服务地址已保存。`
  } catch (e) { error.value = e.message }
}
async function createInstitutionDeployment() {
  try {
    const created = await api.createSharedDeployment({ institution_name: newInstitutionName.value.trim(), institution_code: newInstitutionCode.value.trim() })
    const binding = await api.bindDeploymentProject(created.id, projectId.value)
    newInstitutionName.value = ''; newInstitutionCode.value = ''; await load(); deploymentId.value = binding.id; await loadDeployment(); notice.value = `发布目标「${created.name}」已创建。`
  } catch (e) { error.value = e.message }
}
async function bindExistingDeployment() {
  if (!bindDeploymentId.value) return
  try { const binding = await api.bindDeploymentProject(bindDeploymentId.value, projectId.value); bindDeploymentId.value = ''; await load(); deploymentId.value = binding.id; await loadDeployment() }
  catch (e) { error.value = e.message }
}
async function diff() { try { result.value = await api.routingDiff(deploymentId.value, selectedStage.value); preview.value = null } catch (e) { error.value = e.message } }
async function validate() { try { result.value = await api.validateRouting(deploymentId.value, selectedStage.value); preview.value = null } catch (e) { error.value = e.message } }
function releaseBody(production = false) { return { release_stage: selectedStage.value, expected_target_uri: currentStageTarget.value, confirm_production: production } }
async function releaseCurrentStage() {
  if (!publishState.value.ready) { error.value = publishState.value.problems.join('；'); return }
  if (isKgConsultation.value && selectedStage.value !== 'test') { error.value = 'kg_for_consultation 第一阶段只允许发布测试环境'; return }
  if (freezesForInstitution.value) {
    try { result.value = await api.freezeRouting(deploymentId.value, selectedStage.value); await loadDeployment(); tab.value = 'history'; notice.value = `${statusLabel(selectedStage.value)}项目版本已冻结；请到“机构发布部署”继续生成机构 Release。` }
    catch (e) { error.value = e.message }
    return
  }
  const production = selectedStage.value === 'production'
  if (production && !window.confirm(`确认发布生产环境？\n发布目标：${selectedDeployment.value?.name}\nMilvus：${currentStageTarget.value}\n发布后中心生产 Runtime 将立即使用新版本。`)) return
  try { result.value = await api.publishRouting(deploymentId.value, releaseBody(production)); await loadDeployment(); tab.value = 'history' }
  catch (e) { error.value = e.message }
}
async function showVersion(version) { try { preview.value = await api.routeVersion(deploymentId.value, version, selectedStage.value); result.value = null; tab.value = 'routing' } catch (e) { error.value = e.message } }
async function rollback(version) {
  const production = selectedStage.value === 'production'
  const message = production ? `确认回滚生产环境到 v${version} 的配置并发布新版本？\nMilvus：${currentStageTarget.value}` : `恢复测试环境 v${version} 的配置并发布新版本？`
  if (!window.confirm(message)) return
  try { await api.rollbackRouting(deploymentId.value, version, releaseBody(production)); await loadDeployment() } catch (e) { error.value = e.message }
}

watch(projectId, () => { selectedStage.value = 'test'; deploymentId.value = ''; chooseBoundDeployment(); newProjectTaskId.value = ''; newIndexProfileId.value = '' })
watch(deploymentId, () => { selectedStage.value = 'test'; loadDeployment() })
watch(selectedStage, async () => { result.value = null; preview.value = null; institutionTargetUri.value = currentStageTarget.value; if (deploymentId.value) versions.value = await api.routeVersions(deploymentId.value, selectedStage.value) })
watch(deploymentTaskId, syncChosen)
watch(newProjectTaskId, () => { newIndexProfileId.value = compatibleProfiles.value[0]?.id || '' })
onMounted(load)
</script>

<template>
  <section>
    <div class="page-head"><div><h2>项目发布</h2><p>选择发布目标和环境，配置检索通道、知识范围并生成独立发布版本。</p></div><div class="page-actions"><span class="badge blue">{{ local ? '机构本地' : '中心控制面' }}</span><button v-if="central" class="primary" @click="showCreateProject=!showCreateProject">{{ showCreateProject?'取消新增':'新增项目' }}</button></div></div>
    <form v-if="showCreateProject" class="panel stack" @submit.prevent="createProject"><h3>新增项目</h3><label>项目名称<input v-model="newProjectName" required maxlength="255"></label><button class="primary">创建项目</button></form>
    <section class="panel"><div class="panel-head"><div><h3>{{ selectedProject?.name || '暂无项目' }}</h3><p>项目创建后自动绑定 DataForge 中心。</p></div><div class="page-actions"><select v-model="projectId"><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select><select v-if="!local" v-model="deploymentId"><option v-for="deployment in deployments" :key="deployment.id" :value="deployment.id">{{ deployment.name }}</option></select></div></div></section>
    <nav class="tabs"><button :class="{active:tab==='target'}" @click="tab='target'">发布目标</button><button :class="{active:tab==='tasks'}" @click="tab='tasks'">任务配置</button><button :class="{active:tab==='scope'}" @click="tab='scope'">知识范围</button><button :class="{active:tab==='routing'}" @click="tab='routing'">发布配置</button><button :class="{active:tab==='history'}" @click="tab='history'">发布记录</button></nav>
    <PublishTargetPanel v-if="tab==='target'" :deployment="selectedDeployment" :selected-stage="selectedStage" :target-uri="currentStageTarget" @update:selected-stage="selectedStage=$event">
      <form v-if="selectedDeployment?.scope==='institution'" class="stack" @submit.prevent="saveInstitutionIdentity"><label>机构名称<input v-model="institutionName" required></label><label>机构代码<input v-model="institutionCode" required :readonly="selectedDeployment.institution_code_locked"></label><button>保存机构身份</button></form>
      <form v-if="selectedDeployment?.scope==='institution'&&!local" class="stack" @submit.prevent="saveInstitutionTarget"><label>{{ statusLabel(selectedStage) }} Milvus 服务地址<input v-model="institutionTargetUri" required placeholder="http://hospital-milvus:19531"></label><button>保存 Milvus 服务地址</button></form>
      <p v-if="selectedDeployment?.scope==='central'" class="notice">DataForge 中心的测试与生产 Milvus 服务由平台配置维护，此处只读。</p>
      <form v-if="!local" class="stack" @submit.prevent="createInstitutionDeployment"><h4>新增机构发布目标</h4><label>机构名称<input v-model="newInstitutionName" required></label><label>机构代码<input v-model="newInstitutionCode" required></label><button>创建并绑定当前项目</button></form>
      <form v-if="!local&&unboundDeployments.length" class="stack" @submit.prevent="bindExistingDeployment"><h4>绑定已有发布目标</h4><select v-model="bindDeploymentId" required><option value="">选择发布目标</option><option v-for="item in unboundDeployments" :key="item.id" :value="item.id">{{ item.name }} · {{ item.institution_code }}</option></select><button>绑定当前项目</button></form>
    </PublishTargetPanel>
    <ProjectTaskPanel v-else-if="tab==='tasks'" :count="deploymentTasks.length"><div class="grid2"><form class="stack" @submit.prevent="createProjectTask"><h4>1. 新增业务任务</h4><label>任务编码<input v-model="newTaskCode" required></label><label>任务名称<input v-model="newTaskName" required></label><label>知识类型<select v-model="newTaskKnowledgeType" required><option v-for="item in activeKnowledgeTypes" :key="item.id" :value="item.code">{{ item.name }}</option></select></label><label>任务说明<textarea v-model="newTaskDescription" rows="3"></textarea></label><button class="primary">创建业务任务</button></form><form class="stack" @submit.prevent="createDeploymentTask"><h4>2. 配置检索通道</h4><label>业务任务<select v-model="newProjectTaskId" required><option value="">选择业务任务</option><option v-for="task in unboundProjectTasks" :key="task.id" :value="task.id">{{ task.name }}</option></select></label><label>索引配置<select v-model="newIndexProfileId" required><option value="">选择索引配置</option><option v-for="profile in compatibleProfiles" :key="profile.id" :value="profile.id">{{ profile.code }}</option></select></label><label v-if="selectedQaEmbeddingMode">QA 向量化方式<input :value="selectedQaEmbeddingMode==='question'?'问题文本':'问题与答案全文'" readonly></label><label>候选数量 Top K<input v-model.number="newTopK" type="number" min="1" required></label><label><input v-model="newDeploymentTaskEnabled" type="checkbox"> 启用</label><button class="primary">保存检索通道</button></form></div><table><thead><tr><th>业务任务</th><th>知识类型</th><th>索引配置</th><th>Top K</th><th>状态</th></tr></thead><tbody><tr v-for="task in deploymentTasks" :key="task.id"><td>{{ task.task?.name }}</td><td>{{ task.task?.knowledge_type }}</td><td>{{ task.index_profile?.code }}</td><td>{{ task.top_k }}</td><td>{{ task.enabled?'启用':'未启用' }}</td></tr></tbody></table></ProjectTaskPanel>
    <section v-else-if="tab==='scope'" class="panel stack"><div class="panel-head"><div><h3>知识范围</h3><p>{{ selectedDeployment?.name }} · {{ selectedDeployment?.scope==='institution'?selectedDeployment?.institution_code:'general' }}</p></div></div><label>检索通道<select v-model="deploymentTaskId" required><option value="">选择检索通道</option><option v-for="task in deploymentTasks.filter(item=>item.enabled)" :key="task.id" :value="task.id">{{ task.task?.name }}</option></select></label><KnowledgeScopePanel :libraries="availableLibraries" :chosen="chosen" @toggle="toggleLibrary" @move="moveLibrary" /><button class="primary" :disabled="!deploymentTaskId||!chosen.length" @click="saveRoute">保存知识范围</button></section>
    <RoutingPublishPanel v-else-if="tab==='routing'" :validation="validationView" :result="result" :preview="preview" :institution="freezesForInstitution" :ready="publishState.ready" :problems="publishState.problems" :action-label="releaseActionLabel" @diff="diff" @validate="validate" @release="releaseCurrentStage" />
    <section v-else class="panel"><div class="panel-head"><div><h3>{{ statusLabel(selectedStage) }}发布记录</h3><p>测试与生产版本独立编号、独立回滚。</p></div></div><RouteVersionTable :versions="versions" :allow-rollback="!freezesForInstitution" @preview="showVersion" @rollback="rollback" /></section>
    <p v-if="notice" class="notice">{{ notice }}</p><p v-if="error" class="error">{{ error }}</p>
  </section>
</template>
