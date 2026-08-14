<script setup>
import { computed, ref, watch } from 'vue'
const props = defineProps({ node: Object, artifact: Object, content: Object })
const emit = defineEmits(['inspect-artifact'])
const tab = ref('overview')
watch(() => props.node?.node_id || props.artifact?.id, () => { tab.value = 'overview' })
const title = computed(() => props.artifact ? `Artifact · ${props.artifact.type || 'execution'}` : props.node?.node_id || '选择节点或 Artifact Edge')
const inspectorType = computed(() => {
  const code = `${props.node?.operator_code || ''} ${props.node?.node_id || ''}`.toLowerCase()
  if (code.includes('document-parser')) return 'Document Parser / OCR 诊断'
  if (code.includes('chunk')) return 'Chunk 诊断'
  if (code.includes('qa')) return 'QA 诊断'
  if (code.includes('graph') || code.includes('entity') || code.includes('relation')) return 'Graph 诊断'
  if (code.includes('quality')) return 'Quality Gate 诊断'
  if (code.includes('sink')) return 'Knowledge Sink 诊断'
  if (code.includes('vector') || code.includes('milvus')) return 'Vector Sync / Milvus 只读阶段'
  return '通用节点诊断'
})
const payload = computed(() => {
  if (props.artifact) return props.content || props.artifact
  if (!props.node) return null
  return { overview: { status: props.node.status, operator: `${props.node.operator_code || ''}@${props.node.operator_version || ''}`, duration_ms: props.node.duration_ms, error: props.node.error_detail || props.node.error }, parameters: props.node.resolved_parameters, input: props.node.input_artifact_ids, output: props.node.output_artifact_ids, logs: props.node.logs, metrics: props.node.metrics, lineage: props.node.lineage }
})
</script>

<template>
  <aside class="runtime-inspector">
    <header><div><h3>{{ title }}</h3><small v-if="node">{{ inspectorType }}</small></div><span v-if="node" class="badge" :class="node.status==='completed'?'green':node.status==='failed'?'red':'amber'">{{ node.status }}</span></header>
    <nav v-if="node && !artifact"><button v-for="item in ['overview','parameters','input','output','logs','metrics','lineage']" :key="item" :class="{active:tab===item}" @click="tab=item">{{ item }}</button></nav>
    <div class="body" v-if="payload"><pre>{{ JSON.stringify(artifact ? payload : payload[tab], null, 2) }}</pre><template v-if="node && ['input','output'].includes(tab)"><button v-for="id in payload[tab] || []" :key="id" class="artifact-link" @click="emit('inspect-artifact', id)">{{ id }}</button></template></div>
    <p v-else class="empty">从 Runtime DAG 选择真实算子节点，或点击 Artifact Edge 查看数据。</p>
  </aside>
</template>

<style scoped>
.runtime-inspector{height:100%;overflow:auto;border:1px solid #dbe3ef;border-radius:12px;background:#fff}.runtime-inspector header{display:flex;align-items:center;justify-content:space-between;padding:15px;border-bottom:1px solid #edf0f4}.runtime-inspector h3{margin:0;font-size:14px}.runtime-inspector nav{display:flex;flex-wrap:wrap;gap:4px;padding:8px;border-bottom:1px solid #edf0f4}.runtime-inspector nav button{border:0;background:transparent;padding:6px;color:#65748a;font-size:10px}.runtime-inspector nav button.active{color:#2f6fed;background:#edf4ff}.body{padding:12px}.body pre{max-height:520px;overflow:auto;margin:0;padding:10px;border-radius:8px;background:#f6f8fb;font-size:10px;white-space:pre-wrap}.artifact-link{display:block;width:100%;margin-top:6px;text-align:left;font-size:10px}.empty{padding:22px;color:#7b8798;line-height:1.6}
.runtime-inspector header small{display:block;margin-top:4px;color:#748196;font-size:9px}
</style>
