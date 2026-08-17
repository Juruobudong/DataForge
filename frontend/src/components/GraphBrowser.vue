<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background, BackgroundVariant } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { api } from '../api/platform'
import { graphUiState, layoutGraph, entityTypes, relationTypes, typeColor } from './graphBrowserModel'
import { graphModeLabel, literalDatatypeLabel } from '../constants/knowledgeLabels'

const props = defineProps({ libraryId: { type: String, required: true } })
const { fitView } = useVueFlow('knowledge-graph')
const query = ref(''), matches = ref([]), nodes = ref([]), edges = ref([])
const selected = ref(null), evidence = ref([]), summary = ref(null), graphData = ref(null)
const state = ref('loading'), error = ref(''), depth = ref(1)
const entityTypeFilter = ref(''), relationTypeFilter = ref(''), showLiteralNodes = ref(false)

const isSemantic = computed(() => summary.value?.graph_mode === 'semantic')
const stats = computed(() => summary.value?.stats || {})
const typeOptions = computed(() => entityTypes(summary.value?.nodes || []))
const relationOptions = computed(() => relationTypes(summary.value?.edges || []))

function refreshGraph() {
  if (!graphData.value) return
  const graph = graphData.value
  const baseNodes = (graph.nodes || []).map(item => ({ ...item }))
  const baseEdges = (graph.edges || []).map(item => ({ ...item }))
  if (showLiteralNodes.value && !isSemantic.value && graph.facts?.length) {
    for (const fact of graph.facts) {
      baseNodes.push({ id: fact.id, name: fact.object, type_code: null, type_label: '字面值', kind: 'literal', fact })
      baseEdges.push({ id: `fact-${fact.id}`, source: fact.subject_entity_id, target: fact.id, predicate: fact.predicate, relation_type: null, kind: 'literal' })
    }
  }
  const nodeFilter = entityTypeFilter.value
  const edgeFilter = relationTypeFilter.value
  const visibleNodes = nodeFilter ? baseNodes.filter(node => node.type_code === nodeFilter || node.type === nodeFilter) : baseNodes
  const nodeIds = new Set(visibleNodes.map(node => node.id))
  const visibleEdges = baseEdges.filter(edge => {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return false
    if (edgeFilter && edge.relation_type !== edgeFilter && edge.predicate !== edgeFilter) return false
    return true
  })
  const layout = layoutGraph({ nodes: visibleNodes, edges: visibleEdges })
  nodes.value = layout.nodes
  edges.value = layout.edges
  nextTick(() => fitView({ padding: .14, duration: 260 }))
}

function applyGraph(graph) {
  graphData.value = graph
  state.value = graphUiState(graph)
  refreshGraph()
}

async function loadOverview() {
  state.value = 'loading'; error.value = ''; selected.value = null; evidence.value = []
  entityTypeFilter.value = ''; relationTypeFilter.value = ''; showLiteralNodes.value = false
  try {
    summary.value = await api.graphOverview(props.libraryId)
    graphData.value = summary.value
    state.value = graphUiState(summary.value)
    if (state.value === 'ready') refreshGraph()
    else { nodes.value = []; edges.value = [] }
  } catch (err) {
    nodes.value = []; edges.value = []; graphData.value = null
    error.value = err.message; state.value = 'error'
  }
}

async function search() {
  try { matches.value = await api.graphEntities(props.libraryId, query.value); error.value = '' } catch (err) { error.value = err.message }
}

async function openEntity(entity, requestedDepth = depth.value) {
  try {
    const [detail, graph] = await Promise.all([
      api.graphEntity(props.libraryId, entity.id),
      api.graphNeighbors(props.libraryId, entity.id, requestedDepth),
    ])
    depth.value = requestedDepth; selected.value = { kind: 'entity', ...detail }; evidence.value = []
    applyGraph(graph); state.value = 'ready'; error.value = ''
  } catch (err) { error.value = err.message; state.value = 'error' }
}

async function nodeClick({ node }) {
  const meta = node.data?.meta || {}
  if (meta.kind === 'literal') { selected.value = { kind: 'literal', ...meta.fact }; evidence.value = []; return }
  try { selected.value = { kind: 'entity', ...await api.graphEntity(props.libraryId, node.id) }; evidence.value = []; error.value = '' } catch (err) { error.value = err.message }
}

async function edgeClick({ edge }) {
  try {
    const result = await api.graphEvidence(props.libraryId, edge.id)
    selected.value = { kind: 'relation', ...result.relation }; evidence.value = result.evidence; error.value = ''
  } catch (err) { error.value = err.message }
}

function autoLayout() { if (graphData.value?.nodes?.length) refreshGraph() }
function reset() { query.value = ''; matches.value = []; loadOverview() }
watch(() => props.libraryId, loadOverview, { immediate: true })
watch([entityTypeFilter, relationTypeFilter, showLiteralNodes], refreshGraph)
</script>

<template>
  <section class="graph-browser">
    <header class="graph-toolbar">
      <form @submit.prevent="search"><input v-model="query" placeholder="搜索实体名称、别名或描述"><button>搜索</button></form>
      <div class="actions">
        <button :class="{ active: depth === 1 }" @click="depth = 1">1 跳</button>
        <button :class="{ active: depth === 2 }" @click="depth = 2">2 跳</button>
        <button @click="autoLayout">自动布局</button>
        <button @click="fitView({ padding: .14, duration: 240 })">适配画布</button>
        <button @click="reset">重置</button>
      </div>
    </header>

    <div class="graph-stats">
      <span class="badge blue">{{ stats.entity_count ?? 0 }} 个实体</span>
      <span class="badge">{{ stats.relation_count ?? 0 }} 条关系</span>
      <span v-if="!isSemantic" class="badge amber">{{ stats.literal_fact_count ?? 0 }} 条字面值事实</span>
      <span class="badge green">{{ stats.entity_type_count ?? 0 }} 种实体类型</span>
      <span v-if="summary?.graph_mode" class="badge">{{ graphModeLabel(summary.graph_mode) }}</span>
    </div>

    <div class="graph-filters">
      <label>实体类型
        <select v-model="entityTypeFilter"><option value="">全部</option><option v-for="t in typeOptions" :key="t.key" :value="t.key">{{ t.label }}</option></select>
      </label>
      <label>关系类型
        <select v-model="relationTypeFilter"><option value="">全部</option><option v-for="r in relationOptions" :key="r.key" :value="r.key">{{ r.label }}</option></select>
      </label>
      <label v-if="!isSemantic" class="check"><input v-model="showLiteralNodes" type="checkbox"> 显示字面值节点</label>
    </div>

    <div class="graph-layout">
      <aside class="entity-results">
        <p v-if="matches.length" class="aside-title">搜索结果</p>
        <button v-for="entity in matches" :key="entity.id" @click="openEntity(entity)"><b>{{ entity.name }}</b><small>{{ entity.type }}</small></button>
        <p v-if="query && !matches.length" class="muted">没有匹配实体。</p>
        <template v-if="typeOptions.length">
          <p class="aside-title">图例</p>
          <div class="legend"><span v-for="t in typeOptions" :key="t.key" class="legend-item"><i :style="{ background: typeColor(t.key) }"></i>{{ t.label }}</span></div>
        </template>
      </aside>

      <div class="graph-canvas" :class="`state-${state}`">
        <VueFlow id="knowledge-graph" :nodes="nodes" :edges="edges" fit-view-on-init :nodes-draggable="true" :nodes-connectable="false" :elements-selectable="true" @node-click="nodeClick" @edge-click="edgeClick">
          <Background :variant="BackgroundVariant.Dots" :gap="18" :size="1.1" pattern-color="#cad3df" bg-color="#f7f9fc" />
          <Controls position="bottom-right" />
          <MiniMap position="bottom-left" :pannable="true" :zoomable="true" node-color="#dce8fa" mask-color="rgba(238,242,247,.72)" />
          <template #node-default="node">
            <div class="graph-node" :class="{ literal: node.data.meta?.kind === 'literal' }" :style="{ borderColor: node.data.color }" :title="node.data.label">
              <b>{{ node.data.label }}</b>
              <small>{{ node.data.meta?.kind === 'literal' ? '字面值' : node.data.meta?.type }}</small>
            </div>
          </template>
        </VueFlow>
        <div v-if="state === 'loading'" class="graph-state"><b>正在加载图谱…</b></div>
        <div v-else-if="state === 'empty'" class="graph-state"><b>当前知识库暂无可浏览关系</b><p>知识项、实体和关系均会在知识生产完成后显示。</p></div>
        <div v-else-if="state === 'error'" class="graph-state error-state"><b>图谱加载失败</b><p>{{ error }}</p><button class="primary" @click="loadOverview">重新加载</button></div>
      </div>

      <aside class="graph-inspector">
        <template v-if="selected?.kind === 'entity'">
          <p class="aside-title">实体详情</p>
          <h4>{{ selected.name }}</h4>
          <p>类型：{{ selected.type_label || selected.type }}</p>
          <p v-if="selected.description">{{ selected.description }}</p>
          <p v-if="selected.aliases?.length">别名：{{ selected.aliases.join('、') }}</p>
          <p>关系：{{ selected.relation_count }} 条 · Evidence：{{ selected.evidence_count ?? 0 }} 条</p>
          <template v-if="selected.facts?.length">
            <p class="aside-title">事实 / 属性</p>
            <article v-for="fact in selected.facts" :key="fact.id"><b>{{ fact.predicate }}</b><small>{{ fact.object }}</small></article>
          </template>
          <button class="primary" @click="openEntity(selected)">展开 {{ depth }} 跳邻居</button>
        </template>
        <template v-else-if="selected?.kind === 'relation'">
          <p class="aside-title">关系详情</p>
          <h4>{{ selected.relation_type_label || selected.predicate }}</h4>
          <p v-if="selected.description">{{ selected.description }}</p>
          <p v-if="selected.keywords?.length">关键词：{{ selected.keywords.join('、') }}</p>
          <p v-if="selected.weight != null">权重：{{ selected.weight }}</p>
          <details v-if="evidence.length" open><summary>Evidence（{{ evidence.length }}）</summary><article v-for="item in evidence" :key="item.id"><b>{{ item.source.original_filename || item.source.name }}</b><small>{{ item.anchor.label || item.anchor.file || '来源锚点' }}</small><p>{{ item.evidence_text }}</p></article></details>
          <p v-else class="muted">该关系暂无可显示的 Evidence。</p>
        </template>
        <template v-else-if="selected?.kind === 'literal'">
          <p class="aside-title">字面值详情</p>
          <h4>{{ selected.predicate }}</h4>
          <p>原始值：{{ selected.object }}</p>
          <p v-if="selected.literal_datatype">值类型：{{ literalDatatypeLabel(selected.literal_datatype) }}</p>
          <p v-if="selected.literal_unit">单位：{{ selected.literal_unit }}</p>
          <p v-if="selected.literal_normalized_value">标准化值：{{ JSON.stringify(selected.literal_normalized_value) }}</p>
        </template>
        <template v-else><p class="aside-title">图谱详情</p><p class="muted">选择节点查看实体，选择连线查看关系 Evidence。</p></template>
      </aside>
    </div>
    <p v-if="error && state !== 'error'" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.graph-browser { min-width: 0; }
.graph-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.graph-toolbar form { min-width: min(100%, 380px); flex: 1; margin: 0; }
.graph-toolbar form input { min-width: 180px; }
.graph-toolbar .actions { justify-content: flex-end; }
.graph-toolbar .actions button.active { border-color: var(--blue); color: var(--blue); background: var(--blue-soft); }
.graph-stats { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0 8px; }
.graph-stats .badge.amber { background: #fff4e0; color: #a16207; }
.graph-stats .badge.green { background: #e7f8f0; color: #0e8a63; }
.graph-filters { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; margin-bottom: 12px; }
.graph-filters label { display: flex; align-items: center; gap: 6px; color: #536177; font-size: 13px; }
.graph-filters select { min-width: 120px; }
.graph-filters .check { cursor: pointer; }
.graph-layout { display: grid; grid-template-columns: minmax(190px, .65fr) minmax(0, 2.4fr) minmax(250px, .9fr); gap: 16px; align-items: stretch; }
.entity-results, .graph-inspector { display: grid; min-width: 0; align-content: start; gap: 8px; padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); box-shadow: var(--shadow); }
.entity-results button { width: 100%; min-height: 0; padding: 10px; text-align: left; }
.entity-results b, .entity-results small { display: block; }
.entity-results small, .muted { color: var(--muted); font-size: 13px; }
.aside-title { margin: 0; color: var(--muted); font-size: 13px; font-weight: 800; }
.legend { display: grid; gap: 5px; }
.legend-item { display: flex; align-items: center; gap: 6px; color: #536177; font-size: 12px; }
.legend-item i { width: 10px; height: 10px; border-radius: 3px; }
.graph-inspector h4 { margin: 0; font-size: 16px; }
.graph-inspector p { margin: 0; line-height: 1.6; }
.graph-inspector details { margin-top: 4px; }
.graph-inspector summary { cursor: pointer; font-weight: 800; }
.graph-inspector article { padding: 10px 0; border-top: 1px solid var(--border); }
.graph-inspector article b, .graph-inspector article small { display: block; }
.graph-inspector article small { margin-top: 4px; color: var(--muted); }
.graph-inspector article p { margin: 6px 0 0; }
.graph-canvas { position: relative; min-width: 0; min-height: 560px; height: calc(100vh - 330px); overflow: hidden; border: 1px solid #dbe3ef; border-radius: var(--radius); background: #f7f9fc; }
.graph-canvas :deep(.vue-flow) { height: 100%; }
.graph-canvas :deep(.vue-flow__node) { border: 0; background: transparent; padding: 0; }
.graph-canvas :deep(.vue-flow__controls), .graph-canvas :deep(.vue-flow__minimap) { overflow: hidden; border: 1px solid #dbe3ef; border-radius: 9px; background: #fff; box-shadow: 0 5px 18px rgba(30, 51, 82, .12); }
.graph-canvas :deep(.vue-flow__controls-button) { min-height: 30px; }
.graph-node { min-width: 130px; max-width: 240px; padding: 10px 12px; border: 1px solid #c9dafa; border-left-width: 4px; border-radius: 10px; color: #24364f; background: #fff; box-shadow: 0 5px 16px rgba(30, 51, 82, .1); }
.graph-node.literal { border-color: #cbd5e1; border-style: dashed; background: #f8fafc; }
.graph-node b, .graph-node small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.graph-node b { font-size: 13px; }
.graph-node small { margin-top: 3px; color: var(--muted); font-size: 12px; }
.graph-state { position: absolute; inset: 0; z-index: 5; display: grid; place-content: center; padding: 28px; color: #536177; background: rgba(247, 249, 252, .9); text-align: center; }
.graph-state b { font-size: 16px; }
.graph-state p { max-width: 300px; margin: 8px 0 0; color: var(--muted); line-height: 1.6; }
.error-state { color: var(--red); }
.error-state button { margin-top: 12px; }
.error-state p { color: inherit; }
@media (max-width: 1440px) { .graph-layout { grid-template-columns: 210px minmax(0, 1fr); } .graph-inspector { grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 900px) { .graph-toolbar { display: grid; } .graph-toolbar .actions { justify-content: flex-start; } .graph-layout { grid-template-columns: 1fr; } .graph-canvas { order: -1; min-height: 480px; height: 65vh; } .graph-inspector { grid-column: auto; grid-template-columns: 1fr; } }
</style>
