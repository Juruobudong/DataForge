<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import DataForgeFlowCanvas from '../DataForgeFlowCanvas.vue'
import OperatorPalette from '../palette/OperatorPalette.vue'
import NodeInspector from '../inspector/NodeInspector.vue'
import EdgeInspector from '../inspector/EdgeInspector.vue'
import GraphSchemaEditor from '../../graph/GraphSchemaEditor.vue'
import PromptPreview from '../../graph/PromptPreview.vue'
import SubflowExtractionDialog from '../SubflowExtractionDialog.vue'
import { deserializeDefinition, makeCanvasNode, serializeDefinition, validateFlow, validateSubflow, subflowNodeDefinition, resolveSubflow } from '../flowModel'
import { useFlowHistory } from '../composables/useFlowHistory'
import { removeEntityReferences } from '../../graph/entityTypeModel'

const props = defineProps({
  catalog: { type: Array, default: () => [] },
  subflows: { type: Array, default: () => [] },
  outputTypes: { type: Array, default: () => [] },
  sampleResult: { type: Object, default: null },
  fragment: { type: Boolean, default: false },
  purpose: { type: String, default: 'knowledge' },
})
const emit = defineEmits(['dirty', 'error', 'open-subflow', 'subflow-created'])
const extraction = ref(null)

const selectedNode = ref(null), selectedEdge = ref(null)
const issues = ref([]), focusedIssue = ref(null), connectionError = ref(null)
const nodes = ref([]), edges = ref([])
const canvas = ref(null), editor = ref(null)
const graphConfig = ref({ entity_types: [], relation_types: [], literal_policy: { enabled_datatypes: [] }, unknown_entity_policy: 'reject', unknown_relation_policy: 'reject', prompt: { mode: 'generated', body: null } })
const graphConfigOpen = ref(false)
const { canUndo, canRedo, remember, undo: historyUndo, redo: historyRedo, clear: clearHistory } = useFlowHistory(nodes, edges, 40, graphConfig)

const hasGraphOutput = computed(() => props.outputTypes.some(value => value === 'graph' || value.startsWith('graph:')))
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
    entity_types: (raw.entity_types || []).map(e => ({ ...e, code: e.code || '', label: e.label || '', description: e.description || '', source: e.source || 'custom' })),
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
  issues.value = props.fragment ? validateSubflow(nodes.value, edges.value) : validateFlow(nodes.value, edges.value, props.outputTypes)
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
  event.dataTransfer.setData('application/dataforge-operator', JSON.stringify(subflowNodeDefinition(item, kind)))
  event.dataTransfer.effectAllowed = 'move'
}
function addDefinition(raw, position) {
  if (raw.kind === 'subflow') {
    const item = resolveSubflow(raw, props.subflows)
    if (!item || item.revision_status !== 'published' || item.status !== 'active' || (props.purpose === 'knowledge' && item.usage === 'source_preparation')) { emit('error', '该子流程尚未发布或仅适用于文档预处理'); return }
    raw = { ...raw, subflow_revision_id: item.revision_id || item.latest_revision_id }
  }
  beforeChange()
  const definition = { ...raw, id: uniqueId(raw.kind === 'subflow' ? raw.ref : raw.ref || 'node') }
  nodes.value.push(makeCanvasNode(definition, { x: position.x - 135, y: position.y - 70 }, props.catalog, props.subflows))
}
function addItem(item, kind) {
  const rect = editor.value?.getBoundingClientRect()
  const position = canvas.value?.screenToFlowCoordinate({ x: (rect?.left || 500) + (rect?.width || 900) / 2, y: (rect?.top || 200) + 300 }) || { x: 320, y: 200 }
  addDefinition(subflowNodeDefinition(item, kind), position)
}
function extractSelection() {
  const ids = nodes.value.filter(node => node.selected).map(node => node.id)
  if (!ids.length) { emit('error', '请先选择需要另存的节点'); return }
  extraction.value = { definition: serialize(), ids }
}
function openSubflow(node) {
  const item = resolveSubflow(node.data.definition, props.subflows)
  if (!item) { emit('error', '子流程修订不存在'); return }
  if (!node.data.definition.subflow_revision_id) { emit('error', '版本未锁定，请先保存流程或显式选择版本'); return }
  emit('open-subflow', item)
}
function changeSubflowRevision(item) {
  if (!selectedNode.value || !item) return
  beforeChange()
  selectedNode.value.data.definition.subflow_revision_id = item.revision_id || item.latest_revision_id
  selectedNode.value.data.meta = makeCanvasNode(selectedNode.value.data.definition, selectedNode.value.position, props.catalog, props.subflows).data.meta
  issues.value = props.fragment ? validateSubflow(nodes.value, edges.value) : validateFlow(nodes.value, edges.value, props.outputTypes)
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
  if (['quality-evaluator', 'quality-filter'].includes(selectedNode.value.data.meta.code) && value.quality_profile_revision_id) {
    const outgoing = Object.fromEntries(nodes.value.map(node => [node.id, []]))
    for (const edge of edges.value) (outgoing[edge.source] ||= []).push(edge.target)
    const sinksOf = start => { const result = new Set(), seen = new Set(), queue = [start]; while (queue.length) { const id = queue.shift(); if (seen.has(id)) continue; seen.add(id); const node = nodes.value.find(item => item.id === id); if (node?.data.meta.kind === 'knowledge_sink') result.add(id); for (const target of outgoing[id] || []) queue.push(target) } return result }
    const selectedSinks = sinksOf(selectedNode.value.id)
    for (const node of nodes.value) {
      if (!['quality-evaluator', 'quality-filter'].includes(node.data.meta.code) || node.id === selectedNode.value.id) continue
      if ([...sinksOf(node.id)].some(id => selectedSinks.has(id))) node.data.definition.params = { ...(node.data.definition.params || {}), quality_profile_revision_id: value.quality_profile_revision_id }
    }
  }
}
function applyNormalizedDefinition(value) {
  if (value?.graph_config) graphConfig.value = normalizeGraphConfig(value.graph_config)
  const normalized = Object.fromEntries((value?.nodes || []).map(node => [node.id, node]))
  nodes.value = nodes.value.map(node => {
    const definition = normalized[node.id]
    if (!definition) return node
    return { ...node, data: { definition: { ...node.data.definition, ...definition }, meta: makeCanvasNode(definition, node.position, props.catalog, props.subflows).data.meta } }
  })
  if (selectedNode.value) selectedNode.value = nodes.value.find(node => node.id === selectedNode.value.id) || null
}
function applyGraphConfig(value) {
  beforeChange()
  const codes = new Set((value.entity_types || []).map(item => item.code))
  const removed = graphConfig.value.entity_types.map(item => item.code).filter(code => !codes.has(code))
  const cleaned = removeEntityReferences(value, nodes.value, removed)
  graphConfig.value = cleaned.graphConfig
  nodes.value = cleaned.nodes
  if (selectedNode.value) selectedNode.value = nodes.value.find(node => node.id === selectedNode.value.id) || null
}
function selectNode(node) { selectedNode.value = node; selectedEdge.value = null; connectionError.value = null }
function selectEdge(edge) { selectedEdge.value = edge; selectedNode.value = null; connectionError.value = null }
function deleteEdge(edgeId) { canvas.value?.deleteEdge(edgeId) }
function reportConnectionError(issue) { connectionError.value = issue; if (issue) emit('error', issue.message) }
function focusIssue(issue) { focusedIssue.value = issue; canvas.value?.focusElement(issue) }
function focusBackendProblem(problem) {
  if (!problem?.code) return false
  const details = problem.details || {}
  const edge = edges.value.find(item => item.source === details.source_node_id && item.target === details.target_node_id &&
    (item.sourceHandle || 'output') === (details.source_port || 'output') && (item.targetHandle || 'input') === (details.target_port || 'input'))
  const issue = { code: problem.code, message: problem.message || 'Flow Edge 校验失败', edgeId: edge?.id,
    nodeId: edge ? undefined : details.target_node_id || details.source_node_id }
  issues.value = [...issues.value.filter(item => item.code !== issue.code || item.edgeId !== issue.edgeId), issue]
  if (edge) selectEdge(edge)
  focusIssue(issue)
  return true
}
function autoLayout() { canvas.value?.autoLayout(); markDirty() }
function shortcut(event) {
  const target = event.target
  if (target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"]')) return
  if (!(event.ctrlKey || event.metaKey)) return
  if (event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redo() : undo() }
}

defineExpose({ serialize, validate, loadDefinition, applyNormalizedDefinition, focusBackendProblem, reset, nodes, edges })

onMounted(() => window.addEventListener('keydown', shortcut))
onBeforeUnmount(() => window.removeEventListener('keydown', shortcut))
</script>

<template>
  <div class="advanced-editor">
    <div class="flow-toolbar">
      <div><button :disabled="!canUndo" title="Ctrl+Z" @click="undo">↶ 撤销</button><button :disabled="!canRedo" title="Ctrl+Shift+Z" @click="redo">↷ 重做</button><span></span><button @click="autoLayout">自动布局</button><button @click="canvas?.fit()">适应画布</button></div>
      <div><span class="selection-state">{{ nodes.length }} 节点 · {{ edges.length }} 连线</span><button v-if="!fragment" :disabled="!nodes.some(node => node.selected)" @click="extractSelection">另存为可复用子流程</button><button v-if="hasGraphOutput" :class="{ active: graphConfigOpen }" @click="graphConfigOpen = !graphConfigOpen">图谱抽取配置</button></div>
    </div>
    <div ref="editor" class="flow-workspace">
      <OperatorPalette :catalog="catalog" :subflows="subflows" :output-types="fragment ? [] : outputTypes" :purpose="purpose" @drag-start="dragStart" @add-item="addItem" @add-sink="addSink" />
      <DataForgeFlowCanvas ref="canvas" v-model:nodes="nodes" v-model:edges="edges" :issue="focusedIssue" :flow-context="{ schemaVersion: 3, outputTypes }" :show-technical-code="!fragment" @before-change="beforeChange" @select-node="selectNode" @select-edge="selectEdge" @connection-error="reportConnectionError" @add-definition="addDefinition" @open-subflow="openSubflow" />
      <EdgeInspector v-if="selectedEdge" :edge="selectedEdge" :nodes="nodes" :issue="selectedIssue" @delete="deleteEdge" />
      <NodeInspector v-else :node="selectedNode" :subflows="subflows" :entity-types="graphConfig.entity_types" :issue="selectedIssue" :sample-result="sampleResult" @apply-parameters="applyParameters" @open-subflow="openSubflow(selectedNode)" @change-subflow-revision="changeSubflowRevision" />
    </div>
    <section v-if="graphConfigOpen && hasGraphOutput" class="graph-config-panel"><GraphSchemaEditor :model-value="graphConfig" @update:model-value="applyGraphConfig" /><PromptPreview :graph-config="graphConfig" /></section>
    <section v-if="issues.length" class="validation-panel"><div><h3>画布校验</h3><span>{{ issues.length }} 个问题</span></div><button v-for="(issue,index) in issues" :key="`${issue.code}-${index}`" @click="focusIssue(issue)"><b>{{ issue.code }}</b><span>{{ issue.message }}</span><small>定位 →</small></button></section>
    <SubflowExtractionDialog v-if="extraction" :definition="extraction.definition" :output-types="outputTypes" :selected-node-ids="extraction.ids" @close="extraction=null" @created="emit('subflow-created', $event)" @open="extraction=null; emit('open-subflow', $event)" />
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
