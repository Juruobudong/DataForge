<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const projects = ref([]), libraries = ref([]), projectId = ref(''), taskId = ref('')
const name = ref(''), taskCode = ref(''), taskName = ref(''), orgCode = ref('general'), chosen = ref([])
const result = ref(null), versions = ref([]), preview = ref(null), error = ref(''), tab = ref('config')
const tasks = computed(() => projects.value.flatMap(project => project.tasks.map(task => ({ ...task, projectCode: project.code }))))
const selectedProject = computed(() => projects.value.find(project => project.id === projectId.value))
async function load() { [projects.value, libraries.value] = await Promise.all([api.projects(), api.knowledgeLibraries()]); if (projectId.value) await loadVersions() }
async function choose(id) { projectId.value = id; tab.value = 'config'; await loadVersions() }
async function loadVersions() { if (projectId.value) versions.value = await api.routeVersions(projectId.value) }
async function createProject() { try { await api.createProject({ name: name.value }); name.value = ''; await load() } catch (e) { error.value = e.message } }
async function createTask() { try { await api.createProjectTask(projectId.value, { code: taskCode.value, name: taskName.value }); taskCode.value = ''; taskName.value = ''; await load() } catch (e) { error.value = e.message } }
async function saveRoute() { try { result.value = await api.putRoute(taskId.value, { org_code: orgCode.value, knowledge_library_ids: chosen.value }); await load() } catch (e) { error.value = e.message } }
async function diff() { try { result.value = await api.routingDiff(projectId.value); tab.value = 'diff' } catch (e) { error.value = e.message } }
async function validate() { try { result.value = await api.validateRouting(projectId.value); tab.value = 'diff' } catch (e) { error.value = e.message } }
async function publish() { try { result.value = await api.publishRouting(projectId.value); await loadVersions(); tab.value = 'history' } catch (e) { error.value = e.message } }
async function showVersion(version) { try { preview.value = await api.routeVersion(projectId.value, version); tab.value = 'preview' } catch (e) { error.value = e.message } }
async function rollback(version) { try { if (!window.confirm(`以 v${version} 生成新的发布版本？`)) return; result.value = await api.rollbackRouting(projectId.value, version); await loadVersions() } catch (e) { error.value = e.message } }
onMounted(load)
</script>

<template>
  <section>
    <div class="page-head"><div><h2>项目知识授权</h2><p>配置 <code>project + task + org_code → knowledge libraries</code>；Collection 和 Partition 自动派生。</p></div><div class="page-actions"><span class="badge blue">RoutingSnapshot</span></div></div>
    <div class="project-layout">
      <aside class="project-list"><p class="nav-group-title">项目</p><button v-for="project in projects" :key="project.id" class="project-item" :class="{active:project.id===projectId}" @click="choose(project.id)"><b>{{ project.name }}</b><small>{{ project.code }} · {{ project.tasks.length }} 个任务</small></button></aside>
      <div>
        <section class="panel"><div class="panel-head"><div><h3>{{ selectedProject?.name || '选择或新建项目' }}</h3><p>知识库授权仅管理路由，索引映射由 DataForge 自动解析。</p></div><span class="badge" :class="projectId ? 'green' : 'amber'">{{ projectId ? '已选择项目' : '等待选择' }}</span></div></section>
        <nav class="tabs"><button :class="{active:tab==='config'}" @click="tab='config'">知识授权</button><button :class="{active:tab==='diff'}" :disabled="!projectId" @click="diff">待发布变更</button><button :class="{active:tab==='preview'}" :disabled="!preview" @click="tab='preview'">Routing 预览</button><button :class="{active:tab==='history'}" :disabled="!projectId" @click="tab='history'">发布记录</button></nav>
        <template v-if="tab==='config'">
          <form @submit.prevent="createProject"><input v-model="name" required placeholder="项目名称"><button class="primary">新建项目</button></form>
          <form @submit.prevent="createTask"><select v-model="projectId" required><option value="">选择项目</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select><input v-model="taskCode" required placeholder="任务编码"><input v-model="taskName" required placeholder="任务名称"><button>新建任务</button></form>
          <form class="stack" @submit.prevent="saveRoute"><label>Project / Task<select v-model="taskId" required><option value="">选择任务</option><option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.projectCode }} / {{ task.code }}</option></select></label><label>org_code<input v-model="orgCode" required placeholder="例如 general"></label><label>可授权知识库<select v-model="chosen" multiple required><option v-for="library in libraries.filter(item=>item.status==='active')" :key="library.id" :value="library.id">{{ library.name }} · {{ library.knowledge_type }} · {{ library.vector_ready?'Ready':'未就绪' }}</option></select></label><button class="primary">保存 Draft 路由</button></form>
          <div class="actions"><button :disabled="!projectId" @click="diff">查看结构化 Diff</button><button class="success" :disabled="!projectId" @click="validate">校验配置</button><button class="primary" :disabled="!projectId" @click="publish">发布授权配置</button></div>
        </template>
        <section v-else-if="tab==='diff'" class="panel"><h3>待发布变更与校验结果</h3><p>仅展示当前项目的授权差异；发布前必须通过路由与 Vector Ready 校验。</p><pre v-if="result">{{ JSON.stringify(result,null,2) }}</pre><p v-else>尚未生成变更。</p></section>
        <section v-else-if="tab==='preview'" class="panel"><h3>RoutingSnapshot 预览</h3><pre v-if="preview">{{ JSON.stringify(preview.snapshot,null,2) }}</pre></section>
        <section v-else class="panel"><h3>发布记录</h3><table><thead><tr><th>版本</th><th>状态</th><th>发布时间</th><th>操作</th></tr></thead><tbody><tr v-for="version in versions" :key="version.id"><td>v{{ version.version_no }}</td><td><span class="badge" :class="version.status==='published'?'green':'amber'">{{ version.status }}</span></td><td>{{ version.published_at || '未发布' }}</td><td><button @click="showVersion(version.version_no)">预览</button><button v-if="version.status==='published'" @click="rollback(version.version_no)">回滚到此版本</button></td></tr></tbody></table></section>
      </div>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>
