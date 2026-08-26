<script setup>
import { computed } from 'vue'
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath } from '@vue-flow/core'
const props = defineProps({ id: String, sourceX: Number, sourceY: Number, targetX: Number, targetY: Number, sourcePosition: String, targetPosition: String, markerEnd: String, selected: Boolean, data: Object })
const geometry = computed(() => getSmoothStepPath({ sourceX: props.sourceX, sourceY: props.sourceY, targetX: props.targetX, targetY: props.targetY, sourcePosition: props.sourcePosition, targetPosition: props.targetPosition, borderRadius: 10 }))
</script>
<template><BaseEdge :id="id" :path="geometry[0]" :marker-end="markerEnd" :interaction-width="18" class="dataforge-edge" :class="[`state-${data?.status || 'idle'}`, { selected }]" /><EdgeLabelRenderer v-if="data?.label"><span class="artifact-edge-label" :title="data?.technicalLabel || data?.label" :style="{ transform: `translate(-50%, -50%) translate(${geometry[1]}px,${geometry[2]}px)` }">{{ data.label }}</span></EdgeLabelRenderer></template>
<style>
.dataforge-edge{stroke:#91a0b5;stroke-width:1.8;transition:stroke .15s,stroke-width .15s}.dataforge-edge:hover{stroke:#5b7eb5;stroke-width:2.6}.dataforge-edge.selected{stroke:#2f6fed;stroke-width:3}.dataforge-edge.state-running{stroke:#2f6fed;stroke-dasharray:7 5;animation:flow-dash .8s linear infinite}.dataforge-edge.state-success{stroke:#1d8c65}.dataforge-edge.state-failed{stroke:#c94a4a}@keyframes flow-dash{to{stroke-dashoffset:-12}}
.artifact-edge-label{position:absolute;z-index:4;padding:3px 6px;border:1px solid #d7e0ec;border-radius:6px;color:#55657b;background:rgba(255,255,255,.94);font-size:9px;font-weight:700;pointer-events:all}
</style>
