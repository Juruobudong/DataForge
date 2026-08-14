<script setup>
import { computed } from 'vue'
import { getSmoothStepPath, Position } from '@vue-flow/core'
const props = defineProps({ sourceX: Number, sourceY: Number, targetX: Number, targetY: Number, sourcePosition: String, targetPosition: String, connectionStatus: String })
const path = computed(() => getSmoothStepPath({ sourceX: props.sourceX, sourceY: props.sourceY, targetX: props.targetX, targetY: props.targetY, sourcePosition: props.sourcePosition || Position.Right, targetPosition: props.targetPosition || Position.Left, borderRadius: 10 })[0])
</script>
<template><g><path :d="path" class="connection-line" :class="`is-${connectionStatus || 'pending'}`"/><circle :cx="targetX" :cy="targetY" r="4" class="connection-target"/></g></template>
<style scoped>.connection-line{fill:none;stroke:#2f6fed;stroke-width:2.5;stroke-dasharray:7 4}.connection-line.is-invalid{stroke:#c94a4a}.connection-line.is-valid{stroke:#1d8c65;stroke-dasharray:none}.connection-target{fill:#fff;stroke:#2f6fed;stroke-width:2}</style>
