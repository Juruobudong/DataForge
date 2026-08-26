<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { api } from '../../api/platform'
import { useRouter, useRoute } from 'vue-router'
import StandardFlowEditor from '../../components/flow/standard/StandardFlowEditor.vue'
import AdvancedFlowEditor from '../../components/flow/advanced/AdvancedFlowEditor.vue'
import FieldHelp from '../../components/common/FieldHelp.vue'
import { groupFlowTemplates, templateOutputSummary } from './templatePresentation'

const templates = ref([]), catalog = ref([]), subflows = ref([]), types = ref([]), managedTemplates = ref([])
const authoringMode = ref('advanced'), stageDefinition = ref(null)
const selected = ref(null), sampleResult = ref(null)
const result = ref(null), error = ref('')
const code = ref(''), name = ref(''), outputTypes = ref(['text'])
const sampleId = ref('guideline-md'), settingsOpen = ref(false), dirty = ref(false), loading = ref(false)
const editing = ref(false)
const wizardOpen = ref(false)
const advancedEditor = ref(null)
const router = useRouter()
const route = useRoute()
const advancedConversionHelp = '基于当前标准配置展开完整执行 DAG，并创建一个新的自定义高级流程。原标准流程不会被修改。'

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
function startNew(managedCode = '') {
  clearDraft(); editing.value = true; wizardOpen.value = false; settingsOpen.value = true
  if (!managedCode) { authoringMode.value = 'advanced'; outputTypes.value = ['text']; return }
  const managed = managedTemplates.value.find(item => item.code === managedCode)
  authoringMode.value = 'standard'
  outputTypes.value = [...(managed?.output_types || ['text'])]
  stageDefinition.value = { schema_version: 1, template_code: managedCode, stages: {} }
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
function buildBody() {
  if (authoringMode.value === 'standard') {
    const managedCode = selected.value?.managed_template_code || stageDefinition.value?.template_code || ''
    return { name: name.value, output_types: outputTypes.value, authoring_mode: 'standard', managed_template_code: managedCode, definition: stageDefinition.value || { schema_version: 1, template_code: managedCode, stages: {} } }
  }
  return { name: name.value, output_types: outputTypes.value, authoring_mode: 'advanced', managed_template_code: null, definition: advancedEditor.value?.serialize() || { schema_version: 3, nodes: [], edges: [] } }
}
function onStageDefinition(value) { stageDefinition.value = value; dirty.value = true }
async function convertToAdvanced() {
  if (!selected.value || authoringMode.value !== 'standard') return
  if (dirty.value) { error.value = '请先保存当前标准配置，再转换为高级编排'; return }
  try {
    const converted = await api.detachFlowToAdvanced(selected.value.id)
    await load()
    const target = templates.value.find(item => item.id === converted.id)
    if (target) await edit(target)
    router.replace(`/developer/flow-templates?template_id=${converted.id}&edit=1`)
  } catch (e) { error.value = e.message }
}
function runDebug(item = selected.value) {
  if (!item) return
  router.push(`/developer/dataflow-debug?template_id=${encodeURIComponent(item.id)}&revision_kind=${item.revision_status === 'draft' ? 'draft' : 'published'}`)
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
onMounted(async () => {
  await load()
  if (route.query.template_id && route.query.edit === '1') {
    const target = templates.value.find(item => item.id === route.query.template_id)
    if (target) await edit(target)
  }
})
</script>

<template>
  <section class="template-page">
    <header class="template-page-head">
      <div><div class="title-row"><h2>知识流程</h2></div><p>通过标准业务配置或高级编排定义知识生产规则；正式输出库由业务运行时绑定。</p></div>
      <div class="header-actions"><template v-if="editing"><button class="exit" @click="exitEditing">‹ 退出编辑</button><span class="save-state" :class="{ dirty }"><i></i>{{ statusLabel }}</span><button @click="settingsOpen=!settingsOpen">流程设置</button><button :disabled="!selected" @click="action('validate')">编译校验</button><button :disabled="!selected" @click="runDebug()">运行调试</button><button class="primary" :disabled="!selected" @click="action('publish')">发布</button></template></div>
    </header>
    <template v-if="!editing">
        <section class="template-strip">
          <button class="new-template" @click="wizardOpen=true">＋ 新建知识流程</button>
          <div class="template-groups">
            <section class="template-group">
              <header><b>内置流程</b><small>{{ templateGroups.builtin.length }} 项</small></header>
              <div class="template-list">
                <button v-for="item in templateGroups.builtin" :key="item.id" :class="{ active:selected?.id===item.id }" @click="edit(item)">
                  <span class="template-card-title"><b>{{ item.name }}</b><span class="builtin-tag">内置</span></span>
                  <small v-if="outputSummary(item)" class="output-summary">{{ outputSummary(item) }}</small>
                  <small class="template-meta">标准配置 · {{ item.code }} · r{{ item.revision || '-' }}<template v-if="item.is_default"> · 默认</template></small>
                  <span class="import-draft" role="button" tabindex="0" @click.stop.prevent="runDebug(item)" @keydown.enter.prevent="runDebug(item)">运行调试</span>
                </button>
              </div>
            </section>
            <section class="template-group">
              <header><b>自定义流程</b><small>{{ templateGroups.custom.length }} 项</small></header>
              <div v-if="templateGroups.custom.length" class="template-list">
                <button v-for="item in templateGroups.custom" :key="item.id" :class="{ active:selected?.id===item.id }" @click="edit(item)">
                  <span class="template-card-title"><b>{{ item.name }}</b><span v-if="item.needs_review_upgrade" class="upgrade-tag">需升级审核入口</span></span>
                  <small v-if="outputSummary(item)" class="output-summary">{{ outputSummary(item) }}</small>
                  <small class="template-meta">{{ item.authoring_mode === 'standard' ? '标准配置' : '高级编排' }} · {{ item.code }} · r{{ item.revision || '-' }}<template v-if="item.is_default"> · 默认</template></small>
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
          <div class="authoring-mode-actions"><span class="selection-state">{{ authoringMode === 'standard' ? '标准配置 · 业务阶段' : '高级编排 · Authoring DAG' }}</span><template v-if="authoringMode==='standard' && selected"><button @click="convertToAdvanced">转换为高级编排</button><FieldHelp label="高级编排转换说明" :text="advancedConversionHelp" /></template></div>
          <div><button class="primary" @click="save">保存草稿</button></div>
        </div>
        <StandardFlowEditor v-if="authoringMode === 'standard'" :template="selected" :managed-templates="managedTemplates" :catalog="catalog" :output-types="outputTypes" @update:definition="onStageDefinition" />
        <AdvancedFlowEditor v-else ref="advancedEditor" :catalog="catalog" :subflows="subflows" :output-types="outputTypes" :sample-result="sampleResult" @dirty="onEditorDirty" @error="onEditorError" />
    </template>
    <div v-if="wizardOpen" class="wizard-backdrop" @click.self="wizardOpen=false"><section class="wizard"><header><div><h3>选择知识生产目标</h3><p>普通路径只选择业务目标，不需要理解 Node、Edge 或 Port。</p></div><button @click="wizardOpen=false">关闭</button></header><div class="goal-grid"><button v-for="item in managedTemplates" :key="item.code" @click="startNew(item.code)"><b>{{ item.name }}</b><small>{{ (item.output_types || []).join(' + ') }}</small></button></div><div class="advanced-choice"><span>高级</span><button @click="startNew('')"><b>空白高级流程</b><small>从 Authoring DAG 开始</small></button></div></section></div>
    <p v-if="error" class="error page-error">{{ error }}</p><pre v-if="result" class="action-result">{{ JSON.stringify(result,null,2) }}</pre>
  </section>
</template>

<style scoped>
.template-page{min-width:1164px}.template-page-head{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:12px}.title-row{display:flex;align-items:center;gap:9px}.title-row h2{margin:0;font-size:21px}.dsl-badge{padding:5px 8px;border:1px solid #d8e4ff;border-radius:999px;color:#2f6fed;background:#eaf1ff;font-size:8px;font-weight:850}.template-page-head p{margin:5px 0 0;color:#778499;font-size:10px}.header-actions{display:flex;align-items:center;gap:7px}.save-state{display:inline-flex;align-items:center;gap:6px;margin-right:4px;color:#627087;font-size:8px;font-weight:800}.save-state i{width:7px;height:7px;border-radius:50%;background:#1d8c65}.save-state.dirty i{background:#b97917}.page-tabs{display:flex;gap:4px;margin-bottom:10px;border-bottom:1px solid #dfe5ed}.page-tabs button{border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent}.page-tabs button.active{border-bottom-color:#2f6fed;color:#2f6fed}.template-strip{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:start;gap:10px;margin-bottom:9px}.new-template{white-space:nowrap}.template-groups{display:grid;gap:9px;min-width:0}.template-group{display:grid;grid-template-columns:84px minmax(0,1fr);align-items:start;gap:8px}.template-group>header{display:grid;gap:2px;padding-top:8px;color:#34445a}.template-group>header small{color:#8a97a8}.template-list{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}.template-list button{display:grid;min-width:190px;max-width:250px;text-align:left}.template-list button.active{border-color:#b9cff7;color:#2f6fed;background:#eff5ff}.template-card-title{display:flex;align-items:center;justify-content:space-between;gap:8px}.template-list b,.template-list small{display:block}.template-list small{margin-top:3px;color:#8290a3;font-size:7px}.builtin-tag{padding:2px 6px;border:1px solid #c9dafb;border-radius:999px;color:#2f6fed;background:#edf4ff;font-size:7px;font-weight:800}.template-list .output-summary{color:#6b7a8f;font-size:7px}.upgrade-tag{padding:2px 6px;border:1px solid #efcf91;border-radius:999px;color:#986316;background:#fff7e7;font-size:7px;font-weight:800}.template-settings{display:grid;grid-template-columns:minmax(180px,1fr) minmax(200px,1.4fr);gap:10px;padding:14px;border:1px solid var(--border);border-radius:11px;background:#fff;margin-bottom:9px}.template-settings>label{display:grid;gap:4px;color:#536177;font-weight:700}.template-settings input,.template-settings select{border:1px solid #dfe5ed}.template-settings fieldset{border:1px solid #dfe5ed;border-radius:8px}.template-settings fieldset legend{color:#536177;font-weight:700}.template-settings fieldset label{display:inline-flex;align-items:center;gap:5px;margin-right:12px;color:#536177;font-weight:400}.settings-actions{display:flex;gap:6px;align-items:center}.settings-actions .danger{color:#c0392b;border-color:#f0c4bc}.flow-toolbar{display:flex;align-items:center;justify-content:space-between;min-height:54px;padding:9px 12px;margin-bottom:10px;border:1px solid var(--border);border-radius:12px;background:#fff}.flow-toolbar .mode-bar{display:flex;align-items:center}.selection-state{color:#66758a;font-size:12px;margin-right:8px}.action-result{margin-top:12px;padding:12px;background:#182231;color:#d9e2ee;border-radius:10px;font:11px monospace;max-height:300px;overflow:auto}
.wizard-backdrop{position:fixed;z-index:40;inset:0;display:grid;place-items:center;background:rgba(16,24,40,.42)}.wizard{width:min(760px,90vw);padding:22px;border-radius:15px;background:#fff;box-shadow:0 24px 70px rgba(15,23,42,.24)}.wizard>header{display:flex;justify-content:space-between;gap:20px}.wizard h3{margin:0}.wizard p{color:#6d7b8e}.goal-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:16px}.goal-grid button,.advanced-choice button{display:grid;gap:5px;padding:16px;text-align:left}.goal-grid small,.advanced-choice small{color:#748197}.advanced-choice{display:grid;grid-template-columns:80px 1fr;gap:10px;align-items:center;margin-top:16px;padding-top:16px;border-top:1px solid #e2e7ee}.advanced-choice>span{font-weight:800;color:#7a8799}
.authoring-mode-actions{display:flex;align-items:center}
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
