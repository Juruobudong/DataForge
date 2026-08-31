<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import TypedHandle from '../ports/TypedHandle.vue'
import { operatorNodeSubtitle } from '../flowModel.js'
import { api } from '../../../api/platform'
import { serviceStatus } from '../../../views/developer/modelServices.js'
import { edgeNodeClasses } from '../edge/edgeInteraction.js'
const props = defineProps({ id: { type: String, required: true }, data: { type: Object, required: true }, selected: Boolean, showTechnicalCode: { type: Boolean, default: false } })
const edgeInteraction = inject('dataforge-edge-interaction', computed(() => ({ mode: 'idle', compatiblePorts: new Map() })))
const interactionClasses = computed(() => edgeNodeClasses(edgeInteraction.value, props.id))
const servings = ref([])
const usesServing = computed(() => Boolean(props.data.meta.parameterSchema?.properties?.llm_serving))
const headerSubtitle = computed(() => operatorNodeSubtitle(props.data.meta, props.showTechnicalCode))
const serving = computed(() => {
  const code = props.data.definition.params?.llm_serving
  return servings.value.find(item => item.serving_code === code) || (!code ? servings.value.find(item => item.is_default) : null)
})
const edgeInputs = computed(() => Object.fromEntries(Object.entries(props.data.meta.inputs || {}).filter(([, spec]) => (spec.binding || 'edge') === 'edge')))
const editableCount = computed(() => Object.keys(props.data.meta.parameterSchema?.properties || {}).length)
const businessSummary = computed(() => {
  const params = props.data.definition.params || {}, code = props.data.meta.code
  if (!editableCount.value) return '系统节点'
  if (code === 'quality-evaluator' || code === 'quality-filter') return params.quality_profile_revision_id || `可配置 ${editableCount.value}`
  if (code === 'entity-extractor') return `${(params.entity_type_scope || (params.entity_types?.length ? 'subset' : 'all')) === 'all' ? '全部实体类型' : `${(params.entity_types || []).length} 种实体`} · ≥${Number(params.confidence_threshold ?? .7).toFixed(2)}`
  if (code === 'relation-extractor') return `${(params.relation_types || []).length} 种关系`
  if (code === 'entity-relation-extractor') return '实体＋关系 · 每块一次联合抽取'
  return `可配置 ${editableCount.value}`
})
onMounted(async () => { if (usesServing.value) { try { servings.value = await api.modelServings() } catch { servings.value = [] } } })
</script>

<template>
  <article class="flow-node operator-node" :class="[`state-${data.meta.status || 'idle'}`, { selected }, interactionClasses]">
    <header><span class="node-icon">◇</span><div><b>{{ data.meta.name }}</b><small>{{ headerSubtitle }}</small></div><span class="node-status">{{ data.meta.status === 'success' ? '✓' : data.meta.status === 'failed' ? '!' : '' }}</span></header>
    <section class="ports" :class="{ split: Object.keys(edgeInputs).length && Object.keys(data.meta.outputs).length }">
      <div><TypedHandle v-for="(spec, port) in edgeInputs" :key="`in-${port}`" :node-id="id" :port="port" :spec="spec" :definition="data.definition" :node-kind="data.meta.kind" direction="input" /></div>
      <div><TypedHandle v-for="(spec, port) in data.meta.outputs" :key="`out-${port}`" :node-id="id" :port="port" :spec="spec" :definition="data.definition" :node-kind="data.meta.kind" direction="output" /></div>
    </section>
    <section v-if="usesServing" class="serving-line"><b>🤖 {{ serving?.name || data.definition.params?.llm_serving || '系统默认' }}</b><small v-if="serving">● {{ serviceStatus(serving).label }}</small></section>
    <footer><span>{{ data.meta.category }}</span><span>{{ businessSummary }}</span></footer>
  </article>
</template>

<style scoped>
.flow-node{width:270px;overflow:hidden;border:1px solid #dbe3ef;border-radius:11px;background:#fff;box-shadow:0 7px 22px rgba(30,51,82,.09);transition:border-color .15s,box-shadow .15s,transform .15s}.flow-node:hover{border-color:#aebfda;box-shadow:0 10px 28px rgba(30,51,82,.13)}.flow-node.selected{border-color:#2f6fed;box-shadow:0 0 0 2px rgba(47,111,237,.16),0 10px 28px rgba(30,51,82,.13)}header{display:grid;grid-template-columns:30px minmax(0,1fr) 16px;gap:9px;align-items:center;padding:12px 13px;border-bottom:1px solid #edf0f4}.node-icon{display:grid;width:30px;height:30px;place-items:center;border-radius:8px;color:#2f6fed;background:#eaf1ff;font-size:16px;font-weight:900}header b,header small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}header b{font-size:11px}header small{margin-top:3px;color:#7a879a;font-size:8px}.node-status{font-weight:900}.ports{display:grid;min-height:58px;align-items:start;padding:6px 0}.ports.split{grid-template-columns:1fr 1fr}.ports.split>div+div{border-left:1px solid #f0f2f6}footer{display:flex;justify-content:space-between;padding:8px 13px;border-top:1px solid #edf0f4;color:#758196;background:#fafbfd;font-size:8px;font-weight:750}.state-running{border-color:#6d99ef}.state-success .node-status{color:#1d8c65}.state-failed{border-color:#d66b6b}.state-failed .node-status{color:#c94a4a}.state-disabled{opacity:.58}
.flow-node.edge-source-node{border-color:#2f6fed;box-shadow:0 0 0 3px rgba(47,111,237,.18)}.flow-node.edge-compatible-node{border-color:#1d8c65;box-shadow:0 0 0 3px rgba(29,140,101,.15)}.flow-node.edge-incompatible-node{opacity:.45}
.serving-line{display:flex;align-items:center;justify-content:space-between;padding:8px 13px;border-top:1px solid #edf0f4;background:#f6f9ff;color:#2f6fed}.serving-line b{font-size:9px}.serving-line small{font-size:8px}
</style>

<style scoped>header b,header small{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere}header>div{min-width:0}</style>
