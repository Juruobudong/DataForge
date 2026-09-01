<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../../../api/platform'
import DataForgeFlowCanvas from '../DataForgeFlowCanvas.vue'
import OperatorPalette from '../palette/OperatorPalette.vue'
import NodeInspector from '../inspector/NodeInspector.vue'
import EdgeInspector from '../inspector/EdgeInspector.vue'
import GraphSchemaEditor from '../../graph/GraphSchemaEditor.vue'
import PromptPreview from '../../graph/PromptPreview.vue'
import SubflowExtractionDialog from '../SubflowExtractionDialog.vue'
import { deserializeDefinition, makeCanvasNode, serializeDefinition, validateFlow, validateSubflow, subflowNodeDefinition, resolveSubflow, operatorAvailable, keepCompatibleParams, connectionIssue } from '../flowModel'
import { useFlowHistory } from '../composables/useFlowHistory'
import { removeEntityReferences } from '../../graph/entityTypeModel'
import { upstreamSignals } from '../upstreamSignals.js'

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
const discoveryDirection = ref('downstream')
const signalNodes = computed(() => upstreamSignals(nodes.value, edges.value, selectedNode.value?.id, props.catalog, props.subflows))
const evaluationNodes = computed(() => signalNodes.value.filter(node => node.operator !== 'SemDeduplicateFilter'))
const deduplicationNodes = computed(() => signalNodes.value.filter(node => node.operator === 'SemDeduplicateFilter'))
const connectionSource = ref(null)
const candidateResults = ref(null), candidateError = ref(''), candidatesLoading = ref(false)
const graphWarnings = computed(() => candidateResults.value?.find(item => item.compatibility?.graph_warnings?.length)?.compatibility.graph_warnings || [])
const runtimeUnknown = computed(() => candidateResults.value?.some(item => item.runtime_status?.status === 'unknown'))
let candidateTimer, candidateSequence = 0
const issues = ref([]), focusedIssue = ref(null), connectionError = ref(null)
const nodes = ref([]), edges = ref([])
const canvas = ref(null), editor = ref(null)
const graphConfig = ref({ entity_types: [], relation_types: [], literal_policy: { enabled_datatypes: [] }, unknown_entity_policy: 'reject', unknown_relation_policy: 'reject', prompt: { mode: 'generated', body: null } })
const graphConfigOpen = ref(false)
const graphConfigPanel = ref(null), graphSchemaEditor = ref(null), graphConfigButton = ref(null), promptPanel = ref(null)
const promptNodeId = ref('')
const promptDefinition = computed(() => serialize())
let graphNavigation = 0

function scrollToSection(target) {
  target?.focus?.({ preventScroll: true })
  target?.scrollIntoView?.({ block: 'start', behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 'instant' : 'smooth' })
}
async function openGraphConfig({ part = 'entities', nodeId = '' } = {}) {
  if (!hasGraphOutput.value) return
  const sequence = ++graphNavigation
  graphConfigOpen.value = true
  if (nodeId) promptNodeId.value = nodeId
  await nextTick()
  if (sequence !== graphNavigation || !graphConfigOpen.value || !hasGraphOutput.value) return
  scrollToSection(part === 'prompt' ? promptPanel.value : part === 'relations' ? graphSchemaEditor.value?.section(part) : graphConfigPanel.value)
}
async function returnToCanvas(collapse = false) {
  const sequence = ++graphNavigation
  if (collapse) graphConfigOpen.value = false
  await nextTick()
  if (sequence === graphNavigation) scrollToSection(graphConfigButton.value)
}
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
  graphNavigation++; promptNodeId.value = ''
  connectionSource.value = null
  const graph = deserializeDefinition(value, props.catalog, props.subflows)
  nodes.value = graph.nodes; edges.value = graph.edges
  graphConfig.value = normalizeGraphConfig(value?.graph_config)
  graphConfigOpen.value = false
  selectedNode.value = null; selectedEdge.value = null; candidateResults.value = null; issues.value = []; connectionError.value = null
  clearHistory()
  nextTick(() => canvas.value?.fit())
}
function reset() {
  graphNavigation++; promptNodeId.value = ''
  connectionSource.value = null
  selectedNode.value = null; selectedEdge.value = null; candidateResults.value = null
  nodes.value = []; edges.value = []
  graphConfig.value = normalizeGraphConfig(null); graphConfigOpen.value = false
  issues.value = []; connectionError.value = null; clearHistory()
}
function dragStart(event, item, kind) {
  event.dataTransfer.setData('application/dataforge-operator', JSON.stringify(subflowNodeDefinition(item, kind)))
  event.dataTransfer.effectAllowed = 'copy'
}
function addDefinition(raw, position) {
  if (raw.kind === 'subflow') {
    const item = resolveSubflow(raw, props.subflows)
    if (!item || item.revision_status !== 'published' || item.status !== 'active') { emit('error', '该子流程尚未发布或不可用'); return }
    raw = { ...raw, subflow_revision_id: item.revision_id || item.latest_revision_id }
  }
  beforeChange()
  const definition = { ...raw, id: uniqueId(raw.kind === 'subflow' ? raw.ref : raw.ref || 'node') }
  const node = makeCanvasNode(definition, { x: position.x - 135, y: position.y - 70 }, props.catalog, props.subflows)
  nodes.value.push(node)
  if (definition.kind === 'operator' && definition.ref === 'document-chunker') {
    const gateDefinition = { id: uniqueId('input-review-gate'), kind: 'execution_gate', locked: true }
    const gate = makeCanvasNode(gateDefinition, { x: position.x + 210, y: position.y - 70 }, props.catalog, props.subflows)
    nodes.value.push(gate)
    edges.value.push({ id: uniqueId('edge'), type: 'dataforge', source: node.id, sourceHandle: 'output', target: gate.id, targetHandle: 'input', data: { status: 'idle' } })
  }
  return node
}
function addItem(item, kind) {
  const discovery = selectedNode.value && kind === 'operator'
    ? candidateResults.value?.find(value => value.code === item.code)?.compatibility : null
  const canConnect = discovery?.compatible
  if (discovery && !canConnect) return
  const rect = editor.value?.getBoundingClientRect()
  const anchor = selectedNode.value
  const position = canConnect
    ? { x: anchor.position.x + (discoveryDirection.value === 'downstream' ? 405 : -135), y: anchor.position.y + 70 }
    : canvas.value?.screenToFlowCoordinate({ x: (rect?.left || 500) + (rect?.width || 900) / 2, y: (rect?.top || 200) + 300 }) || { x: 320, y: 200 }
  const node = addDefinition(subflowNodeDefinition(item, kind), position)
  if (!node || !canConnect) return
  const edge = discoveryDirection.value === 'downstream'
    ? { source: anchor.id, sourceHandle: discovery.source_port, target: node.id, targetHandle: discovery.target_port }
    : { source: node.id, sourceHandle: discovery.source_port, target: anchor.id, targetHandle: discovery.target_port }
  edges.value.push({ id: uniqueId('edge'), type: 'dataforge', ...edge, data: { status: 'idle' } })
  nodes.value.forEach(value => { value.selected = value.id === node.id })
  selectedNode.value = node; selectedEdge.value = null; discoveryDirection.value = 'downstream'
}
function refreshCandidates() {
  clearTimeout(candidateTimer)
  const sequence = ++candidateSequence
  if (props.purpose !== 'knowledge' || (!selectedNode.value && !connectionSource.value)) {
    candidateResults.value = null; candidatesLoading.value = false; candidateError.value = ''; return
  }
  candidatesLoading.value = true; candidateResults.value = []; candidateError.value = ''
  candidateTimer = setTimeout(async () => {
    try {
      const context = selectedNode.value && !connectionSource.value
        ? { node_id: selectedNode.value.id, direction: discoveryDirection.value, include_incompatible: true }
        : { source_node_id: connectionSource.value?.nodeId, source_port: connectionSource.value?.port || 'output', include_incompatible: true }
      const values = await api.operatorCandidates({ definition: serialize(), output_types: props.outputTypes, ...context })
      if (sequence === candidateSequence) candidateResults.value = values
    } catch (error) {
      if (sequence === candidateSequence) candidateError.value = error.message || '算子候选查询失败'
    } finally {
      if (sequence === candidateSequence) candidatesLoading.value = false
    }
  }, 150)
}
watch(() => [serialize(), connectionSource.value, selectedNode.value?.id, discoveryDirection.value, props.outputTypes, props.catalog], refreshCandidates, { deep: true })
onBeforeUnmount(() => { clearTimeout(candidateTimer); candidateSequence++ })
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
function addSink(outputKey, compatibility = null) {
  if (nodes.value.some(node => node.data.definition.kind === 'knowledge_sink' && node.data.definition.output_key === outputKey)) { emit('error', `输出 ${outputKey} 已有 Knowledge Sink`); return }
  beforeChange()
  const family = outputFamily(outputKey), mode = outputKey.includes(':') ? outputKey.split(':')[1] : null
  const definition = { id: uniqueId(`sink-${outputKey.replace(':', '-')}`), kind: 'knowledge_sink', knowledge_type: family, graph_mode: mode, output_key: outputKey }
  const anchor = selectedNode.value
  const node = makeCanvasNode(definition, compatibility?.compatible && anchor ? { x: anchor.position.x + 270, y: anchor.position.y } : { x: 760, y: 120 + nodes.value.length * 14 }, props.catalog, props.subflows)
  nodes.value.push(node)
  if (compatibility?.compatible && anchor) {
    edges.value.push({ id: uniqueId('edge'), type: 'dataforge', source: anchor.id, sourceHandle: compatibility.source_port, target: node.id, targetHandle: compatibility.target_port, data: { status: 'idle' } })
    nodes.value.forEach(value => { value.selected = value.id === node.id })
    selectedNode.value = node; selectedEdge.value = null; discoveryDirection.value = 'downstream'
  }
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
function changeOperatorVersion(version) {
  if (!selectedNode.value) return
  const item = props.catalog.find(item => item.code === selectedNode.value.data.definition.ref)?.versions?.find(item => item.version === version)
  if (!item) return
  beforeChange()
  const definition = selectedNode.value.data.definition
  definition.operator_version = version; delete definition.operator_spec
  definition.params = Object.fromEntries(Object.entries(definition.params || {}).filter(([key]) => key in (item.parameter_schema?.properties || {}) || ['knowledge_type', 'graph_mode'].includes(key)))
  selectedNode.value.data.meta = makeCanvasNode(definition, selectedNode.value.position, props.catalog, props.subflows).data.meta
  validate()
}
function replaceOperator(item) {
  const old = selectedNode.value
  if (!old || old.data.meta.nodeRole !== 'operator' || old.data.definition.kind !== 'operator' || !item) return
  if (item.node_role === 'flow_input' || item.code === 'document-input' || !operatorAvailable(item, props.purpose, props.outputTypes)) return
  if (old.data.definition.ref === item.code && old.data.definition.operator_version === item.version) return
  const definition = { id: old.id, kind: 'operator', node_role: 'operator', ref: item.code,
    operator_version: item.version, params: keepCompatibleParams(old.data.definition.params, item.parameter_schema) }
  const next = { ...makeCanvasNode(definition, old.position, props.catalog, props.subflows), selected: true }
  const nextNodes = nodes.value.map(node => node.id === old.id ? next : node)
  let nextEdges = [...edges.value]
  const removed = []
  for (const edge of edges.value.filter(edge => edge.source === old.id || edge.target === old.id)) {
    const issue = connectionIssue(edge, nextNodes, nextEdges.filter(item => item.id !== edge.id))
    if (issue) { nextEdges = nextEdges.filter(item => item.id !== edge.id); removed.push(`${edge.source} → ${edge.target}：${issue.message}`) }
  }
  remember()
  nodes.value = nextNodes; edges.value = nextEdges; selectedNode.value = next; selectedEdge.value = null
  focusedIssue.value = null; connectionSource.value = null
  markDirty()
  // Focus uses the child canvas's models; wait until both graph props have
  // committed so validation cannot write the previous graph back into state.
  nextTick(() => validate())
  if (removed.length) emit('error', `替换完成，已删除 ${removed.length} 条不兼容连线：${removed.join('；')}`)
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
function selectNode(node) { selectedNode.value = node; selectedEdge.value = null; connectionError.value = null; connectionSource.value = null; discoveryDirection.value = 'downstream' }
function selectEdge(edge) { selectedEdge.value = edge; selectedNode.value = null; connectionSource.value = null; connectionError.value = null }
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
onBeforeUnmount(() => { graphNavigation++; window.removeEventListener('keydown', shortcut) })
</script>

<template>
  <div class="advanced-editor" :class="{ 'knowledge-flow-editor': !fragment }">
    <div class="flow-toolbar">
      <div><button :disabled="!canUndo" title="Ctrl+Z" @click="undo">↶ 撤销</button><button :disabled="!canRedo" title="Ctrl+Shift+Z" @click="redo">↷ 重做</button><span></span><button @click="autoLayout">自动布局</button><button @click="canvas?.fit()">适应画布</button></div>
      <div><span class="selection-state">{{ nodes.length }} 节点 · {{ edges.length }} 连线</span><button v-if="!fragment" :disabled="!nodes.some(node => node.selected)" @click="extractSelection">另存为可复用子流程</button><button v-if="hasGraphOutput" ref="graphConfigButton" :class="{ active: graphConfigOpen }" :aria-expanded="graphConfigOpen" aria-controls="graph-config-panel" @click="openGraphConfig()">图谱抽取配置</button></div>
    </div>
    <div ref="editor" class="flow-workspace">
      <OperatorPalette :catalog="catalog" :subflows="subflows" :output-types="fragment ? [] : outputTypes" :purpose="purpose" :nodes="nodes" :edges="edges" :source="connectionSource" :selected-node="selectedNode" :direction="discoveryDirection" :candidate-results="candidateResults" :loading="candidatesLoading" :error="candidateError" @retry="refreshCandidates" @clear-source="connectionSource = null" @change-direction="discoveryDirection = $event" @drag-start="dragStart" @add-item="addItem" @add-sink="addSink" />
      <DataForgeFlowCanvas ref="canvas" v-model:nodes="nodes" v-model:edges="edges" height="var(--flow-canvas-height)" :issue="focusedIssue" :flow-context="{ schemaVersion: 3, outputTypes }" :show-technical-code="!fragment" @before-change="beforeChange" @change="markDirty" @select-node="selectNode" @select-edge="selectEdge" @connection-source="connectionSource = $event" @connection-error="reportConnectionError" @add-definition="addDefinition" @open-subflow="openSubflow" />
      <EdgeInspector v-if="selectedEdge" :edge="selectedEdge" :nodes="nodes" :issue="selectedIssue" @delete="deleteEdge" />
      <NodeInspector v-else :node="selectedNode" :catalog="catalog" :subflows="subflows" :purpose="purpose" :output-types="outputTypes" :entity-types="graphConfig.entity_types" :evaluation-nodes="evaluationNodes" :deduplication-nodes="deduplicationNodes" :issue="selectedIssue" :sample-result="sampleResult" @replace-operator="replaceOperator" @apply-parameters="applyParameters" @open-graph-config="openGraphConfig" @open-subflow="openSubflow(selectedNode)" @change-subflow-revision="changeSubflowRevision" @change-operator-version="changeOperatorVersion" />
    </div>
    <section v-if="runtimeUnknown" class="validation-panel" role="status">部分算子运行状态暂不可用，连线兼容性仍有效。<button :disabled="candidatesLoading" @click="refreshCandidates">刷新运行状态</button></section>
    <section v-if="graphWarnings.length" class="validation-panel" aria-label="草稿已有问题"><h3>草稿已有问题（不影响无关候选）</h3><button v-for="(warning,index) in graphWarnings" :key="index" @click="focusIssue({ ...warning, nodeId: warning.details?.target_node_id })">{{ warning.message }}</button></section>
    <section v-if="graphConfigOpen && hasGraphOutput" id="graph-config-panel" ref="graphConfigPanel" class="graph-config-panel" tabindex="-1" aria-label="全流程图谱规则">
      <header class="graph-config-heading"><div><h3>全流程图谱规则</h3><p>实体与关系抽取器共用的类型定义；结果仍经过图谱结构、质量校验。业务抽取要求在各节点中编辑。</p></div><div><button type="button" @click="returnToCanvas()">返回画布</button><button type="button" @click="returnToCanvas(true)">收起</button></div></header>
      <GraphSchemaEditor ref="graphSchemaEditor" :model-value="graphConfig" @update:model-value="applyGraphConfig" />
      <div ref="promptPanel" tabindex="-1" class="graph-prompt-panel"><PromptPreview :definition="promptDefinition" v-model:selected-node-id="promptNodeId" /></div>
    </section>
    <section v-if="issues.length" class="validation-panel"><div><h3>画布校验</h3><span>{{ issues.length }} 个问题</span></div><button v-for="(issue,index) in issues" :key="`${issue.code}-${index}`" @click="focusIssue(issue)"><b>{{ issue.code }}</b><span>{{ issue.message }}</span><small>定位 →</small></button></section>
    <SubflowExtractionDialog v-if="extraction" :definition="extraction.definition" :output-types="outputTypes" :selected-node-ids="extraction.ids" @close="extraction=null" @created="emit('subflow-created', $event)" @open="extraction=null; emit('open-subflow', $event)" />
  </div>
</template>

<style scoped>
.advanced-editor { --flow-canvas-height: 720px; display: grid; gap: 10px; }
.knowledge-flow-editor { --flow-canvas-height: max(720px, calc(100dvh - 240px)); }
.knowledge-flow-editor :deep(.operator-palette), .knowledge-flow-editor :deep(.edge-inspector) { height: var(--flow-canvas-height); min-height: 0; }
.knowledge-flow-editor :deep(.edge-inspector) { overflow: auto; }
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
<style scoped>
.graph-config-panel,.graph-prompt-panel,.flow-toolbar button,.graph-config-panel :deep(.schema-block){scroll-margin-top:88px}.graph-config-heading{grid-column:1/-1;display:flex;justify-content:space-between;gap:16px;align-items:start}.graph-config-heading h3{margin:0;font-size:18px}.graph-config-heading p{margin:8px 0 0;color:var(--muted);font-size:13px}.graph-config-heading>div:last-child{display:flex;gap:8px;flex-shrink:0}.graph-prompt-panel{min-width:0}
</style>
