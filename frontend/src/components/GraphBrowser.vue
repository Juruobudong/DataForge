<script setup>
import { nextTick, ref, watch } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background, BackgroundVariant } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { api } from '../api/platform'
import { graphUiState, layoutGraph } from './graphBrowserModel'

const props = defineProps({ libraryId: { type: String, required: true } })
const { fitView } = useVueFlow('knowledge-graph')
const query = ref(''), matches = ref([]), nodes = ref([]), edges = ref([])
const selected = ref(null), evidence = ref([]), summary = ref(null), graphData = ref(null)
const state = ref('loading'), error = ref(''), depth = ref(1)

function applyGraph(graph) {
  graphData.value = graph
  const result = layoutGraph(graph)
  nodes.value = result.nodes
  edges.value = result.edges
  nextTick(() => fitView({ padding: .14, duration: 260 }))
}

async function loadOverview() {
  state.value = 'loading'; error.value = ''; selected.value = null; evidence.value = []
  try {
    summary.value = await api.graphOverview(props.libraryId)
    state.value = graphUiState(summary.value)
    if (state.value === 'ready') applyGraph(summary.value)
    else { nodes.value = []; edges.value = []; graphData.value = summary.value }
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
  try { selected.value = { kind: 'entity', ...await api.graphEntity(props.libraryId, node.id) }; evidence.value = []; error.value = '' } catch (err) { error.value = err.message }
}

async function edgeClick({ edge }) {
  try {
    const result = await api.graphEvidence(props.libraryId, edge.id)
    selected.value = { kind: 'relation', ...result.relation }; evidence.value = result.evidence; error.value = ''
  } catch (err) { error.value = err.message }
}

function autoLayout() { if (graphData.value?.nodes?.length) applyGraph(graphData.value) }
function reset() { query.value = ''; matches.value = []; loadOverview() }
watch(() => props.libraryId, loadOverview, { immediate: true })
</script>

<template>
  <section class="graph-browser">
    <header class="graph-toolbar">
      <form @submit.prevent="search"><input v-model="query" placeholder="搜索实体，例如：高血压"><button>搜索</button></form>
      <div class="actions"><button :class="{ active: depth === 1 }" @click="depth = 1">1 跳</button><button :class="{ active: depth === 2 }" @click="depth = 2">2 跳</button><button @click="autoLayout">自动布局</button><button @click="fitView({ padding: .14, duration: 240 })">适配画布</button><button @click="reset">重置</button></div>
    </header>
    <div class="graph-stats"><span class="badge blue">{{ summary?.entity_count ?? 0 }} 个实体</span><span class="badge">{{ summary?.relation_count ?? 0 }} 条关系</span><span v-if="summary?.graph_mode" class="badge">{{ summary.graph_mode === 'semantic' ? '语义图谱' : '三元组图谱' }}</span></div>
    <div class="graph-layout">
      <aside class="entity-results"><p v-if="matches.length" class="aside-title">搜索结果</p><button v-for="entity in matches" :key="entity.id" @click="openEntity(entity)"><b>{{ entity.name }}</b><small>{{ entity.type }}</small></button><p v-if="query && !matches.length" class="muted">没有匹配实体。</p></aside>
      <div class="graph-canvas" :class="`state-${state}`">
        <VueFlow id="knowledge-graph" :nodes="nodes" :edges="edges" fit-view-on-init :nodes-draggable="true" :nodes-connectable="false" :elements-selectable="true" @node-click="nodeClick" @edge-click="edgeClick">
          <Background :variant="BackgroundVariant.Dots" :gap="18" :size="1.1" pattern-color="#cad3df" bg-color="#f7f9fc" />
          <Controls position="bottom-right" />
          <MiniMap position="bottom-left" :pannable="true" :zoomable="true" node-color="#dce8fa" mask-color="rgba(238,242,247,.72)" />
          <template #node-default="node"><div class="graph-node"><b>{{ node.data.label }}</b><small>{{ node.data.meta.type }}</small></div></template>
        </VueFlow>
        <div v-if="state === 'loading'" class="graph-state"><b>正在加载图谱…</b></div>
        <div v-else-if="state === 'empty'" class="graph-state"><b>当前知识库暂无可浏览关系</b><p>知识项、实体和关系均会在知识生产完成后显示。</p></div>
        <div v-else-if="state === 'error'" class="graph-state error-state"><b>图谱加载失败</b><p>{{ error }}</p><button class="primary" @click="loadOverview">重新加载</button></div>
      </div>
      <aside class="graph-inspector">
        <template v-if="selected?.kind === 'entity'"><p class="aside-title">实体详情</p><h4>{{ selected.name }}</h4><p>类型：{{ selected.type }}</p><p v-if="selected.description">{{ selected.description }}</p><p>关联：{{ selected.relation_count }}</p><button class="primary" @click="openEntity(selected)">展开 {{ depth }} 跳邻居</button></template>
        <template v-else-if="selected?.kind === 'relation'"><p class="aside-title">关系详情</p><h4>{{ selected.predicate }}</h4><p v-if="selected.description">{{ selected.description }}</p><p v-if="selected.keywords?.length">关键词：{{ selected.keywords.join('、') }}</p><details v-if="evidence.length" open><summary>Evidence（{{ evidence.length }}）</summary><article v-for="item in evidence" :key="item.id"><b>{{ item.source.original_filename || item.source.name }}</b><small>{{ item.anchor.label || item.anchor.file || '来源锚点' }}</small><p>{{ item.evidence_text }}</p></article></details><p v-else class="muted">该关系暂无可显示的 Evidence。</p></template>
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
.graph-stats { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; }
.graph-layout { display: grid; grid-template-columns: minmax(190px, .65fr) minmax(0, 2.4fr) minmax(250px, .9fr); gap: 16px; align-items: stretch; }
.entity-results, .graph-inspector { display: grid; min-width: 0; align-content: start; gap: 8px; padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); box-shadow: var(--shadow); }
.entity-results button { width: 100%; min-height: 0; padding: 10px; text-align: left; }
.entity-results b, .entity-results small { display: block; }
.entity-results small, .muted { color: var(--muted); font-size: 13px; }
.aside-title { margin: 0; color: var(--muted); font-size: 13px; font-weight: 800; }
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
.graph-node { min-width: 190px; padding: 10px 12px; border: 1px solid #c9dafa; border-radius: 10px; color: #24364f; background: #fff; box-shadow: 0 5px 16px rgba(30, 51, 82, .1); }
.graph-node b, .graph-node small { display: block; }
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
