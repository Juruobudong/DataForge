<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { api } from '../../api/platform'
import DataForgeFlowCanvas from '../../components/flow/DataForgeFlowCanvas.vue'
import KnowledgeTypesView from './KnowledgeTypesView.vue'
import { useRouter, useRoute } from 'vue-router'
import { deserializeDefinition, subflowPrimaryName, subflowSubtitle } from '../../components/flow/flowModel'
import FlowModeSwitch from '../../components/flow/authoring/FlowModeSwitch.vue'
import StandardFlowEditor from '../../components/flow/standard/StandardFlowEditor.vue'
import AdvancedFlowEditor from '../../components/flow/advanced/AdvancedFlowEditor.vue'
import { groupFlowTemplates, templateOutputSummary } from './templatePresentation'

const tabs = [{ key: 'templates', name: '模板' }, { key: 'knowledge-types', name: '知识类型' }, { key: 'subflows', name: '可复用子图' }]
const activeTab = ref('templates'), templates = ref([]), catalog = ref([]), subflows = ref([]), types = ref([]), managedTemplates = ref([])
const authoringMode = ref('advanced'), stageDefinition = ref(null)
const expandedSubflow = ref(null), miniNodes = ref([]), miniEdges = ref([])
const selected = ref(null), sampleResult = ref(null)
const result = ref(null), error = ref('')
const code = ref(''), name = ref(''), outputTypes = ref(['text'])
const sampleId = ref('guideline-md'), settingsOpen = ref(false), dirty = ref(false), loading = ref(false)
const editing = ref(false)
const advancedEditor = ref(null)
const router = useRouter()
const route = useRoute()

const typeOptions = computed(() => {
  const normal = types.value.filter(item => item.status === 'active' && item.current_revision && item.code !== 'graph').map(item => ({ code: item.code, name: item.name }))
  return [...normal, { code: 'graph:triple', name: '三元组图谱' }, { code: 'graph:semantic', name: '语义图谱' }]
})
const templateGroups = computed(() => groupFlowTemplates(templates.value))
const statusLabel = computed(() => !selected.value ? '新建草稿' : dirty.value ? '草稿 · 未保存' : `r${selected.value.revision || '-'} · 已保存`)

function outputFamily(value) { return value.startsWith('graph:') ? 'graph' : value }
function outputSummary(item) { return templateOutputSummary(item, types.value) }

async function edit(item) {
  if (dirty.value && selected.value?.id !== item.id && !window.confirm('当前画布有未保存修改，确定放弃并切换模板吗？')) return
  selected.value = item; code.value = item.code; name.value = item.name
  outputTypes.value = [...item.output_types].map(value => value === 'graph' ? 'graph:triple' : value)
  authoringMode.value = item.authoring_mode === 'standard' ? 'standard' : 'advanced'
  stageDefinition.value = authoringMode.value === 'standard' ? { schema_version: 1, template_code: item.managed_template_code, stages: item.definition?.stages || {} } : null
  result.value = null; sampleResult.value = null; error.value = ''
  editing.value = true
  if (authoringMode.value === 'advanced') { await nextTick(); advancedEditor.value?.loadDefinition(item.definition) }
}
function clearDraft() {
  selected.value = null
  code.value = ''; name.value = ''; outputTypes.value = ['text']
  authoringMode.value = 'advanced'; stageDefinition.value = null
  result.value = null; sampleResult.value = null; error.value = ''; dirty.value = false
  settingsOpen.value = false
  advancedEditor.value?.reset()
}
function reset() {
  if (dirty.value && !window.confirm('当前画布有未保存修改，确定新建模板吗？')) return
  clearDraft()
  editing.value = true
}
function exitEditing() {
  if (dirty.value && !window.confirm('当前画布有未保存修改，确定退出编辑吗？')) return
  clearDraft()
  editing.value = false
}
async function importBuiltin(item) {
  if (dirty.value && !window.confirm('当前画布有未保存修改，确定导入内置流程并放弃吗？')) return
  try {
    const derived = await api.materializeManagedFlow(item.managed_template_code || item.code)
    selected.value = null
    code.value = ''; name.value = ''; outputTypes.value = derived.output_types.map(value => value === 'graph' ? 'graph:triple' : value)
    authoringMode.value = 'advanced'; stageDefinition.value = null
    result.value = null; sampleResult.value = null; error.value = ''
    dirty.value = false; settingsOpen.value = false
    editing.value = true
    await nextTick()
    advancedEditor.value?.loadDefinition(derived.definition)
  } catch (e) { error.value = e.message }
}
function onEditorDirty() { dirty.value = true; sampleResult.value = null }
function onEditorError(message) { if (message) error.value = message }

async function load() {
  loading.value = true
  try { [templates.value, catalog.value, subflows.value, types.value, managedTemplates.value] = await Promise.all([api.flowTemplates(), api.operatorCatalog({ include_internal: true }), api.flowSubgraphs(), api.knowledgeTypes(), api.managedFlowTemplates()]) }
  catch (e) { error.value = e.message }
  finally { loading.value = false }
}
async function expandSubflow(item) {
  if (expandedSubflow.value?.id === item.id) { expandedSubflow.value = null; return }
  try { const detail = await api.flowSubgraphRevision(item.id, item.revision); expandedSubflow.value = detail; const graph = deserializeDefinition(detail.definition, catalog.value, subflows.value); miniNodes.value = graph.nodes; miniEdges.value = graph.edges } catch (e) { error.value = e.message }
}
function openSubflow(item) { router.push(`/developer/flow-templates/subgraphs/${item.id}/revisions/${item.revision}`) }
function buildBody() {
  if (authoringMode.value === 'standard') {
    const managedCode = selected.value?.managed_template_code || stageDefinition.value?.template_code || ''
    return { name: name.value, output_types: outputTypes.value, authoring_mode: 'standard', managed_template_code: managedCode, definition: stageDefinition.value || { schema_version: 1, template_code: managedCode, stages: {} } }
  }
  return { name: name.value, output_types: outputTypes.value, authoring_mode: 'advanced', managed_template_code: null, definition: advancedEditor.value?.serialize() || { schema_version: 3, nodes: [], edges: [] } }
}
function onStageDefinition(value) { stageDefinition.value = value; dirty.value = true }
async function onModeChange(mode) {
  if (mode === authoringMode.value) return
  if (mode === 'advanced' && authoringMode.value === 'standard') {
    if (selected.value && !window.confirm('进入高级编排将展开当前标准配置的完整执行 DAG，转换后可增删算子。已发布 Revision 不会被修改。确定继续吗？')) return
    if (selected.value) {
      try {
        const detached = await api.detachFlowToAdvanced(selected.value.id)
        authoringMode.value = 'advanced'; stageDefinition.value = null
        await load()
        const refreshed = templates.value.find(item => item.id === selected.value.id)
        if (refreshed) { selected.value = refreshed; edit(refreshed) }
        else { advancedEditor.value?.loadDefinition(detached.definition) }
        dirty.value = false
      } catch (e) { error.value = e.message }
      return
    }
    authoringMode.value = 'advanced'; stageDefinition.value = null; return
  }
  authoringMode.value = mode
  if (mode === 'standard' && selected.value?.managed_template_code) {
    stageDefinition.value = { schema_version: 1, template_code: selected.value.managed_template_code, stages: selected.value.definition?.stages || {} }
  }
}
async function save() {
  if (!code.value.trim() || !name.value.trim()) { error.value = '模板编码和名称不能为空'; return }
  if (selected.value?.is_builtin) {
    const saveAsCustom = window.confirm('当前编辑的是内置流程。\n\n点击「确定」另存为新的自定义流程（需填写新编码和名称）；\n点击「取消」覆盖内置流程。')
    if (saveAsCustom) {
      try {
        const derived = await api.materializeManagedFlow(selected.value.managed_template_code || selected.value.code)
        selected.value = null
        code.value = ''; name.value = ''
        outputTypes.value = derived.output_types.map(value => value === 'graph' ? 'graph:triple' : value)
        authoringMode.value = 'advanced'; stageDefinition.value = null
        dirty.value = false; settingsOpen.value = false
        await nextTick()
        advancedEditor.value?.loadDefinition(derived.definition)
        error.value = '已切换为「另存为自定义」，请填写新编码和名称后再次保存'
      } catch (e) { error.value = e.message }
      return
    }
  }
  try {
    error.value = ''; const body = buildBody()
    const response = selected.value ? await api.updateFlowTemplate(selected.value.id, body) : await api.createFlowTemplate({ ...body, code: code.value })
    result.value = response; await load()
    const refreshed = templates.value.find(item => item.id === (selected.value?.id || response.id)) || templates.value.find(item => item.code === code.value)
    if (refreshed) { selected.value = refreshed; edit(refreshed) } else dirty.value = false
  } catch (e) { error.value = e.message }
}
async function action(kind) {
  if (!selected.value) { error.value = '请先保存模板草稿'; return }
  if (kind === 'validate') {
    if (authoringMode.value === 'advanced' && !advancedEditor.value?.validate()) return
    if (dirty.value) { error.value = '当前画布尚未保存，请先保存草稿后再执行服务端编译校验'; return }
  }
  if (dirty.value && ['publish', 'default', 'sample'].includes(kind)) { error.value = '当前画布尚未保存，请先保存草稿，避免操作旧修订'; return }
  try {
    error.value = ''
    result.value = kind === 'validate' ? await api.validateFlowTemplate(selected.value.id)
      : kind === 'publish' ? await api.publishFlowTemplate(selected.value.id)
        : kind === 'default' ? await api.defaultFlowTemplate(selected.value.id)
          : kind === 'sample' ? await api.sampleFlowTemplate(selected.value.id, sampleId.value)
            : await api.archiveFlowTemplate(selected.value.id)
    if (kind === 'sample') sampleResult.value = result.value
    const selectedId = selected.value.id
    await load()
    selected.value = templates.value.find(item => item.id === selectedId) || null
  } catch (e) { error.value = e.message }
}
onMounted(() => { const tabFromQuery = route.query.tab; if (tabs.some(t => t.key === tabFromQuery)) activeTab.value = tabFromQuery; load() })
</script>

<template>
  <section class="template-page">
    <header class="template-page-head">
      <div><div class="title-row"><h2>知识流程</h2><span class="dsl-badge">Flow DSL v3</span></div><p>白名单算子、强类型端口与 Knowledge Sink 构成受控知识生产 DAG。</p></div>
      <div class="header-actions"><template v-if="editing"><button class="exit" @click="exitEditing">‹ 退出编辑</button><span class="save-state" :class="{ dirty }"><i></i>{{ statusLabel }}</span><button @click="settingsOpen=!settingsOpen">模板设置</button><button :disabled="!selected" @click="action('validate')">编译校验</button><button :disabled="!selected" @click="action('sample')">样例运行</button><button class="primary" :disabled="!selected" @click="action('publish')">发布快照</button></template></div>
    </header>
    <div class="page-tabs"><button v-for="tab in tabs" :key="tab.key" :class="{ active: activeTab===tab.key }" @click="activeTab=tab.key">{{ tab.name }}</button></div>

    <template v-if="activeTab==='templates'">
      <template v-if="!editing">
        <section class="template-strip">
          <button class="new-template" @click="reset">＋ 新建模板</button>
          <div class="template-groups">
            <section class="template-group">
              <header><b>内置流程</b><small>{{ templateGroups.builtin.length }} 项</small></header>
              <div class="template-list">
                <button v-for="item in templateGroups.builtin" :key="item.id" :class="{ active:selected?.id===item.id }" @click="edit(item)">
                  <span class="template-card-title"><b>{{ item.name }}</b><span class="builtin-tag">内置</span></span>
                  <small v-if="outputSummary(item)" class="output-summary">{{ outputSummary(item) }}</small>
                  <small class="template-meta">{{ item.code }} · r{{ item.revision || '-' }}<template v-if="item.is_default"> · 默认</template></small>
                  <span class="import-draft" role="button" tabindex="0" @click.stop.prevent="importBuiltin(item)" @keydown.enter.prevent="importBuiltin(item)">导入为草稿</span>
                </button>
              </div>
            </section>
            <section class="template-group">
              <header><b>自定义流程</b><small>{{ templateGroups.custom.length }} 项</small></header>
              <div v-if="templateGroups.custom.length" class="template-list">
                <button v-for="item in templateGroups.custom" :key="item.id" :class="{ active:selected?.id===item.id }" @click="edit(item)">
                  <span class="template-card-title"><b>{{ item.name }}</b><span v-if="item.needs_review_upgrade" class="upgrade-tag">需升级审核入口</span></span>
                  <small v-if="outputSummary(item)" class="output-summary">{{ outputSummary(item) }}</small>
                  <small class="template-meta">{{ item.code }} · r{{ item.revision || '-' }}<template v-if="item.is_default"> · 默认</template></small>
                </button>
              </div>
              <p v-else class="empty-template-group">尚无自定义流程，可通过“新建模板”创建。</p>
            </section>
          </div>
        </section>
      </template>
      <template v-else>
        <form v-if="settingsOpen" class="template-settings" @submit.prevent="save"><label>模板编码<input v-model="code" :disabled="!!selected" required placeholder="template-code" @input="dirty=true"></label><label>模板名称<input v-model="name" required placeholder="模板名称" @input="dirty=true"></label><fieldset><legend>正式输出</legend><label v-for="item in typeOptions" :key="item.code"><input v-model="outputTypes" type="checkbox" :value="item.code" @change="dirty=true">{{ item.name }}</label></fieldset><label>样例<select v-model="sampleId"><option value="guideline-md">指南 Markdown</option><option value="faq-csv">FAQ CSV</option><option value="case-txt">病例摘要</option></select></label><div class="settings-actions"><button v-if="selected" type="button" @click="action('default')">设为默认</button><button v-if="selected" type="button" class="danger" @click="action('archive')">归档</button><button class="primary">保存草稿</button></div></form>
        <div class="flow-toolbar">
          <div class="mode-bar"><FlowModeSwitch :model-value="authoringMode" :standard-disabled="authoringMode === 'advanced'" @update:model-value="onModeChange" /></div>
          <div><span class="selection-state">{{ authoringMode === 'standard' ? '标准配置 · 系统控制阶段顺序' : '高级编排 · Operator DAG' }}</span><button class="primary" @click="save">保存草稿</button></div>
        </div>
        <StandardFlowEditor v-if="authoringMode === 'standard'" :template="selected" :managed-templates="managedTemplates" :catalog="catalog" :output-types="outputTypes" @update:definition="onStageDefinition" />
        <AdvancedFlowEditor v-else ref="advancedEditor" :catalog="catalog" :subflows="subflows" :output-types="outputTypes" :sample-result="sampleResult" @dirty="onEditorDirty" @error="onEditorError" />
      </template>
    </template>

    <KnowledgeTypesView v-else-if="activeTab==='knowledge-types'" />
    <section v-else class="panel subflow-catalog"><div class="panel-head"><div><h3>可复用子图</h3><p>卡片可原地展开 Mini DAG；完整 DAG 按不可变 revision 查看。</p></div><span class="badge blue">{{ subflows.length }} 项</span></div><div class="subflow-grid"><article v-for="item in subflows" :key="item.id" :class="{expanded:expandedSubflow?.id===item.id}"><button class="subflow-title" @click="expandSubflow(item)"><span>◈</span><div><b>{{ subflowPrimaryName(item) }}</b><small v-if="subflowSubtitle(item)">{{ subflowSubtitle(item) }}</small><p>{{ item.description || '可复用受控子图' }} · {{ item.node_count }} 节点 / {{ item.edge_count }} 连线</p></div></button><div v-if="expandedSubflow?.id===item.id" class="mini-wrap"><DataForgeFlowCanvas v-model:nodes="miniNodes" v-model:edges="miniEdges" mode="mini" height="260" :canvas-id="`mini-${item.id}`" /><button class="primary" @click="openSubflow(item)">查看完整 DAG</button></div></article></div></section>
    <p v-if="error" class="error page-error">{{ error }}</p><pre v-if="result && activeTab==='templates'" class="action-result">{{ JSON.stringify(result,null,2) }}</pre>
  </section>
</template>

<style scoped>
.template-page{min-width:1164px}.template-page-head{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:12px}.title-row{display:flex;align-items:center;gap:9px}.title-row h2{margin:0;font-size:21px}.dsl-badge{padding:5px 8px;border:1px solid #d8e4ff;border-radius:999px;color:#2f6fed;background:#eaf1ff;font-size:8px;font-weight:850}.template-page-head p{margin:5px 0 0;color:#778499;font-size:10px}.header-actions{display:flex;align-items:center;gap:7px}.save-state{display:inline-flex;align-items:center;gap:6px;margin-right:4px;color:#627087;font-size:8px;font-weight:800}.save-state i{width:7px;height:7px;border-radius:50%;background:#1d8c65}.save-state.dirty i{background:#b97917}.page-tabs{display:flex;gap:4px;margin-bottom:10px;border-bottom:1px solid #dfe5ed}.page-tabs button{border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent}.page-tabs button.active{border-bottom-color:#2f6fed;color:#2f6fed}.template-strip{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:start;gap:10px;margin-bottom:9px}.new-template{white-space:nowrap}.template-groups{display:grid;gap:9px;min-width:0}.template-group{display:grid;grid-template-columns:84px minmax(0,1fr);align-items:start;gap:8px}.template-group>header{display:grid;gap:2px;padding-top:8px;color:#34445a}.template-group>header small{color:#8a97a8}.template-list{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}.template-list button{display:grid;min-width:190px;max-width:250px;text-align:left}.template-list button.active{border-color:#b9cff7;color:#2f6fed;background:#eff5ff}.template-card-title{display:flex;align-items:center;justify-content:space-between;gap:8px}.template-list b,.template-list small{display:block}.template-list small{margin-top:3px;color:#8290a3;font-size:7px}.builtin-tag{padding:2px 6px;border:1px solid #c9dafb;border-radius:999px;color:#2f6fed;background:#edf4ff;font-size:7px;font-weight:800}.template-list .output-summary{color:#6b7a8f;font-size:7px}.upgrade-tag{padding:2px 6px;border:1px solid #efcf91;border-radius:999px;color:#986316;background:#fff7e7;font-size:7px;font-weight:800}.template-settings{display:grid;grid-template-columns:minmax(180px,1fr) minmax(200px,1.4fr);gap:10px;padding:14px;border:1px solid var(--border);border-radius:11px;background:#fff;margin-bottom:9px}.template-settings>label{display:grid;gap:4px;color:#536177;font-weight:700}.template-settings input,.template-settings select{border:1px solid #dfe5ed}.template-settings fieldset{border:1px solid #dfe5ed;border-radius:8px}.template-settings fieldset legend{color:#536177;font-weight:700}.template-settings fieldset label{display:inline-flex;align-items:center;gap:5px;margin-right:12px;color:#536177;font-weight:400}.settings-actions{display:flex;gap:6px;align-items:center}.settings-actions .danger{color:#c0392b;border-color:#f0c4bc}.flow-toolbar{display:flex;align-items:center;justify-content:space-between;min-height:54px;padding:9px 12px;margin-bottom:10px;border:1px solid var(--border);border-radius:12px;background:#fff}.flow-toolbar .mode-bar{display:flex;align-items:center}.selection-state{color:#66758a;font-size:12px;margin-right:8px}.action-result{margin-top:12px;padding:12px;background:#182231;color:#d9e2ee;border-radius:10px;font:11px monospace;max-height:300px;overflow:auto}
</style>
<style scoped>
.template-page-head p,.template-list small,.template-settings>label,.template-settings legend,.template-settings fieldset label,.selection-state,.subflow-grid small,.subflow-grid p { font-size: var(--font-technical); }
.subflow-title { display:grid; grid-template-columns:24px minmax(0,1fr); gap:10px; align-items:start; text-align:left; }
.subflow-title>div { min-width:0; }
.subflow-title b,.subflow-title small,.subflow-title p { display:block; }
.subflow-title small { margin-top:3px; color:#7b8798; }
.subflow-title p { margin:6px 0 0; color:#5d6a7c; }
.dsl-badge,.save-state { font-size: var(--font-technical); }
.exit { color:#5a6b85; background:#fff; border-color:#dbe3ef; font-weight:800; }
.import-draft { margin-top:6px; align-self:start; color:#2f6fed; font-size:8px; font-weight:800; cursor:pointer; }
.import-draft:hover { text-decoration:underline; }
.template-page-head { gap: 28px; }
.template-settings { gap: 12px; padding: 16px; }
.flow-toolbar { min-height: 54px; padding: 9px 12px; }
</style>
