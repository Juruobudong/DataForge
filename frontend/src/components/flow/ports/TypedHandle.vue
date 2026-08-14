<script setup>
import { computed, inject } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { artifactMatches, resolveCandidateType } from '../flowModel.js'

const props = defineProps({ port: { type: String, required: true }, spec: { type: Object, required: true }, direction: { type: String, required: true }, definition: { type: Object, default: () => ({}) }, nodeKind: String })
const activeConnection = inject('dataforge-active-connection', computed(() => null))
const handleType = computed(() => props.direction === 'input' ? 'target' : 'source')
const position = computed(() => props.direction === 'input' ? Position.Left : Position.Right)
const targetState = computed(() => {
  if (props.direction !== 'input' || !activeConnection.value) return ''
  const expected = resolveCandidateType(props.spec.artifact_type, props.definition)
  const graphFallback = props.nodeKind === 'knowledge_sink' && expected.startsWith('candidate:graph:') && activeConnection.value.type === 'candidate:graph'
  return expected !== 'source_file' && (artifactMatches(activeConnection.value.type, expected) || graphFallback) ? 'compatible-target' : 'incompatible-target'
})
</script>

<template>
  <div class="typed-port" :class="`typed-port--${direction}`" :title="`${port} · ${spec.artifact_type || '未知类型'} · ${spec.cardinality || 'one'}`">
    <Handle :id="port" :type="handleType" :position="position" class="typed-handle" :class="targetState" />
    <span class="port-name">{{ port }}</span>
    <small>{{ spec.artifact_type || '未知类型' }}</small>
  </div>
</template>

<style scoped>
.typed-port{position:relative;display:grid;min-height:34px;align-content:center;gap:1px;padding:4px 14px}.typed-port--input{text-align:left}.typed-port--output{text-align:right}.port-name{overflow:hidden;color:#334155;font-size:10px;font-weight:800;text-overflow:ellipsis}.typed-port small{overflow:hidden;color:#8290a5;font-size:8px;text-overflow:ellipsis}.typed-handle{width:10px!important;height:10px!important;border:2px solid #fff!important;background:#7692bd!important;box-shadow:0 0 0 1px #7692bd;transition:transform .15s,background .15s,box-shadow .15s}.typed-handle:hover,.typed-handle.connecting{transform:scale(1.3);background:#2f6fed!important;box-shadow:0 0 0 3px rgba(47,111,237,.18)}.typed-handle.valid{background:#1d8c65!important;box-shadow:0 0 0 3px rgba(29,140,101,.18)}
.typed-handle.compatible-target{transform:scale(1.24);background:#1d8c65!important;box-shadow:0 0 0 4px rgba(29,140,101,.2)}.typed-handle.incompatible-target{background:#c4ccd7!important;box-shadow:0 0 0 1px #c4ccd7;opacity:.48}
</style>
