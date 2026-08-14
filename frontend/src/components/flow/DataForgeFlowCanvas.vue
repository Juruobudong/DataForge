<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref } from 'vue'
import dagre from '@dagrejs/dagre'
import { MarkerType, VueFlow, useVueFlow } from '@vue-flow/core'
import { Background, BackgroundVariant } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import OperatorNode from './nodes/OperatorNode.vue'
import SubflowNode from './nodes/SubflowNode.vue'
import KnowledgeSinkNode from './nodes/KnowledgeSinkNode.vue'
import FlowEdge from './edges/FlowEdge.vue'
import ConnectionLine from './edges/ConnectionLine.vue'
import { removeElements, resolveCandidateType } from './flowModel'
import { useFlowConnections } from './composables/useFlowConnections'
import { useFlowSelection } from './composables/useFlowSelection'

const nodes = defineModel('nodes', { required: true })
const edges = defineModel('edges', { required: true })
const props = defineProps({ issue: Object, mode: { type: String, default: 'edit' }, height: { type: [String, Number], default: 720 }, canvasId: { type: String, default: 'dataforge-template-flow' } })
const emit = defineEmits(['before-change', 'select-node', 'select-edge', 'connection-error', 'add-definition', 'open-subflow'])
const editable = computed(() => props.mode === 'edit')
const compact = computed(() => props.mode === 'mini')
const root = ref(null)
const activeConnection = ref(null)
provide('dataforge-active-connection', activeConnection)
const { fitView, screenToFlowCoordinate, setCenter } = useVueFlow(props.canvasId)
const { isValidConnection, addTypedEdge } = useFlowConnections(nodes, edges, issue => emit('connection-error', issue))
const { selectedNodes, selectedEdges } = useFlowSelection(nodes, edges)

function connect(connection) { if (!editable.value) return; emit('before-change'); if (!addTypedEdge(connection)) return }
function connectStart(params) {
  if (params.handleType !== 'source') return
  const node = nodes.value.find(item => item.id === params.nodeId)
  const port = node?.data.meta.outputs?.[params.handleId || 'output']
  activeConnection.value = port ? { nodeId: params.nodeId, handleId: params.handleId, type: resolveCandidateType(port.artifact_type, node.data.definition) } : null
}
function connectEnd() { activeConnection.value = null }
function drop(event) {
  event.preventDefault()
  if (!editable.value) return
  const raw = event.dataTransfer?.getData('application/dataforge-operator')
  if (!raw) return
  emit('add-definition', JSON.parse(raw), screenToFlowCoordinate({ x: event.clientX, y: event.clientY }))
}
function editableTarget(target) { return target instanceof Element && Boolean(target.closest('input, textarea, select, [contenteditable="true"]')) }
function keydown(event) {
  if (!editable.value) return
  if (!['Delete', 'Backspace'].includes(event.key) || editableTarget(event.target)) return
  const nodeIds = selectedNodes.value.map(node => node.id), edgeIds = selectedEdges.value.map(edge => edge.id)
  if (!nodeIds.length && !edgeIds.length) return
  event.preventDefault(); emit('before-change')
  const result = removeElements(nodes.value, edges.value, nodeIds, edgeIds)
  nodes.value = result.nodes; edges.value = result.edges
  emit('select-node', null); emit('select-edge', null)
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
function fit() { nextTick(() => fitView({ padding: .14, duration: 250 })) }
onMounted(() => { window.addEventListener('keydown', keydown); if (props.mode !== 'edit') nextTick(() => fitView({ padding: compact.value ? .08 : .14 })) })
onBeforeUnmount(() => window.removeEventListener('keydown', keydown))
defineExpose({ autoLayout, focusElement, fit, screenToFlowCoordinate })
</script>

<template>
  <div ref="root" class="flow-canvas" :class="`mode-${mode}`" :style="{ height: typeof height === 'number' ? `${height}px` : height }" @dragover.prevent @drop="drop">
    <VueFlow :id="canvasId" v-model:nodes="nodes" v-model:edges="edges"
      :is-valid-connection="editable ? isValidConnection : () => false"
      :default-edge-options="{ type: 'dataforge', markerEnd: MarkerType.ArrowClosed }"
      :delete-key-code="null" :min-zoom=".25" :max-zoom="2" :snap-to-grid="true" :snap-grid="[10, 10]"
      :nodes-draggable="editable" :nodes-connectable="editable" elements-selectable zoom-on-scroll pan-on-drag :selection-on-drag="editable"
      @connect="connect" @connect-start="connectStart" @connect-end="connectEnd" @node-click="emit('select-node', $event.node)" @edge-click="emit('select-edge', $event.edge)"
      @node-double-click="$event.node.data.meta.kind === 'subflow' && emit('open-subflow', $event.node)"
      @pane-click="emit('select-node', null)" @node-drag-start="editable && emit('before-change')">
      <Background :variant="BackgroundVariant.Dots" :gap="18" :size="1.1" pattern-color="#cad3df" bg-color="#f7f9fc" />
      <Controls v-if="!compact" position="bottom-right" />
      <MiniMap v-if="!compact" position="bottom-left" :pannable="true" :zoomable="true" :node-stroke-width="2" node-color="#dce8fa" mask-color="rgba(238,242,247,.72)" />
      <template #node-operator="nodeProps"><OperatorNode v-bind="nodeProps" /></template>
      <template #node-subflow="nodeProps"><SubflowNode v-bind="nodeProps" /></template>
      <template #node-knowledge-sink="nodeProps"><KnowledgeSinkNode v-bind="nodeProps" /></template>
      <template #edge-dataforge="edgeProps"><FlowEdge v-bind="edgeProps" /></template>
      <template #connection-line="lineProps"><ConnectionLine v-bind="lineProps" /></template>
      <div v-if="!compact" class="canvas-chip">{{ mode === 'runtime' ? 'Runtime DAG · 不可变快照' : '强类型 DAG · Flow DSL v3' }}</div>
    </VueFlow>
  </div>
</template>

<style scoped>
.flow-canvas{position:relative;min-width:620px;overflow:hidden;border:1px solid #dbe3ef;border-radius:12px;background:#f7f9fc;box-shadow:var(--shadow)}.flow-canvas.mode-mini{min-width:0;border:0;border-radius:8px;box-shadow:none}.flow-canvas :deep(.vue-flow){height:100%}.flow-canvas :deep(.vue-flow__node){border:0;background:transparent;padding:0}.flow-canvas :deep(.vue-flow__node.selected){box-shadow:none}.flow-canvas :deep(.vue-flow__controls){overflow:hidden;border:1px solid #dbe3ef;border-radius:9px;box-shadow:0 5px 18px rgba(30,51,82,.12)}.flow-canvas :deep(.vue-flow__controls-button){width:30px;height:30px;border-bottom-color:#edf0f4}.flow-canvas :deep(.vue-flow__minimap){overflow:hidden;border:1px solid #dbe3ef;border-radius:9px;background:#fff;box-shadow:0 5px 18px rgba(30,51,82,.1)}.flow-canvas :deep(.vue-flow__edge.selected .vue-flow__edge-path){stroke:#2f6fed;stroke-width:3}.flow-canvas :deep(marker path){fill:context-stroke;stroke:context-stroke}.canvas-chip{position:absolute;top:12px;left:12px;padding:6px 9px;border:1px solid #dce4ef;border-radius:8px;color:#65748a;background:rgba(255,255,255,.9);font-size:8px;font-weight:800;pointer-events:none}
.flow-canvas :deep(.vue-flow__handle.connecting:not(.valid)){background:#c4ccd7!important;box-shadow:0 0 0 2px rgba(201,74,74,.12)}
</style>
