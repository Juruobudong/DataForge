<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api/platform'
import DataForgeFlowCanvas from '../../components/flow/DataForgeFlowCanvas.vue'
import OperatorPalette from '../../components/flow/palette/OperatorPalette.vue'
import NodeInspector from '../../components/flow/inspector/NodeInspector.vue'
import OperatorInspector from '../../components/flow/inspector/OperatorInspector.vue'
import { useRouter } from 'vue-router'
import { deserializeDefinition, makeCanvasNode, serializeDefinition, validateFlow } from '../../components/flow/flowModel'
import { useFlowHistory } from '../../components/flow/composables/useFlowHistory'
import GraphSchemaEditor from '../../components/graph/GraphSchemaEditor.vue'
import PromptPreview from '../../components/graph/PromptPreview.vue'

const tabs = [{ key: 'templates', name: '模板' }, { key: 'catalog', name: '算子目录' }, { key: 'subflows', name: '可复用子图' }]
const activeTab = ref('templates'), templates = ref([]), catalog = ref([]), subflows = ref([]), types = ref([])
const facets = ref({ categories: [], knowledge_types: [], statuses: [] }), catalogQuery = ref(''), catalogCategory = ref(''), catalogKnowledge = ref(''), catalogExposure = ref(''), catalogStatus = ref(''), selectedOperator = ref(null)
const expandedSubflow = ref(null), miniNodes = ref([]), miniEdges = ref([])
const selected = ref(null), selectedNode = ref(null), selectedEdge = ref(null), sampleResult = ref(null)
const result = ref(null), error = ref(''), connectionError = ref(null), issues = ref([]), focusedIssue = ref(null)
const code = ref(''), name = ref(''), outputTypes = ref(['text']), nodes = ref([]), edges = ref([])
const sampleId = ref('guideline-md'), settingsOpen = ref(false), dirty = ref(false), loading = ref(false)
const canvas = ref(null), editor = ref(null)
const graphConfig = ref({ entity_types: [], relation_types: [], literal_policy: { enabled_datatypes: [] }, unknown_entity_policy: 'reject', unknown_relation_policy: 'reject', prompt: { mode: 'generated', body: null } })
const graphConfigOpen = ref(false)
const router = useRouter()
const { canUndo, canRedo, remember, undo: historyUndo, redo: historyRedo, clear: clearHistory } = useFlowHistory(nodes, edges)

const hasGraphOutput = computed(() => outputTypes.value.some(value => value.startsWith('graph:')))

const typeOptions = computed(() => {
  const normal = types.value.filter(item => item.status === 'active' && item.current_revision && item.code !== 'graph').map(item => ({ code: item.code, name: item.name }))
  return [...normal, { code: 'graph:triple', name: '三元组图谱' }, { code: 'graph:semantic', name: '语义图谱' }]
})
const statusLabel = computed(() => !selected.value ? '新建草稿' : dirty.value ? '草稿 · 未保存' : `r${selected.value.revision || '-'} · 已保存`)
const selectedIssue = computed(() => issues.value.find(issue => issue.nodeId === selectedNode.value?.id || issue.edgeId === selectedEdge.value?.id) || connectionError.value)
const visibleCatalog = computed(() => catalog.value.filter(item => (!catalogQuery.value || `${item.display_name_zh} ${item.code} ${item.summary}`.toLowerCase().includes(catalogQuery.value.toLowerCase())) && (!catalogCategory.value || item.category === catalogCategory.value) && (!catalogKnowledge.value || item.knowledge_types?.includes(catalogKnowledge.value)) && (!catalogExposure.value || item.exposure === catalogExposure.value) && (!catalogStatus.value || item.status === catalogStatus.value)))

function beforeChange() { remember(); dirty.value = true; focusedIssue.value = null; sampleResult.value = null }
function undo() { historyUndo(); dirty.value = true; selectedNode.value = null; selectedEdge.value = null }
function redo() { historyRedo(); dirty.value = true; selectedNode.value = null; selectedEdge.value = null }
function definition() {
  const def = serializeDefinition(nodes.value, edges.value)
  if (hasGraphOutput.value) def.graph_config = { ...graphConfig.value }
  return def
}
function outputFamily(value) { return value.startsWith('graph:') ? 'graph' : value }
function uniqueId(prefix) { return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 5)}` }
function normalizeGraphConfig(raw) {
  const base = { entity_types: [], relation_types: [], literal_policy: { enabled_datatypes: [] }, unknown_entity_policy: 'reject', unknown_relation_policy: 'reject', prompt: { mode: 'generated', body: null } }
  if (!raw || typeof raw !== 'object') return base
  return {
    ...base, ...raw,
    entity_types: (raw.entity_types || []).map(e => ({ code: e.code || '', label: e.label || '', description: e.description || '' })),
    relation_types: (raw.relation_types || []).map(r => ({ code: r.code || '', label: r.label || '', description: r.description || '', source_types: r.source_types || [], target_types: r.target_types || [] })),
    literal_policy: { enabled_datatypes: raw.literal_policy?.enabled_datatypes || [] },
    prompt: { ...base.prompt, ...(raw.prompt || {}) },
  }
}

function loadDefinition(value) {
  const graph = deserializeDefinition(value, catalog.value, subflows.value)
  nodes.value = graph.nodes; edges.value = graph.edges
  selectedNode.value = null; selectedEdge.value = null; issues.value = []; connectionError.value = null
  clearHistory(); dirty.value = false
  nextTick(() => canvas.value?.fit())
}
function edit(item) {
  if (dirty.value && selected.value?.id !== item.id && !window.confirm('当前画布有未保存修改，确定放弃并切换模板吗？')) return
  selected.value = item; code.value = item.code; name.value = item.name
  outputTypes.value = [...item.output_types].map(value => value === 'graph' ? 'graph:triple' : value)
  graphConfig.value = normalizeGraphConfig(item.definition?.graph_config)
  graphConfigOpen.value = false
  result.value = null; sampleResult.value = null; error.value = ''; loadDefinition(item.definition)
}
function reset() {
  if (dirty.value && !window.confirm('当前画布有未保存修改，确定新建模板吗？')) return
  selected.value = null; selectedNode.value = null; selectedEdge.value = null
  code.value = ''; name.value = ''; outputTypes.value = ['text']; nodes.value = []; edges.value = []
  graphConfig.value = normalizeGraphConfig(null); graphConfigOpen.value = false
  result.value = null; sampleResult.value = null; error.value = ''; issues.value = []; clearHistory(); dirty.value = false
}
function dragStart(event, item, kind) {
  event.dataTransfer.setData('application/dataforge-operator', JSON.stringify({ kind, ref: item.code, params: {} }))
  event.dataTransfer.effectAllowed = 'move'
}
function addDefinition(raw, position) {
  beforeChange()
  const definition = { ...raw, id: uniqueId(raw.kind === 'subflow' ? raw.ref : raw.ref || 'node') }
  nodes.value.push(makeCanvasNode(definition, { x: position.x - 135, y: position.y - 70 }, catalog.value, subflows.value))
}
function addItem(item, kind) {
  const rect = editor.value?.getBoundingClientRect()
  const position = canvas.value?.screenToFlowCoordinate({ x: (rect?.left || 500) + (rect?.width || 900) / 2, y: (rect?.top || 200) + 300 }) || { x: 320, y: 200 }
  addDefinition({ kind, ref: item.code, params: {} }, position)
}
function addSink(outputKey) {
  if (nodes.value.some(node => node.data.definition.kind === 'knowledge_sink' && node.data.definition.output_key === outputKey)) { error.value = `输出 ${outputKey} 已有 Knowledge Sink`; return }
  beforeChange()
  const family = outputFamily(outputKey), mode = outputKey.includes(':') ? outputKey.split(':')[1] : null
  const definition = { id: uniqueId(`sink-${outputKey.replace(':', '-')}`), kind: 'knowledge_sink', knowledge_type: family, graph_mode: mode, output_key: outputKey }
  nodes.value.push(makeCanvasNode(definition, { x: 760, y: 120 + nodes.value.length * 14 }, catalog.value, subflows.value))
}
function applyParameters(value) {
  if (!selectedNode.value) return
  beforeChange(); selectedNode.value.data.definition.params = value
  selectedNode.value.data.meta = makeCanvasNode(selectedNode.value.data.definition, selectedNode.value.position, catalog.value, subflows.value).data.meta
}
function selectNode(node) { selectedNode.value = node; selectedEdge.value = null; connectionError.value = null }
function selectEdge(edge) { selectedEdge.value = edge; selectedNode.value = null; connectionError.value = null }
function reportConnectionError(issue) { connectionError.value = issue; if (issue) error.value = issue.message }
function runLocalValidation() {
  issues.value = validateFlow(nodes.value, edges.value, outputTypes.value)
  if (issues.value.length) focusIssue(issues.value[0])
  return issues.value.length === 0
}
function focusIssue(issue) { focusedIssue.value = issue; canvas.value?.focusElement(issue) }
function autoLayout() { canvas.value?.autoLayout(); dirty.value = true }

async function load() {
  loading.value = true
  try { [templates.value, catalog.value, subflows.value, types.value, facets.value] = await Promise.all([api.flowTemplates(), api.operatorCatalog({ include_internal: true }), api.flowSubgraphs(), api.knowledgeTypes(), api.operatorCatalogFacets()]) }
  catch (e) { error.value = e.message }
  finally { loading.value = false }
}
async function expandSubflow(item) {
  if (expandedSubflow.value?.id === item.id) { expandedSubflow.value = null; return }
  try { const detail = await api.flowSubgraphRevision(item.id, item.revision); expandedSubflow.value = detail; const graph = deserializeDefinition(detail.definition, catalog.value, subflows.value); miniNodes.value = graph.nodes; miniEdges.value = graph.edges } catch (e) { error.value = e.message }
}
function openSubflow(item) { router.push(`/developer/flow-templates/subgraphs/${item.id}/revisions/${item.revision}`) }
async function save() {
  if (!code.value.trim() || !name.value.trim()) { error.value = '模板编码和名称不能为空'; return }
  try {
    error.value = ''; const body = { name: name.value, output_types: outputTypes.value, definition: definition() }
    const response = selected.value ? await api.updateFlowTemplate(selected.value.id, body) : await api.createFlowTemplate({ ...body, code: code.value })
    result.value = response; await load()
    const refreshed = templates.value.find(item => item.id === (selected.value?.id || response.id)) || templates.value.find(item => item.code === code.value)
    if (refreshed) { selected.value = refreshed; edit(refreshed) } else dirty.value = false
  } catch (e) { error.value = e.message }
}
async function action(kind) {
  if (!selected.value) { error.value = '请先保存模板草稿'; return }
  if (kind === 'validate') {
    if (!runLocalValidation()) return
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
function shortcut(event) {
  const target = event.target
  if (target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"]')) return
  if (!(event.ctrlKey || event.metaKey)) return
  if (event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redo() : undo() }
}
onMounted(() => { load(); window.addEventListener('keydown', shortcut) })
onBeforeUnmount(() => window.removeEventListener('keydown', shortcut))
</script>

<template>
  <section class="template-page">
    <header class="template-page-head">
      <div><div class="title-row"><h2>知识流程模板</h2><span class="dsl-badge">Flow DSL v3</span></div><p>白名单算子、强类型端口与 Knowledge Sink 构成受控知识生产 DAG。</p></div>
      <div class="header-actions"><span class="save-state" :class="{ dirty }"><i></i>{{ statusLabel }}</span><button @click="settingsOpen=!settingsOpen">模板设置</button><button :disabled="!selected" @click="action('validate')">编译校验</button><button :disabled="!selected" @click="action('sample')">样例运行</button><button class="primary" :disabled="!selected" @click="action('publish')">发布快照</button></div>
    </header>
    <div class="page-tabs"><button v-for="tab in tabs" :key="tab.key" :class="{ active: activeTab===tab.key }" @click="activeTab=tab.key">{{ tab.name }}</button></div>
    <div v-if="activeTab==='catalog'" class="secondary-filters"><label>知识类型<select v-model="catalogKnowledge"><option value="">全部</option><option v-for="item in facets.knowledge_types" :key="item" :value="item">{{ item }}</option></select></label><label>生命周期<select v-model="catalogStatus"><option value="">全部</option><option v-for="item in facets.statuses" :key="item" :value="item">{{ item }}</option></select></label></div>

    <template v-if="activeTab==='templates'">
      <section class="template-strip">
        <button class="new-template" @click="reset">＋ 新建模板</button>
        <div class="template-list"><button v-for="item in templates" :key="item.id" :class="{ active:selected?.id===item.id }" @click="edit(item)"><b>{{ item.name }}</b><small>{{ item.code }} · r{{ item.revision || '-' }}<template v-if="item.is_default"> · 默认</template></small></button></div>
      </section>
      <form v-if="settingsOpen" class="template-settings" @submit.prevent="save"><label>模板编码<input v-model="code" :disabled="!!selected" required placeholder="template-code" @input="dirty=true"></label><label>模板名称<input v-model="name" required placeholder="模板名称" @input="dirty=true"></label><fieldset><legend>正式输出</legend><label v-for="item in typeOptions" :key="item.code"><input v-model="outputTypes" type="checkbox" :value="item.code" @change="dirty=true">{{ item.name }}</label></fieldset><label>样例<select v-model="sampleId"><option value="guideline-md">指南 Markdown</option><option value="faq-csv">FAQ CSV</option><option value="case-txt">病例摘要</option></select></label><div class="settings-actions"><button v-if="selected" type="button" @click="action('default')">设为默认</button><button v-if="selected" type="button" class="danger" @click="action('archive')">归档</button><button class="primary">保存草稿</button></div></form>
      <div class="flow-toolbar"><div><button :disabled="!canUndo" title="Ctrl+Z" @click="undo">↶ 撤销</button><button :disabled="!canRedo" title="Ctrl+Shift+Z" @click="redo">↷ 重做</button><span></span><button @click="autoLayout">自动布局</button><button @click="canvas?.fit()">适应画布</button></div><div><span class="selection-state">{{ nodes.length }} 节点 · {{ edges.length }} 连线</span><button v-if="hasGraphOutput" :class="{ active: graphConfigOpen }" @click="graphConfigOpen = !graphConfigOpen">图谱抽取配置</button><button class="primary" @click="save">保存草稿</button></div></div>
      <div ref="editor" class="flow-workspace">
        <OperatorPalette :catalog="catalog" :subflows="subflows" :output-types="outputTypes" @drag-start="dragStart" @add-item="addItem" @add-sink="addSink" />
        <DataForgeFlowCanvas ref="canvas" v-model:nodes="nodes" v-model:edges="edges" :issue="focusedIssue" @before-change="beforeChange" @select-node="selectNode" @select-edge="selectEdge" @connection-error="reportConnectionError" @add-definition="addDefinition" />
        <NodeInspector :node="selectedNode" :issue="selectedIssue" :sample-result="sampleResult" @apply-parameters="applyParameters" />
      </div>
      <section v-if="graphConfigOpen && hasGraphOutput" class="graph-config-panel"><GraphSchemaEditor v-model="graphConfig" /><PromptPreview :graph-config="graphConfig" /></section>
      <section v-if="issues.length" class="validation-panel"><div><h3>画布校验</h3><span>{{ issues.length }} 个问题</span></div><button v-for="(issue,index) in issues" :key="`${issue.code}-${index}`" @click="focusIssue(issue)"><b>{{ issue.code }}</b><span>{{ issue.message }}</span><small>定位 →</small></button></section>
    </template>

    <section v-else-if="activeTab==='catalog'" class="catalog-layout"><div class="panel catalog-list"><div class="panel-head"><div><h3>Operator Catalog</h3><p>业务说明与技术契约分层展示；数量和分类来自 Registry。</p></div><span class="badge blue">{{ visibleCatalog.length }} / {{ catalog.length }}</span></div><div class="catalog-filters"><input v-model="catalogQuery" placeholder="搜索名称、编码或说明"><select v-model="catalogCategory"><option value="">全部分类</option><option v-for="item in facets.categories" :key="item.code" :value="item.name">{{ item.name }} ({{ item.count }})</option></select><select v-model="catalogExposure"><option value="">全部暴露级别</option><option value="canvas">可直接使用</option><option value="controlled">受控使用</option><option value="internal">系统内部</option><option value="disabled">已禁用</option></select></div><button v-for="item in visibleCatalog" :key="item.id" class="operator-row" :class="{active:selectedOperator?.id===item.id}" @click="selectedOperator=item"><div><b>{{ item.display_name_zh || item.name }}</b><small>{{ item.code }} · v{{ item.version }} · {{ item.category }}</small><p>{{ item.summary }}</p></div><span class="badge" :class="item.exposure==='canvas'?'green':'amber'">{{ {canvas:'可直接使用',controlled:'受控使用',internal:'系统内部',disabled:'已禁用'}[item.exposure] }}</span></button></div><OperatorInspector :operator="selectedOperator || visibleCatalog[0]" /></section>
    <section v-else class="panel subflow-catalog"><div class="panel-head"><div><h3>可复用子图</h3><p>卡片可原地展开 Mini DAG；完整画布按不可变 revision 查看。</p></div><span class="badge blue">{{ subflows.length }} 项</span></div><div class="subflow-grid"><article v-for="item in subflows" :key="item.id" :class="{expanded:expandedSubflow?.id===item.id}"><button class="subflow-title" @click="expandSubflow(item)"><span>◈</span><div><b>{{ item.name }}</b><small>{{ item.code }} · r{{ item.revision }}</small><p>{{ item.description || '可复用受控子图' }} · {{ item.node_count }} 节点 / {{ item.edge_count }} 连线</p></div></button><div v-if="expandedSubflow?.id===item.id" class="mini-wrap"><DataForgeFlowCanvas v-model:nodes="miniNodes" v-model:edges="miniEdges" mode="mini" height="260" :canvas-id="`mini-${item.id}`" /><button class="primary" @click="openSubflow(item)">打开完整画布</button></div></article></div></section>
    <p v-if="error" class="error page-error">{{ error }}</p><pre v-if="result && activeTab==='templates'" class="action-result">{{ JSON.stringify(result,null,2) }}</pre>
  </section>
</template>

<style scoped>
.template-page{min-width:1164px}.template-page-head{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:12px}.title-row{display:flex;align-items:center;gap:9px}.title-row h2{margin:0;font-size:21px}.dsl-badge{padding:5px 8px;border:1px solid #d8e4ff;border-radius:999px;color:#2f6fed;background:#eaf1ff;font-size:8px;font-weight:850}.template-page-head p{margin:5px 0 0;color:#778499;font-size:10px}.header-actions{display:flex;align-items:center;gap:7px}.save-state{display:inline-flex;align-items:center;gap:6px;margin-right:4px;color:#627087;font-size:8px;font-weight:800}.save-state i{width:7px;height:7px;border-radius:50%;background:#1d8c65}.save-state.dirty i{background:#b97917}.page-tabs{display:flex;gap:4px;margin-bottom:10px;border-bottom:1px solid #dfe5ed}.page-tabs button{border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent}.page-tabs button.active{border-bottom-color:#2f6fed;color:#2f6fed}.template-strip{display:flex;align-items:stretch;gap:8px;margin-bottom:9px}.new-template{flex:0 0 auto}.template-list{display:flex;gap:6px;overflow-x:auto}.template-list button{display:grid;min-width:145px;text-align:left}.template-list button.active{border-color:#b9cff7;color:#2f6fed;background:#eff5ff}.template-list b,.template-list small{display:block}.template-list small{margin-top:2px;color:#8290a3;font-size:7px}.template-settings{display:grid;grid-template-columns:180px minmax(220px,1fr) minmax(340px,1.4fr) 150px auto;align-items:end;margin:0 0 9px;padding:12px;border:1px solid var(--border);border-radius:11px;background:#fff;box-shadow:var(--shadow)}.template-settings>label{display:grid;gap:5px;color:#617087;font-size:8px;font-weight:800}.template-settings fieldset{display:flex;min-height:55px;align-items:center;gap:10px;border:1px solid #e3e8ef;border-radius:8px}.template-settings legend{color:#617087;font-size:8px;font-weight:800}.template-settings fieldset label{font-size:8px}.settings-actions{display:flex;gap:6px}.flow-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;padding:7px 9px;border:1px solid var(--border);border-radius:10px;background:#fff}.flow-toolbar>div{display:flex;align-items:center;gap:6px}.flow-toolbar>div>span:not(.selection-state){width:1px;height:23px;background:#e3e8ef}.selection-state{margin-right:4px;color:#768399;font-size:8px;font-weight:750}.flow-workspace{display:grid;min-width:1164px;grid-template-columns:220px minmax(620px,1fr) 300px;gap:12px;align-items:start;overflow-x:auto}.validation-panel{margin-top:10px;padding:12px;border:1px solid #efdbb0;border-radius:11px;background:#fffbf0}.validation-panel>div{display:flex;align-items:center;justify-content:space-between}.validation-panel h3{margin:0;font-size:11px}.validation-panel>div span{color:#b97917;font-size:8px}.validation-panel button{display:grid;width:100%;grid-template-columns:150px 1fr 60px;margin-top:6px;text-align:left}.validation-panel button b{color:#b97917}.validation-panel button small{text-align:right}.page-error{position:sticky;bottom:10px;z-index:30;box-shadow:0 8px 28px rgba(201,74,74,.12)}.action-result{max-height:240px}.catalog-layout{display:grid;grid-template-columns:minmax(600px,1fr) 380px;gap:12px;min-height:650px}.catalog-list{overflow:auto}.catalog-filters{display:grid;grid-template-columns:1fr 180px 180px;gap:8px;margin:10px 0}.operator-row{display:flex;width:100%;justify-content:space-between;gap:16px;margin-top:6px;padding:12px;text-align:left}.operator-row.active{border-color:#2f6fed;background:#f1f6ff}.operator-row small{display:block;margin-top:3px;color:#7b8798}.operator-row p{margin:6px 0 0;color:#5d6a7c}.subflow-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:12px}.subflow-grid article{padding:13px;border:1px solid #d8e4f6;border-radius:10px;background:#f8fbff}.subflow-grid article.expanded{grid-column:span 2}.subflow-title{display:grid;width:100%;grid-template-columns:36px 1fr;gap:10px;border:0;background:transparent;text-align:left}.subflow-title>span{display:grid;width:35px;height:35px;place-items:center;border-radius:9px;color:#2f6fed;background:#e1ebff}.subflow-grid b,.subflow-grid small{display:block}.subflow-grid small{margin-top:3px;color:#7b889b;font-size:8px}.subflow-grid p{margin:7px 0 0;font-size:8px}.mini-wrap{margin-top:10px}.mini-wrap>button{margin-top:8px}
.secondary-filters{display:flex;justify-content:flex-end;gap:8px;margin:-2px 0 8px}.secondary-filters label{display:flex;align-items:center;gap:5px;color:#66758a;font-size:9px}
.graph-config-panel{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(320px,1fr);gap:14px;margin-top:12px;padding:14px;border:1px solid var(--border);border-radius:11px;background:#f7f9fc}
.flow-toolbar button.active{border-color:#2f6fed;color:#2f6fed;background:#eff5ff}
</style>
<style scoped>
.template-page-head p,.template-list small,.template-settings>label,.template-settings legend,.template-settings fieldset label,.selection-state,.validation-panel>div span,.subflow-grid small,.subflow-grid p,.secondary-filters label { font-size: var(--font-technical); }
.dsl-badge,.save-state { font-size: var(--font-technical); }
.template-page-head { gap: 28px; }
.template-settings { gap: 12px; padding: 16px; }
.flow-toolbar { min-height: 54px; padding: 9px 12px; }
.validation-panel h3 { font-size: var(--font-card); }
</style>
