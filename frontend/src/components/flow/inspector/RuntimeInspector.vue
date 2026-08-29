<script setup>
import { computed, ref, watch } from 'vue'
import { operatorPrimaryName, operatorSubtitle } from '../flowModel'
import { nodeFailureInfo } from '../runtimeDiagnostics'
const props = defineProps({ node: Object, artifact: Object, content: Object, operator: Object })
const emit = defineEmits(['inspect-artifact'])
const tabs = [
  { key: 'overview', label: '概览' },
  { key: 'parameters', label: '参数' },
  { key: 'input', label: '输入' },
  { key: 'output', label: '输出' },
  { key: 'logs', label: '日志' },
  { key: 'metrics', label: '指标' },
  { key: 'lineage', label: '血缘' },
]
const tab = ref('overview')
const failure = computed(() => nodeFailureInfo(props.node))
function showDiagnostics() { tab.value = 'logs' }
defineExpose({ showDiagnostics })
watch(() => props.node?.node_id || props.artifact?.id, () => { tab.value = 'overview' })
const title = computed(() => props.artifact ? `Artifact · ${props.artifact.type || 'execution'}` : (props.operator && operatorPrimaryName(props.operator)) || props.node?.node_id || '选择节点或 Artifact Edge')
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
  const error = (typeof props.node.error_detail === 'string' ? props.node.error_detail : props.node.error_detail?.message) || props.node.error || null
  return { overview: { status: props.node.status, operator: `${props.node.operator_code || ''}@${props.node.operator_version || ''}`, duration_ms: props.node.duration_ms, error }, parameters: props.node.resolved_parameters, input: props.node.input_artifact_ids, output: props.node.output_artifact_ids, logs: props.node.logs, metrics: props.node.metrics, lineage: props.node.lineage }
})
const derivedItems = computed(() => (props.content?.items || []).filter(item => item.source_chunk && typeof item.effective_text === 'string'))
const evaluatedItems = computed(() => (props.content?.items || []).filter(item => item.evaluation_results))
</script>

<template>
  <aside class="runtime-inspector">
    <header><div><h3>{{ title }}</h3><small v-if="operator && !artifact" class="operator-bilingual">{{ operatorSubtitle(operator, true) }}</small><small v-if="node">{{ inspectorType }} · {{ node.node_id }}</small></div><span v-if="node" class="badge" :class="node.status==='failed'?'red':failure.hasFailure?'amber':node.status==='completed'?'green':'amber'">{{ node.status }}{{ node.status === 'completed' && failure.hasFailure ? ' · 有处理失败' : '' }}</span></header>
    <nav v-if="node && !artifact"><button v-for="item in tabs" :key="item.key" :class="{active:tab===item.key}" @click="tab=item.key">{{ item.label }}</button></nav>
    <div class="body" v-if="payload">
      <p v-if="!artifact && failure.recoveredChunks && ['overview', 'logs'].includes(tab)" role="status">格式恢复后成功：{{ failure.recoveredChunks }} 块</p>
      <section v-if="!artifact && failure.hasFailure && ['overview', 'logs'].includes(tab)" class="failure-summary" aria-label="失败原因">
        <h4>{{ failure.title }}</h4>
        <p v-for="stat in failure.processing" :key="stat.output_key">{{ stat.output_key }}：成功 {{ stat.successful_chunks }} 块 · 失败 {{ stat.failed_chunks }} 块</p>
        <p v-if="!failure.processing.length">失败分块数量未记录，不根据日志条数推算。</p>
        <p v-for="reason in failure.reasons" :key="reason" class="failure-reason">{{ reason }}</p>
        <p v-if="!failure.reasons.length">没有已记录的明确失败原因，请检查节点原始日志；没有日志时无法进一步判断。</p>
        <p v-if="failure.explanation">{{ failure.explanation }}</p>
        <button v-if="tab !== 'logs'" @click="showDiagnostics">查看原始日志</button>
      </section>
      <section v-if="!artifact && tab === 'logs'" class="operator-logs" aria-label="算子日志">
        <p v-if="!node.logs?.length">暂无算子日志。日志在节点结束后显示。</p>
        <article v-for="(log, index) in node.logs || []" :key="index"><h4>{{ log.stream || '日志' }} <span v-if="log.truncated" class="truncated">已截断（每流最多 32 KiB）</span></h4><pre>{{ log.message }}</pre></article>
      </section>
      <section v-else-if="artifact && derivedItems.length" class="derived-preview" aria-label="派生正文">
        <p>保留 {{ derivedItems.filter(item=>item.disposition==='keep').length }} 条 · 过滤 {{ derivedItems.filter(item=>item.disposition==='filtered').length }} 条（当前页）</p>
        <article v-for="(item,index) in derivedItems" :key="index"><h4>{{ item.disposition === 'keep' ? '保留' : '正常过滤' }}</h4><h4>原始正文 · Evidence 保留</h4><pre>{{ item.source_chunk.content }}</pre><h4>处理后正文</h4><pre>{{ item.effective_text }}</pre><details><summary>来源与处理记录</summary><pre>{{ JSON.stringify(item,null,2) }}</pre></details></article>
      </section>
      <section v-else-if="artifact && evaluatedItems.length" aria-label="QA质量评估"><p>以下为模型评分，不等于原始证据事实核验。</p><pre>{{ JSON.stringify(payload,null,2) }}</pre></section>
      <pre v-else>{{ JSON.stringify(artifact ? payload : payload[tab], null, 2) }}</pre>
      <template v-if="node && ['input','output'].includes(tab)"><button v-for="id in payload[tab] || []" :key="id" class="artifact-link" @click="emit('inspect-artifact', id)">{{ id }}</button></template>
    </div>
    <p v-else class="empty">从 Runtime DAG 选择真实算子节点，或点击 Artifact Edge 查看数据。</p>
  </aside>
</template>

<style scoped>
.runtime-inspector{height:100%;overflow:auto;border:1px solid #dbe3ef;border-radius:12px;background:#fff}.runtime-inspector header{display:flex;align-items:center;justify-content:space-between;padding:15px;border-bottom:1px solid #edf0f4}.runtime-inspector h3{margin:0;font-size:14px}.runtime-inspector nav{display:flex;flex-wrap:wrap;gap:4px;padding:8px;border-bottom:1px solid #edf0f4}.runtime-inspector nav button{border:0;background:transparent;padding:6px;color:#65748a;font-size:10px}.runtime-inspector nav button.active{color:#2f6fed;background:#edf4ff}.body{padding:12px}.body pre{max-height:520px;overflow:auto;margin:0;padding:10px;border-radius:8px;background:#f6f8fb;font-size:10px;white-space:pre-wrap}.artifact-link{display:block;width:100%;margin-top:6px;text-align:left;font-size:10px}.empty{padding:22px;color:#7b8798;line-height:1.6}
.runtime-inspector header small{display:block;margin-top:4px;color:#748196;font-size:9px}
.operator-logs h4{font-size:13px;margin:12px 0 6px}.operator-logs pre{font:13px/1.7 monospace;overflow-wrap:anywhere}.operator-logs p{font-size:13px;color:#64748b}.truncated{font-size:12px;color:#986316;font-weight:400}
.failure-summary{margin-bottom:12px;padding:12px;border:1px solid #e8c879;border-radius:8px;background:#fff8e8;color:#795518;font-size:13px;line-height:1.7;overflow-wrap:anywhere}.failure-summary h4{margin:0 0 8px}.failure-summary p{margin:6px 0;white-space:pre-wrap}.failure-reason{font-family:ui-monospace,monospace;color:#9b3434}
</style>

<style scoped>.runtime-inspector header>div{min-width:0}.runtime-inspector h3,.operator-bilingual{overflow-wrap:anywhere;white-space:normal}</style>
