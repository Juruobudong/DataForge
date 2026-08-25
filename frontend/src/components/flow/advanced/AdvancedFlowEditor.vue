<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import DataForgeFlowCanvas from '../DataForgeFlowCanvas.vue'
import OperatorPalette from '../palette/OperatorPalette.vue'
import NodeInspector from '../inspector/NodeInspector.vue'
import GraphSchemaEditor from '../../graph/GraphSchemaEditor.vue'
import PromptPreview from '../../graph/PromptPreview.vue'
import { deserializeDefinition, makeCanvasNode, serializeDefinition, validateFlow } from '../flowModel'
import { useFlowHistory } from '../composables/useFlowHistory'

const props = defineProps({
  catalog: { type: Array, default: () => [] },
  subflows: { type: Array, default: () => [] },
  outputTypes: { type: Array, default: () => [] },
  sampleResult: { type: Object, default: null },
})
const emit = defineEmits(['dirty', 'error'])

const selectedNode = ref(null), selectedEdge = ref(null)
const issues = ref([]), focusedIssue = ref(null), connectionError = ref(null)
const nodes = ref([]), edges = ref([])
const canvas = ref(null), editor = ref(null)
const graphConfig = ref({ entity_types: [], relation_types: [], literal_policy: { enabled_datatypes: [] }, unknown_entity_policy: 'reject', unknown_relation_policy: 'reject', prompt: { mode: 'generated', body: null } })
const graphConfigOpen = ref(false)
const { canUndo, canRedo, remember, undo: historyUndo, redo: historyRedo, clear: clearHistory } = useFlowHistory(nodes, edges)

const hasGraphOutput = computed(() => props.outputTypes.some(value => value.startsWith('graph:')))
const selectedIssue = computed(() => issues.value.find(issue => issue.nodeId === selectedNode.value?.id || issue.edgeId === selectedEdge.value?.id) || connectionError.value)

function markDirty() { emit('dirty') }
function beforeChange() { remember(); markDirty(); focusedIssue.value = null }
function undo() { historyUndo(); markDirty(); selectedNode.value = null; selectedEdge.value = null }
function redo() { historyRedo(); markDirty(); selectedNode.value = null; selectedEdge.value = null }
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

function serialize() {
  const def = serializeDefinition(nodes.value, edges.value)
  if (hasGraphOutput.value) def.graph_config = { ...graphConfig.value }
  return def
}
function validate() {
  issues.value = validateFlow(nodes.value, edges.value, props.outputTypes)
  if (issues.value.length) focusIssue(issues.value[0])
  return issues.value.length === 0
}
function loadDefinition(value) {
  const graph = deserializeDefinition(value, props.catalog, props.subflows)
  nodes.value = graph.nodes; edges.value = graph.edges
  graphConfig.value = normalizeGraphConfig(value?.graph_config)
  graphConfigOpen.value = false
  selectedNode.value = null; selectedEdge.value = null; issues.value = []; connectionError.value = null
  clearHistory()
  nextTick(() => canvas.value?.fit())
}
function reset() {
  selectedNode.value = null; selectedEdge.value = null
  nodes.value = []; edges.value = []
  graphConfig.value = normalizeGraphConfig(null); graphConfigOpen.value = false
  issues.value = []; connectionError.value = null; clearHistory()
}
function dragStart(event, item, kind) {
  event.dataTransfer.setData('application/dataforge-operator', JSON.stringify({ kind, ref: item.code, params: {} }))
  event.dataTransfer.effectAllowed = 'move'
}
function addDefinition(raw, position) {
  beforeChange()
  const definition = { ...raw, id: uniqueId(raw.kind === 'subflow' ? raw.ref : raw.ref || 'node') }
  nodes.value.push(makeCanvasNode(definition, { x: position.x - 135, y: position.y - 70 }, props.catalog, props.subflows))
}
function addItem(item, kind) {
  const rect = editor.value?.getBoundingClientRect()
  const position = canvas.value?.screenToFlowCoordinate({ x: (rect?.left || 500) + (rect?.width || 900) / 2, y: (rect?.top || 200) + 300 }) || { x: 320, y: 200 }
  addDefinition({ kind, ref: item.code, params: {} }, position)
}
function addSink(outputKey) {
  if (nodes.value.some(node => node.data.definition.kind === 'knowledge_sink' && node.data.definition.output_key === outputKey)) { emit('error', `输出 ${outputKey} 已有 Knowledge Sink`); return }
  beforeChange()
  const family = outputFamily(outputKey), mode = outputKey.includes(':') ? outputKey.split(':')[1] : null
  const definition = { id: uniqueId(`sink-${outputKey.replace(':', '-')}`), kind: 'knowledge_sink', knowledge_type: family, graph_mode: mode, output_key: outputKey }
  nodes.value.push(makeCanvasNode(definition, { x: 760, y: 120 + nodes.value.length * 14 }, props.catalog, props.subflows))
}
function applyParameters(value) {
  if (!selectedNode.value) return
  beforeChange(); selectedNode.value.data.definition.params = value
  selectedNode.value.data.meta = makeCanvasNode(selectedNode.value.data.definition, selectedNode.value.position, props.catalog, props.subflows).data.meta
}
function selectNode(node) { selectedNode.value = node; selectedEdge.value = null; connectionError.value = null }
function selectEdge(edge) { selectedEdge.value = edge; selectedNode.value = null; connectionError.value = null }
function reportConnectionError(issue) { connectionError.value = issue; if (issue) emit('error', issue.message) }
function focusIssue(issue) { focusedIssue.value = issue; canvas.value?.focusElement(issue) }
function autoLayout() { canvas.value?.autoLayout(); markDirty() }
function shortcut(event) {
  const target = event.target
  if (target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"]')) return
  if (!(event.ctrlKey || event.metaKey)) return
  if (event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redo() : undo() }
}

defineExpose({ serialize, validate, loadDefinition, reset, nodes, edges })

onMounted(() => window.addEventListener('keydown', shortcut))
onBeforeUnmount(() => window.removeEventListener('keydown', shortcut))
</script>

<template>
  <div class="advanced-editor">
    <div class="flow-toolbar">
      <div><button :disabled="!canUndo" title="Ctrl+Z" @click="undo">↶ 撤销</button><button :disabled="!canRedo" title="Ctrl+Shift+Z" @click="redo">↷ 重做</button><span></span><button @click="autoLayout">自动布局</button><button @click="canvas?.fit()">适应画布</button></div>
      <div><span class="selection-state">{{ nodes.length }} 节点 · {{ edges.length }} 连线</span><button v-if="hasGraphOutput" :class="{ active: graphConfigOpen }" @click="graphConfigOpen = !graphConfigOpen">图谱抽取配置</button></div>
    </div>
    <div ref="editor" class="flow-workspace">
      <OperatorPalette :catalog="catalog" :subflows="subflows" :output-types="outputTypes" @drag-start="dragStart" @add-item="addItem" @add-sink="addSink" />
      <DataForgeFlowCanvas ref="canvas" v-model:nodes="nodes" v-model:edges="edges" :issue="focusedIssue" @before-change="beforeChange" @select-node="selectNode" @select-edge="selectEdge" @connection-error="reportConnectionError" @add-definition="addDefinition" />
      <NodeInspector :node="selectedNode" :issue="selectedIssue" :sample-result="sampleResult" @apply-parameters="applyParameters" />
    </div>
    <section v-if="graphConfigOpen && hasGraphOutput" class="graph-config-panel"><GraphSchemaEditor v-model="graphConfig" /><PromptPreview :graph-config="graphConfig" /></section>
    <section v-if="issues.length" class="validation-panel"><div><h3>画布校验</h3><span>{{ issues.length }} 个问题</span></div><button v-for="(issue,index) in issues" :key="`${issue.code}-${index}`" @click="focusIssue(issue)"><b>{{ issue.code }}</b><span>{{ issue.message }}</span><small>定位 →</small></button></section>
  </div>
</template>

<style scoped>
.advanced-editor { display: grid; gap: 10px; }
.flow-toolbar { display: flex; align-items: center; justify-content: space-between; min-height: 54px; padding: 9px 12px; border: 1px solid var(--border); border-radius: 12px; background: #fff; }
.flow-toolbar > div { display: flex; align-items: center; gap: 6px; }
.selection-state { color: #66758a; font-size: 12px; margin-right: 6px; }
.flow-workspace { display: grid; width: 100%; min-width: 1164px; grid-template-columns: 220px minmax(620px, 1fr) 300px; gap: 10px; align-items: stretch; }
.graph-config-panel { display: grid; grid-template-columns: minmax(0,1.4fr) minmax(320px,1fr); gap: 14px; padding: 14px; border: 1px solid var(--border); border-radius: 11px; background: #f7f9fc; }
.flow-toolbar button.active { border-color: #2f6fed; color: #2f6fed; background: #eff5ff; }
.validation-panel { display: grid; gap: 6px; padding: 12px; border: 1px solid #efcf91; border-radius: 11px; background: #fff7e7; }
.validation-panel > div { display: flex; align-items: center; justify-content: space-between; }
.validation-panel h3 { margin: 0; color: #986316; }
.validation-panel > div span { color: #986316; font-size: 12px; }
.validation-panel button { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center; text-align: left; }
.validation-panel button b { color: #986316; }
.validation-panel button span { color: #6b5a3a; }
.validation-panel button small { color: #a8842e; }
</style>
