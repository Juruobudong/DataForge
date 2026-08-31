<script setup>
import { computed, inject } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { portKey } from '../edge/edgeCompatibility.js'

const props = defineProps({ nodeId: { type: String, required: true }, port: { type: String, required: true }, spec: { type: Object, required: true }, direction: { type: String, required: true }, definition: { type: Object, default: () => ({}) }, nodeKind: String })
const edgeInteraction = inject('dataforge-edge-interaction', computed(() => ({ mode: 'idle', compatiblePorts: new Map() })))
const handleType = computed(() => props.direction === 'input' ? 'target' : 'source')
const position = computed(() => props.direction === 'input' ? Position.Left : Position.Right)
const key = computed(() => portKey(props.nodeId, props.direction, props.port))
const compatibility = computed(() => edgeInteraction.value?.compatiblePorts?.get(key.value))
const targetState = computed(() => {
  if (edgeInteraction.value?.mode === 'idle') return ''
  if (edgeInteraction.value?.snapTargetKey === key.value) return 'snapped-target'
  if (compatibility.value?.allowed) return 'compatible-target'
  if (compatibility.value) return 'incompatible-target'
  if (props.direction === 'output' && edgeInteraction.value?.sourceNodeId === props.nodeId && edgeInteraction.value?.sourcePortId === props.port) return 'active-source'
  return ''
})
const title = computed(() => {
  const base = `${props.port} · ${props.spec.artifact_type || '未知类型'} · ${props.spec.cardinality || 'one'}`
  if (!compatibility.value || compatibility.value.allowed) return base
  return `${base}\n${compatibility.value.message}${compatibility.value.resolvedSourceType || compatibility.value.resolvedTargetType ? `\n${compatibility.value.resolvedSourceType || '?'} → ${compatibility.value.resolvedTargetType || '?'}` : ''}`
})
</script>

<template>
  <div class="typed-port" :class="`typed-port--${direction}`" :title="title">
    <Handle :id="port" :type="handleType" :position="position" class="typed-handle" :class="targetState" :data-port-key="key" />
    <span class="port-name">{{ port }}</span>
    <small>{{ spec.artifact_type || '未知类型' }}</small>
  </div>
</template>

<style scoped>
.typed-port{position:relative;display:grid;min-height:34px;align-content:center;gap:1px;padding:4px 14px}.typed-port--input{text-align:left}.typed-port--output{text-align:right}.port-name{overflow:hidden;color:#334155;font-size:10px;font-weight:800;text-overflow:ellipsis}.typed-port small{overflow:hidden;color:#8290a5;font-size:8px;text-overflow:ellipsis}.typed-handle{width:10px!important;height:10px!important;border:2px solid #fff!important;background:#7692bd!important;box-shadow:0 0 0 1px #7692bd;transition:scale .15s,background .15s,box-shadow .15s}.typed-handle:hover,.typed-handle.connecting{scale:1.3;background:#2f6fed!important;box-shadow:0 0 0 3px rgba(47,111,237,.18)}.typed-handle.valid{background:#1d8c65!important;box-shadow:0 0 0 3px rgba(29,140,101,.18)}
.typed-handle.compatible-target{scale:1.24;background:#1d8c65!important;box-shadow:0 0 0 4px rgba(29,140,101,.2)}.typed-handle.snapped-target{scale:1.48;background:#137956!important;box-shadow:0 0 0 6px rgba(29,140,101,.24)}.typed-handle.active-source{scale:1.35;background:#2f6fed!important;box-shadow:0 0 0 5px rgba(47,111,237,.22)}.typed-handle.incompatible-target{background:#c4ccd7!important;box-shadow:0 0 0 1px #c4ccd7;opacity:.48}
</style>
