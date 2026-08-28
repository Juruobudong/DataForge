<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import dagre from '@dagrejs/dagre'
import { MarkerType, VueFlow, useVueFlow } from '@vue-flow/core'
import { Background, BackgroundVariant } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import OperatorNode from './nodes/OperatorNode.vue'
import FlowInputNode from './nodes/FlowInputNode.vue'
import SubflowNode from './nodes/SubflowNode.vue'
import KnowledgeSinkNode from './nodes/KnowledgeSinkNode.vue'
import FlowEdge from './edges/FlowEdge.vue'
import ConnectionLine from './edges/ConnectionLine.vue'
import { removeElements } from './flowModel'
import { useFlowConnections } from './composables/useFlowConnections'
import { useFlowSelection } from './composables/useFlowSelection'
import { nearestPort, SNAP_RADIUS } from './edge/edgeCompatibility.js'
import { beginEdgeInteraction, idleEdgeInteraction } from './edge/edgeInteraction.js'

const nodes = defineModel('nodes', { required: true })
const edges = defineModel('edges', { required: true })
const props = defineProps({ issue: Object, mode: { type: String, default: 'edit' }, height: { type: [String, Number], default: 720 }, canvasId: { type: String, default: 'dataforge-template-flow' }, showTechnicalCode: { type: Boolean, default: false }, flowContext: { type: Object, default: () => ({ schemaVersion: 3, outputTypes: [] }) } })
const emit = defineEmits(['before-change', 'change', 'select-node', 'select-edge', 'connection-error', 'connection-source', 'add-definition', 'open-subflow'])
const editable = computed(() => props.mode === 'edit')
const compact = computed(() => props.mode === 'mini')
const canvasHeight = computed(() => {
  const value = String(props.height).trim()
  return typeof props.height === 'number' || /^\d+(?:\.\d+)?$/.test(value) ? `${value}px` : value
})
const root = ref(null)
const interaction = ref(idleEdgeInteraction())
const pendingReconnectEdge = ref(null)
const connectionCommitted = ref(false)
const connectionCancelled = ref(false)
const hoverTooltip = ref(null)
const flashTooltip = ref(null)
const edgeMenu = ref(null)
const edgeMenuButton = ref(null)
const nodesReady = ref(false)
let fitRequested = false
let fitFrame = 0
let disposed = false
let flashTimer = 0
provide('dataforge-edge-interaction', interaction)
const effectiveFlowContext = computed(() => ({ schemaVersion: 3, outputTypes: [], ...props.flowContext }))
const { fitView, screenToFlowCoordinate, setCenter, endConnection } = useVueFlow(props.canvasId)
const { isValidConnection, addTypedEdge, reconnectTypedEdge } = useFlowConnections(nodes, edges, effectiveFlowContext, issue => emit('connection-error', issue))
const { selectedNodes, selectedEdges } = useFlowSelection(nodes, edges)

const allowedTargetCount = computed(() => [...interaction.value.compatiblePorts.values()].filter(value => value.allowed).length)
const interactionContract = computed(() => {
  const result = [...interaction.value.compatiblePorts.values()].find(value => value.allowed)
  return interaction.value.mode === 'reconnecting-source' ? result?.resolvedTargetType : result?.resolvedSourceType
})
const interactionLabel = computed(() => {
  if (interaction.value.mode === 'idle') return props.mode === 'runtime' ? 'Runtime DAG · 不可变快照' : '强类型 DAG · Flow DSL v3'
  const action = interaction.value.mode.startsWith('reconnecting') ? '正在重新连接' : '正在连接'
  const contract = interactionContract.value || '待解析 Contract'
  return `${action} · ${interaction.value.mode === 'reconnecting-source' ? `? → ${contract}` : `${contract} → ?`} · ${allowedTargetCount.value} 个合法端口`
})
const visibleTooltip = computed(() => hoverTooltip.value || flashTooltip.value)

function connect(connection) {
  if (!editable.value || connectionCancelled.value) return
  connectionCommitted.value = addTypedEdge(connection, () => emit('before-change'))
}
function connectStart(params) {
  if (!editable.value) return
  if (params.handleType === 'source') emit('connection-source', { nodeId: params.nodeId, port: params.handleId || 'output' })
  connectionCommitted.value = false; connectionCancelled.value = false; hoverTooltip.value = null; flashTooltip.value = null
  const original = pendingReconnectEdge.value
  if (!original && params.handleType !== 'source') return
  const mode = !original ? 'connecting' : params.handleType === 'source' ? 'reconnecting-target' : 'reconnecting-source'
  interaction.value = beginEdgeInteraction({
    mode, flowContext: effectiveFlowContext.value, nodes: nodes.value, edges: edges.value,
    sourceNodeId: params.handleType === 'source' ? params.nodeId : undefined,
    sourcePortId: params.handleType === 'source' ? params.handleId || 'output' : undefined,
    targetNodeId: params.handleType === 'target' ? params.nodeId : undefined,
    targetPortId: params.handleType === 'target' ? params.handleId || 'input' : undefined,
    originalEdgeId: original?.id,
  })
}
function showDropError(value, event) {
  if (!value || value.allowed) return
  emit('connection-error', { code: value.reasonCode, ...value })
  const bounds = root.value?.getBoundingClientRect()
  flashTooltip.value = { result: value, x: (event?.clientX || bounds?.left || 0) - (bounds?.left || 0) + 12, y: (event?.clientY || bounds?.top || 0) - (bounds?.top || 0) + 12 }
  clearTimeout(flashTimer)
  flashTimer = window.setTimeout(() => { flashTooltip.value = null }, 2500)
}
function resetInteraction() {
  interaction.value = idleEdgeInteraction(); pendingReconnectEdge.value = null; hoverTooltip.value = null
}
function connectEnd(event) {
  if (!connectionCommitted.value && !connectionCancelled.value) {
    const result = interaction.value.compatiblePorts.get(interaction.value.hoveredPortKey)
    showDropError(result, event)
  }
  resetInteraction(); connectionCommitted.value = false; connectionCancelled.value = false
}
function edgeUpdateStart({ edge }) { pendingReconnectEdge.value = edge; emit('select-edge', edge) }
function edgeUpdate({ edge, connection }) {
  if (connectionCancelled.value) return
  connectionCommitted.value = reconnectTypedEdge(edge, connection, () => emit('before-change'))
  if (connectionCommitted.value) emit('select-edge', edges.value.find(item => item.id === edge.id) || edge)
}
function edgeUpdateEnd() { if (interaction.value.mode !== 'idle') resetInteraction() }
function pointerMove(event) {
  if (interaction.value.mode === 'idle' || !root.value) return
  const direction = interaction.value.mode === 'reconnecting-source' ? 'output' : 'input'
  const handles = [...root.value.querySelectorAll(`.typed-handle[data-port-key*="::${direction}::"]`)].map(element => {
    const bounds = element.getBoundingClientRect()
    return { key: element.dataset.portKey, x: bounds.left + bounds.width / 2, y: bounds.top + bounds.height / 2 }
  })
  const nearest = nearestPort({ x: event.clientX, y: event.clientY }, handles, interaction.value.compatiblePorts, SNAP_RADIUS)
  interaction.value = { ...interaction.value, hoveredPortKey: nearest?.key, snapTargetKey: nearest?.compatibility?.allowed ? nearest.key : undefined }
  if (nearest?.compatibility && !nearest.compatibility.allowed) {
    const bounds = root.value.getBoundingClientRect()
    hoverTooltip.value = { result: nearest.compatibility, x: event.clientX - bounds.left + 12, y: event.clientY - bounds.top + 12 }
  } else hoverTooltip.value = null
}
function drop(event) {
  event.preventDefault()
  if (!editable.value) return
  const raw = event.dataTransfer?.getData('application/dataforge-operator')
  if (!raw) return
  emit('add-definition', JSON.parse(raw), screenToFlowCoordinate({ x: event.clientX, y: event.clientY }))
}
function editableTarget(target) { return target instanceof Element && Boolean(target.closest('input, textarea, select, [contenteditable="true"]')) }
function closeEdgeMenu(restoreFocus = true) {
  const focus = edgeMenu.value?.restoreFocus
  edgeMenu.value = null
  if (restoreFocus && focus instanceof HTMLElement) nextTick(() => focus.focus())
}
function deleteElements(nodeIds = [], edgeIds = []) {
  if (!nodeIds.length && !edgeIds.length) return false
  emit('before-change')
  const result = removeElements(nodes.value, edges.value, nodeIds, edgeIds)
  nodes.value = result.nodes; edges.value = result.edges
  emit('select-node', null); emit('select-edge', null); closeEdgeMenu(false)
  return true
}
function deleteEdge(edgeId) { return deleteElements([], [edgeId]) }
function openEdgeMenu({ event, edge }) {
  if (!editable.value) return
  event.preventDefault(); emit('select-edge', edge)
  const bounds = root.value?.getBoundingClientRect()
  edgeMenu.value = { edge, x: event.clientX - (bounds?.left || 0), y: event.clientY - (bounds?.top || 0), restoreFocus: document.activeElement }
  nextTick(() => edgeMenuButton.value?.focus())
}
function keydown(event) {
  if (!editable.value) return
  if (event.key === 'Escape') {
    if (edgeMenu.value) { event.preventDefault(); closeEdgeMenu(); return }
    if (interaction.value.mode !== 'idle') {
      event.preventDefault(); connectionCancelled.value = true; interaction.value = { ...interaction.value, cancelled: true }
      endConnection(event, false); resetInteraction()
    }
    return
  }
  if (!['Delete', 'Backspace'].includes(event.key) || editableTarget(event.target)) return
  const nodeIds = selectedNodes.value.map(node => node.id), edgeIds = selectedEdges.value.map(edge => edge.id)
  if (!nodeIds.length && !edgeIds.length) return
  event.preventDefault(); deleteElements(nodeIds, edgeIds)
}
function autoLayout() {
  if (!nodes.value.length) return
  if (editable.value) emit('before-change')
  const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: 'LR', ranksep: 90, nodesep: 55, edgesep: 24, marginx: 35, marginy: 35 })
  nodes.value.forEach(node => graph.setNode(node.id, { width: node.data.meta.kind === 'knowledge_sink' ? 250 : 270, height: 145 }))
  edges.value.forEach(edge => graph.setEdge(edge.source, edge.target))
  dagre.layout(graph)
  nodes.value = nodes.value.map(node => { const point = graph.node(node.id); return { ...node, position: { x: point.x - point.width / 2, y: point.y - point.height / 2 } } })
  nextTick(() => fitView({ padding: .14, duration: 280 }))
}
function focusElement(issue) {
  if (!issue) return
  nodes.value = nodes.value.map(node => ({ ...node, selected: node.id === issue.nodeId }))
  edges.value = edges.value.map(edge => ({ ...edge, selected: edge.id === issue.edgeId }))
  const node = nodes.value.find(item => item.id === issue.nodeId)
  if (node) { emit('select-node', node); setCenter(node.position.x + 135, node.position.y + 72, { zoom: 1.15, duration: 300 }) }
}
function consumeFitRequest() {
  if (!fitRequested || !nodesReady.value || !nodes.value.length) return
  fitRequested = false
  nextTick(() => {
    if (disposed) return
    if (fitFrame) cancelAnimationFrame(fitFrame)
    fitFrame = requestAnimationFrame(() => fitView({ padding: compact.value ? .08 : .14, duration: 250 }))
  })
}
function fit() { fitRequested = true; consumeFitRequest() }
function nodesInitialized() { nodesReady.value = true; consumeFitRequest() }
watch(() => nodes.value.map(node => node.id).join('\u0000'), () => { nodesReady.value = false })
onMounted(() => { window.addEventListener('keydown', keydown); if (props.mode !== 'edit') fit() })
onBeforeUnmount(() => { disposed = true; clearTimeout(flashTimer); if (fitFrame) cancelAnimationFrame(fitFrame); window.removeEventListener('keydown', keydown) })
defineExpose({ autoLayout, deleteEdge, focusElement, fit, screenToFlowCoordinate })
</script>

<template>
  <div ref="root" class="flow-canvas" :class="`mode-${mode}`" :style="{ height: canvasHeight }" tabindex="-1" @pointermove="pointerMove" @dragover.prevent @drop="drop">
    <VueFlow :id="canvasId" v-model:nodes="nodes" v-model:edges="edges"
      :is-valid-connection="editable ? isValidConnection : () => false"
      :default-edge-options="{ type: 'dataforge', markerEnd: MarkerType.ArrowClosed }"
      :delete-key-code="null" :connection-radius="SNAP_RADIUS" :edge-updater-radius="12" :edges-updatable="editable" :min-zoom=".25" :max-zoom="2" :snap-to-grid="true" :snap-grid="[10, 10]"
      :nodes-draggable="editable" :nodes-connectable="editable" elements-selectable zoom-on-scroll pan-on-drag :selection-on-drag="editable"
      @connect="connect" @connect-start="connectStart" @connect-end="connectEnd" @edge-update-start="edgeUpdateStart" @edge-update="edgeUpdate" @edge-update-end="edgeUpdateEnd" @edge-context-menu="openEdgeMenu" @nodes-initialized="nodesInitialized" @node-click="emit('select-node', $event.node)" @edge-click="emit('select-edge', $event.edge)"
      @node-double-click="$event.node.data.meta.kind === 'subflow' && emit('open-subflow', $event.node)"
      @pane-click="emit('select-node', null); emit('select-edge', null); closeEdgeMenu(false)" @node-drag-start="editable && emit('before-change')" @node-drag-stop="editable && emit('change')">
      <Background :variant="BackgroundVariant.Dots" :gap="18" :size="1.1" pattern-color="#cad3df" bg-color="#f7f9fc" />
      <Controls v-if="!compact" position="bottom-right" />
      <MiniMap v-if="!compact" position="bottom-left" :pannable="true" :zoomable="true" :node-stroke-width="2" node-color="#dce8fa" mask-color="rgba(238,242,247,.72)" />
      <template #node-operator="nodeProps"><OperatorNode v-bind="nodeProps" :show-technical-code="showTechnicalCode" /></template>
      <template #node-flow-input="nodeProps"><FlowInputNode v-bind="nodeProps" /></template>
      <template #node-subflow="nodeProps"><SubflowNode v-bind="nodeProps" :show-technical-code="showTechnicalCode" /></template>
      <template #node-knowledge-sink="nodeProps"><KnowledgeSinkNode v-bind="nodeProps" /></template>
      <template #edge-dataforge="edgeProps"><FlowEdge v-bind="edgeProps" /></template>
      <template #connection-line="lineProps"><ConnectionLine v-bind="lineProps" /></template>
      <div v-if="!compact" class="canvas-chip" :class="{ connecting: interaction.mode !== 'idle' }">{{ interactionLabel }}</div>
    </VueFlow>
    <div v-if="visibleTooltip" class="edge-tooltip" :style="{ left: `${visibleTooltip.x}px`, top: `${visibleTooltip.y}px` }"><b>✕ 不能连接</b><span>{{ visibleTooltip.result.message }}</span><small v-if="visibleTooltip.result.resolvedSourceType || visibleTooltip.result.resolvedTargetType">{{ visibleTooltip.result.resolvedSourceType || '?' }} → {{ visibleTooltip.result.resolvedTargetType || '?' }}</small></div>
    <div v-if="edgeMenu" class="edge-context-menu" :style="{ left: `${edgeMenu.x}px`, top: `${edgeMenu.y}px` }"><small>{{ edgeMenu.edge.source }}.{{ edgeMenu.edge.sourceHandle || 'output' }} → {{ edgeMenu.edge.target }}.{{ edgeMenu.edge.targetHandle || 'input' }}</small><button ref="edgeMenuButton" @click="deleteEdge(edgeMenu.edge.id)">删除连接</button></div>
  </div>
</template>

<style scoped>
.flow-canvas{position:relative;min-width:620px;overflow:hidden;border:1px solid #dbe3ef;border-radius:12px;background:#f7f9fc;box-shadow:var(--shadow)}.flow-canvas.mode-mini{min-width:0;border:0;border-radius:8px;box-shadow:none}.flow-canvas :deep(.vue-flow){height:100%}.flow-canvas :deep(.vue-flow__node){border:0;background:transparent;padding:0}.flow-canvas :deep(.vue-flow__node.selected){box-shadow:none}.flow-canvas :deep(.vue-flow__controls){overflow:hidden;border:1px solid #dbe3ef;border-radius:9px;box-shadow:0 5px 18px rgba(30,51,82,.12)}.flow-canvas :deep(.vue-flow__controls-button){width:30px;height:30px;border-bottom-color:#edf0f4}.flow-canvas :deep(.vue-flow__minimap){overflow:hidden;border:1px solid #dbe3ef;border-radius:9px;background:#fff;box-shadow:0 5px 18px rgba(30,51,82,.1)}.flow-canvas :deep(.vue-flow__edge.selected .vue-flow__edge-path){stroke:#2f6fed;stroke-width:3}.flow-canvas :deep(marker path){fill:context-stroke;stroke:context-stroke}.canvas-chip{position:absolute;z-index:7;top:12px;left:12px;padding:6px 9px;border:1px solid #dce4ef;border-radius:8px;color:#65748a;background:rgba(255,255,255,.92);font-size:8px;font-weight:800;pointer-events:none}.canvas-chip.connecting{border-color:#b8cef5;color:#2f6fed;background:#f4f8ff;box-shadow:0 5px 18px rgba(47,111,237,.12)}
.flow-canvas :deep(.vue-flow__handle.connecting:not(.valid)){background:#c4ccd7!important;box-shadow:0 0 0 2px rgba(201,74,74,.12)}
.edge-tooltip{position:absolute;z-index:20;display:grid;max-width:260px;gap:3px;padding:9px 11px;border:1px solid #e7bcbc;border-radius:8px;color:#a33f3f;background:#fff7f7;box-shadow:0 8px 24px rgba(72,31,31,.16);pointer-events:none}.edge-tooltip b{font-size:10px}.edge-tooltip span{font-size:10px}.edge-tooltip small{color:#7d5b5b;font:9px/1.4 monospace}.edge-context-menu{position:absolute;z-index:22;display:grid;min-width:190px;gap:7px;padding:8px;border:1px solid #d7deea;border-radius:9px;background:#fff;box-shadow:0 12px 34px rgba(30,41,59,.2)}.edge-context-menu small{color:#68758a;font:9px/1.4 monospace}.edge-context-menu button{text-align:left;color:#b33d3d;background:#fff7f7;border-color:#efd0d0}
</style>
