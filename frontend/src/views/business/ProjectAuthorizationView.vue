<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/platform'
import { compatibleProfilesForTask, qaEmbeddingMode, routingPublishReadiness } from './projectPublishingModel'

const instance = ref(null), projects = ref([]), libraries = ref([]), sharedDeployments = ref([]), knowledgeTypes = ref([]), projectId = ref(''), deploymentId = ref('')
const deploymentTasks = ref([]), authorizations = ref([]), versions = ref([])
const deploymentTaskId = ref(''), orgCode = ref(''), orgName = ref(''), chosen = ref([])
const institutionName = ref(''), institutionCode = ref(''), desiredStage = ref('test')
const newDeploymentCode = ref(''), newDeploymentName = ref(''), newInstitutionName = ref(''), newInstitutionCode = ref('')
const productionUri = ref(''), bindDeploymentId = ref('')
const showCreateProject = ref(false), newProjectName = ref('')
const newTaskCode = ref(''), newTaskName = ref(''), newTaskKnowledgeType = ref(''), newTaskDescription = ref('')
const newProjectTaskId = ref(''), newIndexProfileId = ref(''), newTopK = ref(10), newDeploymentTaskEnabled = ref(true)
const tab = ref('project'), result = ref(null), preview = ref(null), error = ref(''), notice = ref('')
const selectedProject = computed(() => projects.value.find(project => project.id === projectId.value))
const deployments = computed(() => selectedProject.value?.deployments || [])
const local = computed(() => instance.value?.instance_mode === 'local')
const central = computed(() => instance.value?.instance_mode === 'central')
const selectedDeployment = computed(() => deployments.value.find(item => item.id === deploymentId.value))
const deploymentLabel = computed(() => `${selectedDeployment.value?.name || '—'} (${selectedDeployment.value?.code || '—'})`)
const isKgConsultation = computed(() => selectedProject.value?.code === 'kg-for-consultation')
const testMilvusTarget = computed(() => selectedDeployment.value?.stage_targets?.test?.milvus_url || '')
const productionMilvusTarget = computed(() => selectedDeployment.value?.stage_targets?.production?.milvus_url || '')
const currentStageTarget = computed(() => selectedDeployment.value?.stage_targets?.[desiredStage.value]?.milvus_url || '')
const stageTarget = computed(() => currentStageTarget.value || '未配置')
const unboundDeployments = computed(() => {
  const bound = new Set(deployments.value.map(item => item.deployment_id))
  return sharedDeployments.value.filter(item => !bound.has(item.id))
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

async function load() {
  try {
    error.value = ''
    ;[instance.value, projects.value, libraries.value, sharedDeployments.value, knowledgeTypes.value] = await Promise.all([
      api.instance(), api.projects(), api.knowledgeLibraries(), api.sharedDeployments(), api.knowledgeTypes()
    ])
    if (!projectId.value && projects.value.length) projectId.value = projects.value[0].id
    if (!newTaskKnowledgeType.value) newTaskKnowledgeType.value = activeKnowledgeTypes.value[0]?.code || ''
    chooseBoundDeployment()
  } catch (e) { error.value = e.message }
}
function chooseBoundDeployment() {
  if (local.value) deploymentId.value = instance.value.bound_deployment_id || ''
  else if (!deployments.value.some(item => item.id === deploymentId.value)) deploymentId.value = deployments.value[0]?.id || ''
}
async function loadDeployment() {
  if (!deploymentId.value) {
    deploymentTasks.value = []; authorizations.value = []; versions.value = []
    return
  }
  try {
    institutionName.value = selectedDeployment.value?.institution_name || ''
    institutionCode.value = selectedDeployment.value?.institution_code || ''
    orgCode.value = institutionCode.value
    orgName.value = institutionName.value
    desiredStage.value = selectedDeployment.value?.release_stage || 'test'
    productionUri.value = selectedDeployment.value?.stage_targets?.production?.milvus_url || ''
    ;[deploymentTasks.value, authorizations.value, versions.value] = await Promise.all([
      api.deploymentTasks(deploymentId.value), api.authorizations(deploymentId.value),
      api.routeVersions(deploymentId.value, desiredStage.value)
    ])
  } catch (e) { error.value = e.message }
}
async function createProject() {
  try {
    error.value = ''; notice.value = ''
    const created = await api.createProject({ name: newProjectName.value.trim() })
    projectId.value = created.id
    newProjectName.value = ''; showCreateProject.value = false
    await load(); await loadDeployment()
    tab.value = 'tasks'
    notice.value = `Project「${created.name}」已创建，编码 ${created.code}，并已绑定中央 Deployment。`
  } catch (e) { error.value = e.message }
}
async function createProjectTask() {
  try {
    error.value = ''; notice.value = ''
    const created = await api.createProjectTask(projectId.value, {
      code: newTaskCode.value.trim(), name: newTaskName.value.trim(),
      knowledge_type: newTaskKnowledgeType.value, description: newTaskDescription.value.trim()
    })
    newTaskCode.value = ''; newTaskName.value = ''; newTaskDescription.value = ''
    await load()
    newProjectTaskId.value = created.id
    notice.value = `Project Task「${created.name}」已创建，请继续选择 Index Profile。`
  } catch (e) { error.value = e.message }
}
async function createDeploymentTask() {
  try {
    error.value = ''; notice.value = ''
    const payload = {
      project_task_id: newProjectTaskId.value,
      index_profile_id: newIndexProfileId.value,
      qa_embedding_mode: selectedQaEmbeddingMode.value,
      top_k: Number(newTopK.value),
      enabled: newDeploymentTaskEnabled.value,
    }
    const created = await api.createDeploymentTask(deploymentId.value, payload)
    newProjectTaskId.value = ''; newIndexProfileId.value = ''; newTopK.value = 10; newDeploymentTaskEnabled.value = true
    await loadDeployment()
    notice.value = `Deployment Task「${created.task?.name || created.id}」已创建。`
  } catch (e) { error.value = e.message }
}
async function saveRoute() {
  try {
    result.value = await api.putDeploymentRoute(deploymentId.value, deploymentTaskId.value, {
      org_code: orgCode.value, org_name: orgName.value, knowledge_library_ids: chosen.value
    })
    await loadDeployment()
  } catch (e) { error.value = e.message }
}
async function saveDeployment() {
  try {
    const sharedId = selectedDeployment.value?.deployment_id
    await api.patchSharedDeployment(sharedId, {
      institution_name: institutionName.value, institution_code: institutionCode.value
    })
    if (desiredStage.value !== selectedDeployment.value?.release_stage) {
      const body = { release_stage: desiredStage.value }
      if (desiredStage.value === 'production') {
      const message = `确认将「${institutionName.value || selectedDeployment.value?.name}」切换到生产发布？\nDeployment：${deploymentLabel.value}\n目标：${stageTarget.value}`
      if (!window.confirm(message)) { desiredStage.value = selectedDeployment.value?.release_stage || 'test'; return }
      body.confirm_production = true
      body.expected_target_uri = stageTarget.value
      }
      await api.patchDeploymentStage(sharedId, body)
    }
    await load()
    await loadDeployment()
  } catch (e) { error.value = e.message }
}
async function saveProductionTarget() {
  const sharedId = selectedDeployment.value?.deployment_id
  const uri = productionUri.value.trim()
  if (!uri || !window.confirm(`确认保存医院生产 Milvus 地址？\n医院：${institutionName.value || selectedDeployment.value?.name}\nDeployment：${deploymentLabel.value}\n目标：${uri}`)) return
  try {
    await api.putDeploymentTarget(sharedId, 'production', {
      milvus_uri: uri, confirm_production: true, expected_target_uri: uri
    })
    await load(); await loadDeployment()
  } catch (e) { error.value = e.message }
}
async function createHospitalDeployment() {
  try {
    const created = await api.createSharedDeployment({
      code: newDeploymentCode.value,
      name: newDeploymentName.value || newInstitutionName.value,
      institution_name: newInstitutionName.value,
      institution_code: newInstitutionCode.value,
      scope: 'institution',
      release_stage: 'test'
    })
    const binding = await api.bindDeploymentProject(created.id, projectId.value)
    newDeploymentCode.value = ''; newDeploymentName.value = ''; newInstitutionName.value = ''; newInstitutionCode.value = ''
    await load(); deploymentId.value = binding.id; await loadDeployment()
  } catch (e) { error.value = e.message }
}
async function bindExistingDeployment() {
  if (!bindDeploymentId.value) return
  try {
    const binding = await api.bindDeploymentProject(bindDeploymentId.value, projectId.value)
    bindDeploymentId.value = ''; await load(); deploymentId.value = binding.id; await loadDeployment()
  } catch (e) { error.value = e.message }
}
async function diff() { try { result.value = await api.routingDiff(deploymentId.value, desiredStage.value); tab.value = 'routing' } catch (e) { error.value = e.message } }
async function validate() { try { result.value = await api.validateRouting(deploymentId.value); tab.value = 'routing' } catch (e) { error.value = e.message } }
function releaseBody(confirmProduction = false) { return { expected_release_stage: desiredStage.value, expected_target_uri: stageTarget.value, confirm_production: confirmProduction } }
async function publish() {
  if (!publishState.value.ready) {
    error.value = publishState.value.problems.join('；')
    return
  }
  if (isKgConsultation.value && desiredStage.value !== 'test') {
    error.value = 'kg_for_consultation 第一阶段只允许发布测试 Routing'
    return
  }
  const production = desiredStage.value === 'production'
  if (production && !window.confirm(`确认发布「${institutionName.value}」生产 Routing？\nDeployment：${deploymentLabel.value}\n目标：${stageTarget.value}`)) return
  try { result.value = await api.publishRouting(deploymentId.value, releaseBody(production)); await loadDeployment(); tab.value = 'history' } catch (e) { error.value = e.message }
}
async function showVersion(version) { try { preview.value = await api.routeVersion(deploymentId.value, version, desiredStage.value); tab.value = 'routing' } catch (e) { error.value = e.message } }
async function rollback(version) {
  const production = desiredStage.value === 'production'
  const message = production ? `确认回滚「${institutionName.value}」生产版本 v${version}？\nDeployment：${deploymentLabel.value}\n目标：${stageTarget.value}` : `恢复测试版本 v${version} 的授权并发布新版本？`
  if (!window.confirm(message)) return
  try { result.value = await api.rollbackRouting(deploymentId.value, version, releaseBody(production)); await loadDeployment() } catch (e) { error.value = e.message }
}
watch(projectId, () => {
  chooseBoundDeployment()
  newProjectTaskId.value = ''; newIndexProfileId.value = ''
})
watch(deploymentId, loadDeployment)
watch(deploymentTaskId, () => { chosen.value = chosen.value.filter(id => availableLibraries.value.some(item => item.id === id)) })
watch(newProjectTaskId, () => { newIndexProfileId.value = compatibleProfiles.value[0]?.id || '' })
onMounted(load)
</script>

<template>
  <section>
    <div class="page-head"><div><h2>项目发布</h2><p>DeploymentTask 级授权是 RoutingSnapshot 的唯一配置来源。</p></div><div class="page-actions"><span class="badge blue">{{ local ? '本地自治' : '中心控制面' }}</span><button v-if="central" class="primary" @click="showCreateProject=!showCreateProject">{{ showCreateProject ? '取消新增' : '新增 Project' }}</button></div></div>
    <form v-if="central && showCreateProject" class="panel stack" @submit.prevent="createProject">
      <div class="panel-head"><div><h3>新增 Project</h3><p>只填写业务名称；编码由服务端生成，并自动绑定共享中央 Deployment。</p></div></div>
      <label>项目名称<input v-model="newProjectName" required maxlength="255" placeholder="例如：院内临床知识助手"></label>
      <button class="primary">创建 Project</button>
    </form>
    <section class="panel"><div class="panel-head"><div><h3>{{ selectedProject?.name || '暂无项目' }}</h3><p v-if="selectedProject"><code>{{ selectedProject.code }}</code></p><p v-if="local">本地化实例 · {{ selectedDeployment?.name || instance?.instance_code }}</p><p v-else>选择一个 Deployment 管理独立授权与发布版本。</p></div><div class="page-actions"><select v-model="projectId"><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select><select v-if="!local" v-model="deploymentId"><option v-for="deployment in deployments" :key="deployment.id" :value="deployment.id">{{ deployment.name }}</option></select></div></div></section>
    <nav class="tabs"><button :class="{active:tab==='project'}" @click="tab='project'">项目配置</button><button :class="{active:tab==='tasks'}" @click="tab='tasks'">任务配置</button><button :class="{active:tab==='auth'}" @click="tab='auth'">知识授权</button><button :class="{active:tab==='routing'}" @click="tab='routing'">Routing 发布</button><button :class="{active:tab==='history'}" @click="tab='history'">发布记录</button></nav>
    <section v-if="tab==='project'" class="panel">
      <h3>共享 Deployment</h3>
      <p>当前 Project 通过 ProjectDeployment 关联到 <code>{{ selectedDeployment?.code }}</code>；医院身份、阶段和 Target 由共享 Deployment 统一维护。</p>
      <p v-if="isKgConsultation"><span class="badge amber">kg_for_consultation 仅允许 test Snapshot</span></p>
      <div v-if="selectedDeployment" class="grid2">
        <div><small>测试 Milvus Target</small><p><code>{{ testMilvusTarget || '未配置' }}</code></p></div>
        <div><small>生产 Milvus Target</small><p><code>{{ productionMilvusTarget || '未配置' }}</code></p></div>
      </div>
      <form v-if="selectedDeployment" class="stack" @submit.prevent="saveDeployment">
        <template v-if="selectedDeployment.scope==='institution'">
          <label>医院机构名称<input v-model="institutionName" required></label>
          <label>医院机构代码<input v-model="institutionCode" required></label>
        </template>
        <p v-else>中心技术环境不伪造医院身份。</p>
        <label>当前阶段<select v-model="desiredStage"><option value="test">测试</option><option value="production">生产</option></select></label>
        <p>当前目标：<code>{{ stageTarget }}</code></p>
        <button class="primary">保存 Deployment 配置</button>
      </form>
      <form v-if="!local && selectedDeployment?.scope==='institution'" class="stack" @submit.prevent="saveProductionTarget">
        <h4>医院生产 Target</h4>
        <label>Production Milvus URI<input v-model="productionUri" required placeholder="http://hospital-milvus:19531"></label>
        <button>人工确认并保存生产地址</button>
      </form>
      <form v-if="!local" class="stack" @submit.prevent="createHospitalDeployment">
        <h4>新增医院 Deployment</h4>
        <label>稳定 Deployment code<input v-model="newDeploymentCode" required></label>
        <label>Deployment 名称<input v-model="newDeploymentName" required></label>
        <label>医院机构名称<input v-model="newInstitutionName" required></label>
        <label>医院机构代码<input v-model="newInstitutionCode" required></label>
        <p>新建默认使用中心测试 Milvus Target，可在创建后于「共享 Deployment」配置中修改。</p>
        <button>创建并绑定当前 Project</button>
      </form>
      <form v-if="!local && unboundDeployments.length" class="stack" @submit.prevent="bindExistingDeployment">
        <h4>绑定已有 Deployment</h4>
        <select v-model="bindDeploymentId" required><option value="">选择 Deployment</option><option v-for="item in unboundDeployments" :key="item.id" :value="item.id">{{ item.name }} · {{ item.code }}</option></select>
        <button>绑定当前 Project</button>
      </form>
    </section>
    <section v-else-if="tab==='tasks'" class="panel">
      <div class="panel-head"><div><h3>任务配置</h3><p>先定义 Project Task，再将它映射为当前 Deployment 的运行任务。</p></div><span class="badge blue">{{ deploymentTasks.length }} 个 Deployment Task</span></div>
      <div class="grid2">
        <form class="stack" @submit.prevent="createProjectTask">
          <h4>1. 新增 Project Task</h4>
          <label>任务编码<input v-model="newTaskCode" required maxlength="120" placeholder="例如：clinical_qa"></label>
          <label>任务名称<input v-model="newTaskName" required maxlength="255" placeholder="例如：临床问答"></label>
          <label>Knowledge Type<select v-model="newTaskKnowledgeType" required><option v-for="item in activeKnowledgeTypes" :key="item.id" :value="item.code">{{ item.name }} · {{ item.code }}</option></select></label>
          <label>任务说明<textarea v-model="newTaskDescription" rows="3" placeholder="选填"></textarea></label>
          <button class="primary" :disabled="!projectId">创建 Project Task</button>
        </form>
        <form class="stack" @submit.prevent="createDeploymentTask">
          <h4>2. 新增 Deployment Task</h4>
          <label>Project Task<select v-model="newProjectTaskId" required><option value="">选择尚未映射的任务</option><option v-for="task in unboundProjectTasks" :key="task.id" :value="task.id">{{ task.name }} · {{ task.knowledge_type }}</option></select></label>
          <label>Index Profile<select v-model="newIndexProfileId" required><option value="">选择兼容的已发布 Profile</option><option v-for="profile in compatibleProfiles" :key="profile.id" :value="profile.id">{{ profile.code }}</option></select></label>
          <p v-if="newProjectTaskId && !compatibleProfiles.length" class="muted">当前任务没有兼容的已发布 Index Profile，请先在流程开发区完成配置。</p>
          <label v-if="selectedQaEmbeddingMode">QA Embedding 模式<input :value="selectedQaEmbeddingMode" readonly></label>
          <label>Top K<input v-model.number="newTopK" type="number" min="1" required></label>
          <label><input v-model="newDeploymentTaskEnabled" type="checkbox"> 创建后启用</label>
          <button class="primary" :disabled="!deploymentId || !newProjectTaskId || !newIndexProfileId">创建 Deployment Task</button>
        </form>
      </div>
      <h4>Project Tasks</h4>
      <p v-if="!selectedProject?.tasks?.length" class="muted">当前 Project 还没有任务，请先完成上面的第 1 步。</p>
      <table v-else><thead><tr><th>任务</th><th>编码</th><th>Knowledge Type</th><th>说明</th></tr></thead><tbody><tr v-for="task in selectedProject.tasks" :key="task.id"><td>{{ task.name }}</td><td><code>{{ task.code }}</code></td><td>{{ task.knowledge_type }}</td><td>{{ task.description || '—' }}</td></tr></tbody></table>
      <h4>Deployment Tasks</h4>
      <p v-if="!deploymentTasks.length" class="muted">当前 Deployment 还没有运行任务，Routing 暂不能发布。</p>
      <table v-else><thead><tr><th>任务</th><th>类型</th><th>Profile</th><th>QA 模式</th><th>Top K</th><th>状态</th></tr></thead><tbody><tr v-for="task in deploymentTasks" :key="task.id"><td>{{ task.task?.name }}</td><td>{{ task.task?.knowledge_type }}</td><td>{{ task.index_profile?.code }}</td><td>{{ task.qa_embedding_mode || '—' }}</td><td>{{ task.top_k }}</td><td><span class="badge" :class="task.enabled?'green':'amber'">{{ task.enabled?'启用':'未启用' }}</span></td></tr></tbody></table>
    </section>
    <section v-else-if="tab==='auth'" class="panel"><h3>知识授权</h3><p v-if="!deploymentTasks.some(item=>item.enabled)" class="muted">请先在“任务配置”中创建并启用 Deployment Task。</p><form class="stack" @submit.prevent="saveRoute"><label>Deployment Task<select v-model="deploymentTaskId" required><option value="">选择任务</option><option v-for="task in deploymentTasks.filter(item=>item.enabled)" :key="task.id" :value="task.id">{{ task.task?.name }} / {{ task.index_profile?.code }}</option></select></label><label>org_code<input v-model="orgCode" required><small>默认使用医院机构代码，可按院内搜索配置调整。</small></label><label>机构名称<input v-model="orgName"></label><label>知识库<select v-model="chosen" multiple required><option v-for="library in availableLibraries" :key="library.id" :value="library.id">{{ library.name }} · {{ library.origin_type==='central_import'?'中心迁入':'本地创建' }}{{ library.origin_state==='forked'?' · 已本地修改':'' }}</option></select></label><button class="primary" :disabled="!deploymentTaskId">保存授权</button></form><pre v-if="authorizations.length">{{ JSON.stringify(authorizations,null,2) }}</pre></section>
    <section v-else-if="tab==='routing'" class="panel"><h3>Routing 校验与发布 · {{ desiredStage==='production'?'生产':'测试' }}</h3><p>{{ institutionName || selectedDeployment?.name }} · {{ stageTarget }}</p><p v-if="!publishState.ready" class="muted">发布前还需完成：{{ publishState.problems.join('；') }}</p><div class="actions"><button @click="diff">结构化 Diff</button><button class="success" @click="validate">校验</button><button class="primary" :disabled="!publishState.ready" @click="publish">发布</button></div><pre v-if="preview">{{ JSON.stringify(preview.snapshot,null,2) }}</pre><pre v-else-if="result">{{ JSON.stringify(result,null,2) }}</pre></section>
    <section v-else class="panel"><h3>{{ desiredStage==='production'?'生产':'测试' }}发布记录</h3><table><thead><tr><th>版本</th><th>阶段</th><th>来源</th><th>状态</th><th>发布时间</th><th>操作</th></tr></thead><tbody><tr v-for="version in versions" :key="version.id"><td>v{{ version.version_no }}</td><td>{{ version.release_stage }}</td><td>{{ version.origin }}</td><td>{{ version.status }}</td><td>{{ version.published_at || '—' }}</td><td><button @click="showVersion(version.version_no)">预览</button><button v-if="version.status==='published'" @click="rollback(version.version_no)">回滚</button></td></tr></tbody></table></section>
    <p v-if="notice" class="muted">{{ notice }}</p>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>
