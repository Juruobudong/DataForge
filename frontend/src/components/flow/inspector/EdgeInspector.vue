<script setup>
import { computed } from 'vue'

const props = defineProps({ edge: { type: Object, required: true }, nodes: { type: Array, default: () => [] }, issue: Object })
defineEmits(['delete'])
const source = computed(() => props.nodes.find(node => node.id === props.edge.source))
const target = computed(() => props.nodes.find(node => node.id === props.edge.target))
const nodeLabel = node => node?.data?.meta?.name || node?.id || '未知节点'
const sourceLabel = computed(() => `${nodeLabel(source.value)}.${props.edge.sourceHandle || 'output'}`)
const targetLabel = computed(() => `${nodeLabel(target.value)}.${props.edge.targetHandle || 'input'}`)
</script>

<template>
  <aside class="edge-inspector">
    <header><div><span>EDGE</span><h3>连接详情</h3></div><button class="danger" @click="$emit('delete', edge.id)">删除连接</button></header>
    <div class="edge-route"><b>{{ sourceLabel }}</b><span>→</span><b>{{ targetLabel }}</b></div>
    <dl><dt>来源节点</dt><dd>{{ edge.source }}</dd><dt>来源端口</dt><dd>{{ edge.sourceHandle || 'output' }}</dd><dt>目标节点</dt><dd>{{ edge.target }}</dd><dt>目标端口</dt><dd>{{ edge.targetHandle || 'input' }}</dd></dl>
    <p v-if="issue" class="edge-issue"><b>{{ issue.code }}</b>{{ issue.message }}</p>
    <p class="hint">拖动连线端点可原子重连；Delete、Backspace 或右键菜单可删除。</p>
  </aside>
</template>

<style scoped>
.edge-inspector{min-width:0;padding:14px;border:1px solid var(--border);border-radius:12px;background:#fff;box-shadow:var(--shadow)}header{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;padding-bottom:12px;border-bottom:1px solid #e7ebf1}header span{color:#2f6fed;font-size:9px;font-weight:900;letter-spacing:.08em}h3{margin:3px 0 0;font-size:16px}.danger{color:#b93838;border-color:#edcaca;background:#fff}.edge-route{display:grid;gap:7px;margin:14px 0;padding:12px;border-radius:9px;background:#f5f8fc}.edge-route b{overflow:hidden;color:#334155;font-size:11px;text-overflow:ellipsis}.edge-route span{color:#2f6fed;font-weight:900}dl{display:grid;grid-template-columns:74px minmax(0,1fr);gap:8px;margin:0}dt{color:#7b8798;font-size:10px}dd{overflow:hidden;margin:0;color:#435166;font:10px/1.4 monospace;text-overflow:ellipsis}.edge-issue{display:grid;gap:3px;margin:12px 0 0;padding:9px;border:1px solid #efc9c9;border-radius:8px;color:#a63f3f;background:#fff4f4;font-size:10px}.hint{margin:12px 0 0;color:#748197;font-size:10px;line-height:1.55}
</style>
