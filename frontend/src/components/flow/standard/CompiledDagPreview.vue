<script setup>
import { computed, ref, watch } from 'vue'
import DataForgeFlowCanvas from '../DataForgeFlowCanvas.vue'
import { deserializeDefinition } from '../flowModel'

const props = defineProps({
  compiledDefinition: { type: Object, default: null },
  catalog: { type: Array, default: () => [] },
})

const nodes = ref([])
const edges = ref([])

watch(() => props.compiledDefinition, (value) => {
  const graph = value ? deserializeDefinition(value, props.catalog, []) : { nodes: [], edges: [] }
  nodes.value = graph.nodes
  edges.value = graph.edges
}, { immediate: true })

const summary = computed(() => {
  const value = props.compiledDefinition
  if (!value) return null
  const sinks = (value.nodes || []).filter(node => node.kind === 'knowledge_sink').length
  return { nodes: (value.nodes || []).length, edges: (value.edges || []).length, sinks }
})
</script>

<template>
  <div class="compiled-dag-preview">
    <header class="preview-head">
      <div>
        <b>执行 DAG</b>
        <span v-if="summary" class="preview-summary">{{ summary.nodes }} 节点 · {{ summary.edges }} 连线 · {{ summary.sinks }} Sink</span>
      </div>
      <span class="preview-hint">后端真实编译结果，非前端示意</span>
    </header>
    <div v-if="summary" class="preview-canvas">
      <DataForgeFlowCanvas v-model:nodes="nodes" v-model:edges="edges" mode="runtime" height="320" canvas-id="compiled-dag-preview" />
    </div>
    <p v-else class="preview-empty">修改参数后自动生成执行 DAG，或点击「查看执行 DAG」刷新。</p>
  </div>
</template>

<style scoped>
.compiled-dag-preview { border: 1px solid var(--border, #dfe5ed); border-radius: 11px; background: #f7f9fc; overflow: hidden; }
.preview-head { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid var(--border, #dfe5ed); }
.preview-head b { color: #34445a; }
.preview-summary { margin-left: 10px; color: #8290a3; font-size: 11px; }
.preview-hint { color: #8a97a8; font-size: 11px; }
.preview-canvas { height: 320px; }
.preview-empty { padding: 22px 14px; color: #8290a3; font-size: 12px; text-align: center; }
</style>
