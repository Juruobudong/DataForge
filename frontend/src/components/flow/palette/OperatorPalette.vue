<script setup>
import { computed, ref } from 'vue'
import { makeCanvasNode, subflowPrimaryName, subflowSubtitle, operatorPrimaryName, operatorSubtitle, operatorAvailable } from '../flowModel'
import { checkEdgeCompatibility } from '../edge/edgeCompatibility'
import OperatorHelpPopover from './OperatorHelpPopover.vue'

const props = defineProps({ catalog: { type: Array, default: () => [] }, subflows: { type: Array, default: () => [] }, outputTypes: { type: Array, default: () => [] }, purpose: { type: String, default: 'knowledge' }, nodes: { type: Array, default: () => [] }, edges: { type: Array, default: () => [] }, source: { type: Object, default: null }, selectedNode: { type: Object, default: null }, direction: { type: String, default: 'downstream' }, candidateResults: { type: Array, default: null }, loading: Boolean, error: String })
const emit = defineEmits(['drag-start', 'add-item', 'add-sink', 'retry', 'clear-source', 'change-direction'])

const query = ref('')
const help = ref(null)
const expanded = ref(new Set(['DataForge 算子', 'DataFlow 精选', '自定义算子']))

const catalogGroups = { dataforge: 'DataForge 算子', dataflow_featured: 'DataFlow 精选', custom: '自定义算子' }
const categoryLabels = {
  'content-processing': '内容处理', 'knowledge-generation': '知识生成', 'quality-processing': '质量处理', 'index-processing': '索引处理',
  'text-cleaning': '文本清洗', 'content-filtering': '内容过滤', deduplication: '去重', 'text-generation': '文本生成',
}
const discoveryActive = computed(() => Boolean(props.selectedNode && !props.source))
const resultByCode = computed(() => new Map((props.candidateResults || []).map(item => [item.code, item])))
function candidate(item) { return resultByCode.value.get(item.code) }
function compatibility(item) { return candidate(item)?.compatibility }
function runtime(item) { return candidate(item)?.runtime_status || item.dependency_status || { status: 'unknown' } }
function state(item) {
  if (!discoveryActive.value) return 'available'
  if (!compatibility(item)?.compatible) return 'incompatible'
  return runtime(item).status === 'ready' ? 'compatible' : 'unready'
}
function insertable(item) { return !discoveryActive.value || state(item) === 'compatible' }
function compatibilityText(item) {
  const value = compatibility(item)
  if (!discoveryActive.value || !value) return ''
  if (!value.compatible) return value.reason || '当前方向不能直接连接'
  if (runtime(item).status !== 'ready') return `端口兼容；${runtime(item).reason || '当前运行依赖未就绪'}`
  return `${props.direction === 'downstream' ? '可接下游' : '可接上游'} · ${value.source_port} → ${value.target_port}`
}
function sinkCompatibility(outputKey) {
  if (!discoveryActive.value || props.direction !== 'downstream') return null
  const sink = makeCanvasNode({ id: '__sink_candidate__', kind: 'knowledge_sink', knowledge_type: outputKey.split(':')[0], graph_mode: outputKey.split(':')[1], output_key: outputKey }, { x: 0, y: 0 }, props.catalog, props.subflows)
  const attempts = Object.keys(props.selectedNode.data?.meta?.outputs || {}).map(sourcePort => {
    const value = checkEdgeCompatibility({ nodes: [...props.nodes, sink], edges: props.edges, flowContext: { outputTypes: props.outputTypes }, sourceNodeId: props.selectedNode.id, sourcePortId: sourcePort, targetNodeId: sink.id, targetPortId: 'input' })
    return { compatible: value.allowed, reason: value.message, reason_code: value.reasonCode, source_port: sourcePort, target_port: 'input' }
  })
  return attempts.find(value => value.compatible) || attempts[0] || { compatible: false, reason: '当前节点没有可连接到正式知识输出的端口' }
}
function sinkInsertable(outputKey) { return !discoveryActive.value || Boolean(sinkCompatibility(outputKey)?.compatible) }
function addSink(outputKey) { const value = sinkCompatibility(outputKey); if (!discoveryActive.value || value?.compatible) emit('add-sink', outputKey, value) }
function connects(raw) {
  if (!props.source?.nodeId) return true
  const sourceNode = props.nodes.find(node => node.id === props.source.nodeId)
  const candidate = makeCanvasNode({ ...raw, id: '__candidate__', params: { ...(sourceNode?.data?.definition?.params || {}) } }, { x: 0, y: 0 }, props.catalog, props.subflows)
  return Object.keys(candidate.data.meta.inputs || {}).some(port => checkEdgeCompatibility({ nodes: [...props.nodes, candidate], edges: props.edges,
    flowContext: { outputTypes: props.outputTypes }, sourceNodeId: props.source.nodeId, sourcePortId: props.source.port || 'output', targetNodeId: candidate.id, targetPortId: port }).allowed)
}
const available = computed(() => props.catalog.filter(item => {
  if (discoveryActive.value) {
    if (!candidate(item)) return false
    if (!operatorAvailable({ ...item, dependency_status: { status: 'ready' }, knowledge_types: ['*'], graph_modes: [] }, props.purpose, [])) return false
  } else if (!operatorAvailable(item, props.purpose, props.outputTypes)) return false
  if (props.candidateResults && !resultByCode.value.has(item.code)) return false
  if (item.enabled === false || item.status === 'deprecated' || item.approved === false || ['internal', 'disabled'].includes(item.exposure)) return false
  if (item.surfaces && !item.surfaces.includes(props.purpose === 'knowledge' ? 'advanced-canvas' : 'system-internal')) return false
  if (!discoveryActive.value && item.dependency_status && item.dependency_status.status !== 'ready') return false
  if (!discoveryActive.value && props.outputTypes.length && item.knowledge_types?.length && !item.knowledge_types.includes('*') && !props.outputTypes.some(kind => item.knowledge_types.includes(kind.split(':')[0]))) return false
  if (!discoveryActive.value && props.outputTypes.length && item.graph_modes?.length && !props.outputTypes.some(kind => kind.startsWith('graph') && item.graph_modes.includes(kind.split(':')[1] || 'triple'))) return false
  if (query.value && ![item.code, item.name, item.display_name_zh].join(' ').toLowerCase().includes(query.value.toLowerCase())) return false
  return discoveryActive.value || connects({ kind: 'operator', ref: item.code, operator_version: item.version })
}))
const stateOrder = { compatible: 0, unready: 1, incompatible: 2, available: 0 }
const capabilityGroups = computed(() => Object.entries(catalogGroups).map(([group, label]) => [group, label, available.value.filter(item => (item.catalog_group || 'dataforge') === group).sort((a, b) => stateOrder[state(a)] - stateOrder[state(b)])]).filter(([, , items]) => items.length))
const compatibleCount = computed(() => available.value.filter(item => state(item) === 'compatible').length)
function businessGroups(category, items) {
  const order = category === 'dataflow_featured'
    ? ['text-cleaning', 'content-filtering', 'deduplication', 'text-generation']
    : category === 'dataforge' ? ['content-processing', 'knowledge-generation', 'quality-processing', 'index-processing']
      : category === 'custom' ? ['content-processing', 'knowledge-generation', 'quality-processing', 'index-processing', 'text-cleaning', 'content-filtering', 'deduplication', 'text-generation'] : []
  return order.length
    ? order.map(code => [categoryLabels[code], items.filter(item => item.category === code)]).filter(([, values]) => values.length)
    : [['', items]]
}
const searching = computed(() => query.value.trim() !== '')
const versions = ref({})
const matchingSubflows = computed(() => props.subflows.filter(item => !unavailable(item) && [item.name, item.display_name_zh, item.code, item.description].join(' ').toLowerCase().includes(query.value.trim().toLowerCase()) && connects({ kind: 'subflow', ref: item.code, subflow_revision_id: selected(item)?.revision_id || selected(item)?.latest_revision_id })))
const published = item => (item.revisions || [item]).filter(version => version.revision_status === 'published')
const selected = item => published(item).find(version => (version.revision_id || version.latest_revision_id) === versions.value[item.id]) || published(item)[0]
const unavailable = item => !selected(item) || item.status !== 'active' || (props.purpose === 'knowledge' && item.usage === 'source_preparation')
function addOperator(item) { if (insertable(item)) emit('add-item', item, 'operator') }
function dragOperator(event, item) { if (!insertable(item)) event.preventDefault(); else { help.value?.close(); emit('drag-start', event, item, 'operator') } }
function addSubflow(item) { if (!unavailable(item)) emit('add-item', selected(item), 'subflow') }
function dragSubflow(event, item) { if (unavailable(item)) event.preventDefault(); else emit('drag-start', event, selected(item), 'subflow') }

function toggle(key) {
  const next = new Set(expanded.value)
  next.has(key) ? next.delete(key) : next.add(key)
  expanded.value = next
}
</script>

<template>
  <aside class="operator-palette">
    <div class="palette-title"><div><h3>添加节点</h3><small>{{ discoveryActive ? compatibleCount : available.length }} 个{{ discoveryActive ? '可直接连接' : source ? '可连接' : '可用' }}算子</small></div></div>
    <section v-if="discoveryActive" class="discovery-context" aria-label="上下文兼容算子">
      <b>当前节点：{{ selectedNode.data?.meta?.name || selectedNode.id }}</b>
      <div role="tablist" aria-label="连接方向"><button type="button" role="tab" :aria-selected="direction === 'upstream'" :class="{ active: direction === 'upstream' }" @click="emit('change-direction', 'upstream')">可接上游</button><button type="button" role="tab" :aria-selected="direction === 'downstream'" :class="{ active: direction === 'downstream' }" @click="emit('change-direction', 'downstream')">可接下游</button></div>
    </section>
    <label class="search"><span>⌕</span><input v-model="query" aria-label="搜索算子或子流程" placeholder="搜索名称或编码"></label>
    <p v-if="loading" class="hint">正在匹配端口与运行依赖…</p>
    <p v-if="error" class="hint" role="alert">{{ error }} <button @click="emit('retry')">重试</button></p>
    <button v-if="source" class="hint" @click="emit('clear-source')">清除端口筛选</button>
    <div class="palette-scroll">
      <section v-for="([group, category, items]) in capabilityGroups" :key="group" :class="{ 'custom-group': group === 'custom' }">
        <button class="capability-head" @click="toggle(category)"><span class="capability-name">{{ category }}</span><span class="capability-count">{{ items.length }}</span><span class="chev">{{ searching || expanded.has(category) ? '▾' : '▸' }}</span></button>
        <template v-if="searching || expanded.has(category)">
          <template v-for="([business, values]) in businessGroups(group, items)" :key="business">
          <h4 v-if="business">{{ business }}</h4>
          <div v-for="item in values" :key="item.code" class="palette-entry" :class="`compatibility-${state(item)}`" :draggable="insertable(item)" role="group" :aria-label="operatorPrimaryName(item)" :aria-disabled="!insertable(item)" :title="compatibilityText(item)" @mouseenter="help?.show($event,item,'summary')" @mouseleave="help?.leave()" @dragstart="dragOperator($event, item)" @dblclick="addOperator(item)">
            <div class="entry-body" role="button" :tabindex="insertable(item) ? 0 : -1" :aria-label="`${insertable(item) ? '添加' : '查看'}${operatorPrimaryName(item)}`" @focus="help?.show($event,item,'summary')" @blur="help?.leave()" @keydown.enter.self.prevent="addOperator(item)" @keydown.space.self.prevent="addOperator(item)"><b>{{ operatorPrimaryName(item) }}</b><small>{{ item.code }} · v{{ item.version }}</small><small v-if="discoveryActive" class="compatibility-note">{{ compatibilityText(item) }}</small></div>
            <button type="button" class="operator-info" data-operator-info draggable="false" :aria-label="`查看${operatorPrimaryName(item)}说明`" aria-haspopup="dialog" :aria-expanded="Boolean(help?.isOpen(item.code))" @mouseenter="help?.show($event,item,'detail')" @mouseleave="help?.leave()" @focus="help?.show($event,item,'detail')" @blur="help?.leave()" @pointerdown.stop @mousedown.stop @click.stop="help?.toggle($event,item)" @dblclick.stop @keydown.stop @dragstart.stop.prevent>i</button>
          </div>
          </template>
        </template>
      </section>
      <section class="subflow-section">
        <h3>可复用子流程</h3>
        <div v-for="item in matchingSubflows" :key="item.id" class="palette-entry subflow-item" :draggable="!unavailable(item)" role="button" :tabindex="unavailable(item) ? -1 : 0" :aria-disabled="unavailable(item)" title="拖入画布添加；也可双击或按 Enter / 空格添加" @dragstart="dragSubflow($event, item)" @dblclick="addSubflow(item)" @keydown.enter.self.prevent="addSubflow(item)" @keydown.space.self.prevent="addSubflow(item)">
          <b>{{ subflowPrimaryName(item) }}</b><small>{{ subflowSubtitle(selected(item) || item, true) }}</small>
          <select v-if="published(item).length" :aria-label="`${subflowPrimaryName(item)}版本`" :value="versions[item.id] || selected(item)?.revision_id || selected(item)?.latest_revision_id" @change="versions[item.id] = $event.target.value" @dblclick.stop><option v-for="version in published(item)" :key="version.revision_id" :value="version.revision_id || version.latest_revision_id">r{{ version.revision }}</option></select>
          <small v-if="item.usage === 'source_preparation'">审核前 · 文档预处理</small><small v-else-if="!selected(item)">尚未发布</small>
        </div>
        <p v-if="!matchingSubflows.length">暂无匹配的子流程</p>
      </section>
      <section v-if="outputTypes.length && (!discoveryActive || direction === 'downstream')">
        <h4>正式知识输出</h4>
        <button v-for="item in outputTypes" :key="item" class="sink-item" :class="{ 'sink-incompatible': !sinkInsertable(item) }" :disabled="!sinkInsertable(item)" :title="sinkCompatibility(item)?.reason || '双击添加正式知识输出'" @dblclick="addSink(item)"><span class="item-icon">✓</span><span><b>{{ item }}</b><small>Knowledge Sink</small><small v-if="discoveryActive">{{ sinkCompatibility(item)?.compatible ? `可接下游 · ${sinkCompatibility(item).source_port} → input` : sinkCompatibility(item)?.reason }}</small></span><span class="grab">＋</span></button>
      </section>
    </div>
    <p class="hint">拖动卡片到画布，松开即可添加</p>
    <OperatorHelpPopover ref="help" :items="available" />
  </aside>
</template>

<style scoped>
.operator-palette{display:flex;width:220px;height:720px;flex:0 0 220px;flex-direction:column;overflow:hidden;border:1px solid var(--border);border-radius:12px;background:#fff;box-shadow:var(--shadow)}
.palette-title{display:flex;align-items:center;justify-content:space-between;padding:14px 13px 10px}
.palette-title h3{margin:0;font-size:12px}
.palette-title small{color:#8490a2;font-size:8px}
.discovery-context{margin:0 10px 9px;padding:9px;border:1px solid #d8e4ff;border-radius:9px;background:#f7faff}.discovery-context>b{display:block;margin-bottom:8px;color:#294b7a;font-size:12px}.discovery-context>div{display:grid;grid-template-columns:1fr 1fr;gap:5px}.discovery-context button{min-height:30px;padding:5px;border:1px solid #ccd9ec;background:#fff;font-size:11px;text-align:center}.discovery-context button.active{border-color:#2f6fed;color:#fff;background:#2f6fed}
.search{display:flex;align-items:center;gap:6px;margin:0 10px 8px;padding:0 8px;border:1px solid #dfe5ee;border-radius:8px;background:#f9fbfd}
.search input{width:100%;min-width:0;border:0!important;background:transparent!important;outline:0!important;box-shadow:none!important}
.search span{color:#8190a5}
.palette-scroll{flex:1;overflow-y:auto;padding:2px 9px 10px}
.palette-scroll section+section{margin-top:11px}
.palette-scroll .custom-group{margin-top:18px;padding-top:12px;border-top:1px solid #dfe5ee}
.palette-scroll h4{margin:14px 0 8px;padding:6px 9px;border-left:3px solid #2f6fed;border-radius:0 5px 5px 0;background:#f2f6fc;color:#294b7a;font-size:14px;font-weight:800;line-height:1.5;letter-spacing:.02em}
.palette-scroll button{display:grid;width:100%;min-height:48px;grid-template-columns:28px minmax(0,1fr) 16px;gap:8px;align-items:center;margin:4px 0;padding:7px 8px;text-align:left}
.palette-scroll button:hover{border-color:#c9d8f3;background:#f8fbff}
.palette-scroll .capability-head{grid-template-columns:minmax(0,1fr) auto 12px;min-height:36px;margin:4px 0;border:1px solid #d8e4ff;border-radius:8px;background:#f5f8ff;font-weight:800}
.capability-head:hover{border-color:#c9d8f3;background:#eaf1ff}
.capability-name{color:#2f6fed;font-size:13px;font-weight:800}
.capability-count{color:#2f6fed;font-size:11px;background:#e4edff;border-radius:999px;padding:2px 8px;font-weight:800}
.chev{color:#5278bd;font-size:10px}
.item-icon{display:grid;width:27px;height:27px;place-items:center;border-radius:7px;color:#2f6fed;background:#eaf1ff}
.palette-scroll b,.palette-scroll small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.palette-scroll b{font-size:9px}
.palette-scroll small{margin-top:3px;color:#8290a4;font-size:7px}
.grab{color:#b1bac8}
.subflow-item .item-icon{background:#e6efff}
.sink-item .item-icon{color:#1d8c65;background:#eaf7f1}
.sink-item.sink-incompatible{opacity:.5;cursor:not-allowed}
.hint{margin:0;padding:9px 10px;border-top:1px solid #edf0f4;color:#8792a4;background:#fafbfd;font-size:7.5px;text-align:center}
</style>
<style scoped>
.palette-entry:not(.subflow-item){position:relative;display:block;padding-right:36px}.entry-body{outline:none;min-width:0}.entry-body:focus-visible{outline:2px solid #2f6fed;outline-offset:4px;border-radius:3px}.palette-scroll .operator-info{position:absolute;right:8px;top:9px;display:grid;place-items:center;grid-template-columns:1fr;width:20px;height:20px;min-height:20px;margin:0;padding:0;border:1px solid #b5c4d8;border-radius:50%;background:#fff;color:#607691;font-size:12px;font-weight:700;cursor:help}.palette-scroll .operator-info:focus-visible{outline:2px solid #2f6fed;outline-offset:2px}
</style>
<style scoped>
.palette-scroll h3{margin:12px 4px 8px;font-size:15px}.palette-entry{display:grid;grid-template-columns:minmax(0,1fr);gap:6px;margin:6px 0;padding:10px;border:1px solid #e2e8f0;border-radius:8px;background:#fff;cursor:grab}.palette-entry:hover{border-color:#c9d8f3;background:#f8fbff}.palette-entry:active{cursor:grabbing}.palette-entry:focus-visible{outline:2px solid #2f6fed;outline-offset:2px}.palette-entry b,.palette-entry small{grid-column:1/-1;white-space:normal;font-size:13px}.palette-entry small{font-size:12px}.palette-entry select{min-width:0;max-width:120px;cursor:default}.subflow-section{border-top:1px solid #e2e8f0}.subflow-section>p{font-size:13px;color:#748198}.palette-title h3{font-size:15px}.palette-title small,.hint{font-size:12px}
</style>
<style scoped>.palette-entry b,.palette-entry small{overflow-wrap:anywhere;overflow:visible;text-overflow:clip;white-space:normal}</style>
<style scoped>.palette-entry.compatibility-compatible{border-color:#9ed7bf;background:#f4fbf8}.palette-entry.compatibility-unready{border-color:#e5c983;background:#fffaf0;cursor:not-allowed}.palette-entry.compatibility-incompatible{opacity:.52;cursor:not-allowed}.compatibility-note{color:#5d718b!important;font-size:11px!important}.compatibility-compatible .compatibility-note{color:#167553!important}.compatibility-unready .compatibility-note{color:#8b6a16!important}</style>
