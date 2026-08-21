<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background, BackgroundVariant } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { api } from '../api/platform'
import { entityTypes, graphUiState, layoutGraph, pushGraphViewSnapshot, relationTypes, takeGraphViewSnapshot, typeColor } from './graphBrowserModel'
import { graphModeLabel, literalDatatypeLabel } from '../constants/knowledgeLabels'

const props = defineProps({ libraryId: { type: String, required: true } })
const { fitView, getViewport, setViewport } = useVueFlow('knowledge-graph')
const query = ref(''), matches = ref([]), nodes = ref([]), edges = ref([])
const selected = ref(null), evidence = ref([]), summary = ref(null), graphData = ref(null)
const viewMode = ref('overview'), overviewSnapshot = ref(null), neighborhoodTrail = ref([]), currentNeighborhood = ref(null)
const previews = ref({}), state = ref('loading'), error = ref(''), expanding = ref(false)
const entityTypeFilter = ref(''), relationTypeFilter = ref(''), showLiteralNodes = ref(false)
const menuOpen = ref(false), menuOpenSource = ref(null), menuRoot = ref(null), coarsePointer = ref(false)
const largeDialog = ref(null), largePreview = ref(null), largeLoading = ref(false)
const largeEntityTypes = ref([]), largeRelationTypes = ref([])
let pointerMedia, restoringView = false

const isSemantic = computed(() => summary.value?.graph_mode === 'semantic')
const typeOptions = computed(() => entityTypes(graphData.value?.nodes || []))
const relationOptions = computed(() => relationTypes(graphData.value?.edges || []))
const displayStats = computed(() => viewMode.value === 'overview' ? (summary.value?.stats || {}) : {
  entity_count: graphData.value?.nodes?.length || 0,
  relation_count: graphData.value?.edges?.length || 0,
  literal_fact_count: graphData.value?.facts?.length || 0,
  entity_type_count: typeOptions.value.length,
})
const selectedIsNeighborhoodCenter = computed(() => (
  viewMode.value === 'neighborhood' && selected.value?.kind === 'entity' &&
  selected.value.id === currentNeighborhood.value?.center.id
))
const expansionLabel = computed(() => {
  if (selected.value?.kind !== 'entity') return '请选择实体节点'
  if (!selectedIsNeighborhoodCenter.value) return viewMode.value === 'overview' ? '展开邻居' : '查看该实体邻域'
  return currentNeighborhood.value.depth === 1 ? '继续查看 2 跳' : '已查看 2 跳邻域'
})

function filteredGraph() {
  if (!graphData.value) return null
  const baseNodes = (graphData.value.nodes || []).map(item => ({ ...item }))
  const baseEdges = (graphData.value.edges || []).map(item => ({ ...item }))
  if (showLiteralNodes.value && !isSemantic.value && graphData.value.facts?.length) {
    for (const fact of graphData.value.facts) {
      baseNodes.push({ id: fact.id, name: fact.object, type_code: null, type_label: '字面值', kind: 'literal', fact })
      baseEdges.push({ id: `fact-${fact.id}`, source: fact.subject_entity_id, target: fact.id, predicate: fact.predicate, relation_type: null, kind: 'literal' })
    }
  }
  const visibleNodes = entityTypeFilter.value
    ? baseNodes.filter(node => node.type_code === entityTypeFilter.value || node.type === entityTypeFilter.value)
    : baseNodes
  const nodeIds = new Set(visibleNodes.map(node => node.id))
  const visibleEdges = baseEdges.filter(edge => {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return false
    return !relationTypeFilter.value || edge.relation_type === relationTypeFilter.value || edge.predicate === relationTypeFilter.value
  })
  return { nodes: visibleNodes, edges: visibleEdges }
}

function refreshGraph({ relayout = false, fit = false } = {}) {
  const graph = filteredGraph()
  if (!graph) return
  const previousPositions = new Map(nodes.value.map(node => [node.id, { ...node.position }]))
  const layout = layoutGraph(graph)
  nodes.value = layout.nodes.map(node => ({
    ...node,
    position: !relayout && previousPositions.has(node.id) ? previousPositions.get(node.id) : node.position,
    data: {
      ...node.data,
      isCenter: viewMode.value === 'neighborhood' && node.id === currentNeighborhood.value?.center.id,
    },
  }))
  edges.value = layout.edges
  if (fit) nextTick(() => fitView({ padding: .14, duration: 260 }))
}

function cloneNodes(values) {
  return values.map(node => ({ ...node, position: { ...node.position }, data: { ...node.data } }))
}

function cloneEdges(values) {
  return values.map(edge => ({ ...edge, data: edge.data ? { ...edge.data } : edge.data }))
}

function captureViewSnapshot() {
  return {
    mode: viewMode.value,
    graphData: graphData.value,
    nodes: cloneNodes(nodes.value),
    edges: cloneEdges(edges.value),
    selected: selected.value ? { ...selected.value } : null,
    evidence: [...evidence.value],
    previews: { ...previews.value },
    filters: {
      entityType: entityTypeFilter.value,
      relationType: relationTypeFilter.value,
      showLiteralNodes: showLiteralNodes.value,
    },
    query: query.value,
    matches: [...matches.value],
    state: state.value,
    viewport: { ...getViewport() },
    neighborhood: currentNeighborhood.value ? {
      ...currentNeighborhood.value,
      center: { ...currentNeighborhood.value.center },
      filters: {
        entityTypes: [...(currentNeighborhood.value.filters?.entityTypes || [])],
        relationTypes: [...(currentNeighborhood.value.filters?.relationTypes || [])],
      },
    } : null,
  }
}

function restoreViewSnapshot(snapshot) {
  if (!snapshot) return
  restoringView = true
  viewMode.value = snapshot.mode
  graphData.value = snapshot.graphData
  nodes.value = cloneNodes(snapshot.nodes)
  edges.value = cloneEdges(snapshot.edges)
  selected.value = snapshot.selected ? { ...snapshot.selected } : null
  evidence.value = [...snapshot.evidence]
  previews.value = { ...snapshot.previews }
  entityTypeFilter.value = snapshot.filters.entityType
  relationTypeFilter.value = snapshot.filters.relationType
  showLiteralNodes.value = snapshot.filters.showLiteralNodes
  query.value = snapshot.query
  matches.value = [...snapshot.matches]
  state.value = snapshot.state
  currentNeighborhood.value = snapshot.neighborhood
  closeNeighborMenu()
  error.value = ''
  nextTick(async () => {
    try { await setViewport(snapshot.viewport) }
    finally { restoringView = false }
  })
}

async function loadOverview() {
  state.value = 'loading'; error.value = ''; selected.value = null; evidence.value = []
  query.value = ''; matches.value = []; previews.value = {}; closeNeighborMenu()
  entityTypeFilter.value = ''; relationTypeFilter.value = ''; showLiteralNodes.value = false
  viewMode.value = 'overview'; overviewSnapshot.value = null; neighborhoodTrail.value = []; currentNeighborhood.value = null
  try {
    summary.value = await api.graphOverview(props.libraryId)
    graphData.value = summary.value
    state.value = graphUiState(summary.value)
    if (state.value === 'ready') refreshGraph({ relayout: true, fit: true })
    else { nodes.value = []; edges.value = [] }
  } catch (err) {
    nodes.value = []; edges.value = []; graphData.value = null
    error.value = err.message; state.value = 'error'
  }
}

async function search() {
  try { matches.value = await api.graphEntities(props.libraryId, query.value); error.value = '' } catch (err) { error.value = err.message }
}

async function loadEntityPreviews(entityId) {
  try {
    const [one, two] = await Promise.all([
      api.graphNeighborPreview(props.libraryId, entityId, 1),
      api.graphNeighborPreview(props.libraryId, entityId, 2),
    ])
    if (selected.value?.kind === 'entity' && selected.value.id === entityId) previews.value = { 1: one, 2: two }
  } catch (err) { error.value = err.message }
}

async function selectEntity(entity) {
  try {
    const detail = await api.graphEntity(props.libraryId, entity.id)
    selected.value = { kind: 'entity', ...detail }; evidence.value = []; previews.value = {}; error.value = ''
    loadEntityPreviews(entity.id)
  } catch (err) { error.value = err.message }
}

async function openNeighborhood(entity, requestedDepth, { filters = {}, confirmLarge = false } = {}) {
  if (entity?.kind !== 'entity' || expanding.value) return
  const target = { ...entity }
  expanding.value = true; error.value = ''
  try {
    const preview = await api.graphNeighborPreview(props.libraryId, target.id, requestedDepth, filters)
    if (preview.confirmation_required && !confirmLarge) {
      openLargeDialog(target, requestedDepth, preview)
      return
    }
    const graph = await api.graphNeighbors(props.libraryId, target.id, requestedDepth, filters, confirmLarge)
    const sameCenter = viewMode.value === 'neighborhood' && currentNeighborhood.value?.center.id === target.id
    const sourceSnapshot = captureViewSnapshot()
    if (viewMode.value === 'overview') {
      overviewSnapshot.value = sourceSnapshot
      neighborhoodTrail.value = []
    } else if (!sameCenter) {
      neighborhoodTrail.value = pushGraphViewSnapshot(neighborhoodTrail.value, sourceSnapshot)
    }
    viewMode.value = 'neighborhood'
    currentNeighborhood.value = {
      center: { id: target.id, name: target.name, type: target.type, type_label: target.type_label },
      depth: requestedDepth,
      graph,
      filters: {
        entityTypes: [...(filters.entityTypes || [])],
        relationTypes: [...(filters.relationTypes || [])],
      },
    }
    graphData.value = graph
    selected.value = target
    evidence.value = []
    entityTypeFilter.value = ''; relationTypeFilter.value = ''; showLiteralNodes.value = false
    if (!sameCenter) { query.value = ''; matches.value = [] }
    state.value = graph.nodes?.length ? 'ready' : 'empty'
    refreshGraph({ relayout: true, fit: true })
    closeNeighborMenu(); largeDialog.value = null
  } catch (err) {
    error.value = err.message
  } finally {
    expanding.value = false
  }
}

async function expandSelected(requestedDepth, options = {}) {
  if (selected.value?.kind !== 'entity') return
  await openNeighborhood({ ...selected.value }, requestedDepth, options)
}

async function nodeClick({ node }) {
  const meta = node.data?.meta || {}
  if (meta.kind === 'literal') { selected.value = { kind: 'literal', ...meta.fact }; evidence.value = []; return }
  await selectEntity({ id: node.id })
}

async function edgeClick({ edge }) {
  try {
    const result = await api.graphEvidence(props.libraryId, edge.id)
    selected.value = { kind: 'relation', ...result.relation }; evidence.value = result.evidence; error.value = ''
  } catch (err) { error.value = err.message }
}

function autoLayout() { if (graphData.value?.nodes?.length) refreshGraph({ relayout: true, fit: true }) }
function locateSelected() {
  const node = nodes.value.find(item => item.id === selected.value?.id)
  if (node) fitView({ nodes: [node.id], padding: .8, maxZoom: 1.4, duration: 240 })
}
function returnToOverview() {
  if (!overviewSnapshot.value) return
  const snapshot = overviewSnapshot.value
  overviewSnapshot.value = null; neighborhoodTrail.value = []
  restoreViewSnapshot(snapshot)
}
function restoreTrailSnapshot(index) {
  const result = takeGraphViewSnapshot(neighborhoodTrail.value, index)
  if (!result.snapshot) return
  neighborhoodTrail.value = result.history
  restoreViewSnapshot(result.snapshot)
}
function returnToPrevious() { restoreTrailSnapshot(neighborhoodTrail.value.length - 1) }
async function reloadCurrentNeighborhood() {
  const current = currentNeighborhood.value
  if (!current) return
  try {
    const detail = await api.graphEntity(props.libraryId, current.center.id)
    selected.value = { kind: 'entity', ...detail }; previews.value = {}; evidence.value = []
    loadEntityPreviews(current.center.id)
    await openNeighborhood(selected.value, current.depth, { filters: current.filters, confirmLarge: true })
  } catch (err) { error.value = err.message }
}
function openLargeDialog(entity, depth, preview) {
  largeDialog.value = { entity: { ...entity }, depth, facets: preview }
  largePreview.value = preview; largeEntityTypes.value = []; largeRelationTypes.value = []
  closeNeighborMenu()
}
async function recalculateLarge() {
  if (!largeDialog.value) return
  largeLoading.value = true
  try {
    largePreview.value = await api.graphNeighborPreview(props.libraryId, largeDialog.value.entity.id, largeDialog.value.depth, {
      entityTypes: largeEntityTypes.value, relationTypes: largeRelationTypes.value,
    })
    error.value = ''
  } catch (err) { error.value = err.message }
  finally { largeLoading.value = false }
}
async function confirmLargeExpansion() {
  await openNeighborhood(largeDialog.value.entity, largeDialog.value.depth, {
    filters: { entityTypes: largeEntityTypes.value, relationTypes: largeRelationTypes.value },
    confirmLarge: true,
  })
}
function closeNeighborMenu() { menuOpen.value = false; menuOpenSource.value = null }
function openClickedMenu() { menuOpen.value = true; menuOpenSource.value = 'click' }
function toggleMenu() { openClickedMenu() }
function openDesktopMenu() {
  if (!coarsePointer.value && selected.value?.kind === 'entity' && !menuOpen.value) {
    menuOpen.value = true; menuOpenSource.value = 'hover'
  }
}
function openFocusMenu() {
  if (!coarsePointer.value && selected.value?.kind === 'entity') {
    menuOpen.value = true
    if (menuOpenSource.value !== 'click') menuOpenSource.value = 'focus'
  }
}
function closeDesktopMenu() {
  if (!coarsePointer.value && menuOpenSource.value === 'hover') closeNeighborMenu()
}
function onMenuFocusOut(event) { if (!menuRoot.value?.contains(event.relatedTarget)) closeNeighborMenu() }
function primaryExpansionAction() {
  if (coarsePointer.value) { openClickedMenu(); return }
  if (!selectedIsNeighborhoodCenter.value) expandSelected(1)
  else if (currentNeighborhood.value.depth === 1) expandSelected(2)
  else menuOpen.value = true
}
function previewCount(depth) {
  const value = previews.value[depth]
  return value ? `预计 ${value.neighbor_count.toLocaleString()} 个节点` : '正在估算…'
}
function reset() {
  query.value = ''; matches.value = []
  if (viewMode.value === 'overview') loadOverview()
  else reloadCurrentNeighborhood()
}
watch(() => props.libraryId, loadOverview, { immediate: true })
watch([entityTypeFilter, relationTypeFilter, showLiteralNodes], () => { if (!restoringView) refreshGraph() })
onMounted(() => {
  pointerMedia = window.matchMedia('(hover: none), (pointer: coarse)')
  const updatePointer = () => { coarsePointer.value = pointerMedia.matches }
  updatePointer(); pointerMedia.addEventListener?.('change', updatePointer)
  pointerMedia._update = updatePointer
})
onBeforeUnmount(() => {
  if (pointerMedia?._update) pointerMedia.removeEventListener?.('change', pointerMedia._update)
})
</script>

<template>
  <section class="graph-browser">
    <section v-if="viewMode === 'neighborhood'" class="neighborhood-context">
      <nav class="graph-breadcrumbs" aria-label="邻域浏览路径">
        <button @click="returnToOverview">图谱总览</button>
        <template v-for="(snapshot, index) in neighborhoodTrail" :key="`${snapshot.neighborhood.center.id}-${index}`">
          <span>›</span><button @click="restoreTrailSnapshot(index)">{{ snapshot.neighborhood.center.name }}</button>
        </template>
        <span>›</span><b>{{ currentNeighborhood.center.name }}</b>
      </nav>
      <div class="neighborhood-heading">
        <div><h3>{{ currentNeighborhood.center.name }} · {{ currentNeighborhood.depth }} 跳邻域</h3><p>独立邻域画布，不会修改图谱总览。</p></div>
        <div class="actions">
          <button v-if="neighborhoodTrail.length" @click="returnToPrevious">返回上一层</button>
          <button @click="returnToOverview">返回图谱总览</button>
        </div>
      </div>
    </section>

    <header class="graph-toolbar">
      <form @submit.prevent="search"><input v-model="query" placeholder="搜索实体名称、别名或描述"><button>搜索</button></form>
      <div class="actions">
        <button @click="autoLayout">自动布局</button>
        <button @click="fitView({ padding: .14, duration: 240 })">适配画布</button>
        <button @click="reset">{{ viewMode === 'overview' ? '重置' : '重置当前邻域' }}</button>
      </div>
    </header>

    <div class="graph-stats">
      <span class="badge blue">{{ displayStats.entity_count ?? 0 }} 个实体</span>
      <span class="badge">{{ displayStats.relation_count ?? 0 }} 条关系</span>
      <span v-if="!isSemantic" class="badge amber">{{ displayStats.literal_fact_count ?? 0 }} 条字面值事实</span>
      <span class="badge green">{{ displayStats.entity_type_count ?? 0 }} 种实体类型</span>
      <span v-if="currentNeighborhood" class="badge blue">{{ currentNeighborhood.depth }} 跳邻域</span>
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
        <button v-for="entity in matches" :key="entity.id" @click="selectEntity(entity)"><b>{{ entity.name }}</b><small>{{ entity.type }}</small></button>
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
            <div class="graph-node" :class="{ literal: node.data.meta?.kind === 'literal', center: node.data.isCenter }" :style="{ borderColor: node.data.color }" :title="node.data.label">
              <b>{{ node.data.label }}</b>
              <small>{{ node.data.meta?.kind === 'literal' ? '字面值' : node.data.meta?.type }}</small>
            </div>
          </template>
        </VueFlow>
        <div v-if="state === 'loading'" class="graph-state"><b>正在加载图谱…</b></div>
        <div v-else-if="state === 'empty'" class="graph-state"><b>{{ viewMode === 'overview' ? '当前知识库暂无可浏览关系' : '当前筛选下暂无邻域关系' }}</b><p>{{ viewMode === 'overview' ? '知识项、实体和关系均会在知识生产完成后显示。' : '可返回上一层或图谱总览继续浏览。' }}</p></div>
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
          <div
            ref="menuRoot"
            class="neighbor-action"
            @mouseenter="openDesktopMenu"
            @mouseleave="closeDesktopMenu"
            @focusin="openFocusMenu"
            @focusout="onMenuFocusOut"
            @keydown.esc="closeNeighborMenu"
          >
            <div class="split-primary">
              <button class="primary action-body" :disabled="expanding" @click="primaryExpansionAction">
                {{ expanding ? '正在展开…' : expansionLabel }}
              </button>
              <button class="primary action-arrow" :aria-expanded="menuOpen" aria-haspopup="menu" aria-label="选择邻居展开方式" :disabled="expanding" @click="toggleMenu">▾</button>
            </div>
            <div v-if="menuOpen" class="neighbor-menu" role="menu">
              <template v-if="!selectedIsNeighborhoodCenter">
                <button role="menuitem" @click="expandSelected(1)"><b>{{ viewMode === 'overview' ? '展开' : '查看' }} 1 跳邻域</b><small>直接关联实体 · {{ previewCount(1) }}</small></button>
                <button role="menuitem" @click="expandSelected(2)"><b>{{ viewMode === 'overview' ? '展开' : '查看' }} 2 跳邻域</b><small>包含间接关联 · {{ previewCount(2) }}</small></button>
              </template>
              <template v-else-if="currentNeighborhood.depth === 1">
                <button role="menuitem" @click="expandSelected(1, { filters: currentNeighborhood.filters, confirmLarge: true })"><b>重新加载 1 跳</b><small>刷新当前独立邻域</small></button>
                <button role="menuitem" @click="expandSelected(2, { filters: currentNeighborhood.filters })"><b>切换到 2 跳</b><small>替换当前邻域 · {{ previewCount(2) }}</small></button>
                <button role="menuitem" @click="returnToOverview"><b>返回图谱总览</b><small>恢复原画布位置与筛选</small></button>
              </template>
              <template v-else>
                <button role="menuitem" @click="expandSelected(2, { filters: currentNeighborhood.filters, confirmLarge: true })"><b>重新加载 2 跳</b><small>刷新当前独立邻域</small></button>
                <button role="menuitem" @click="expandSelected(1, { filters: currentNeighborhood.filters })"><b>切换到 1 跳</b><small>替换为直接关联实体</small></button>
                <button role="menuitem" @click="returnToOverview"><b>返回图谱总览</b><small>恢复原画布位置与筛选</small></button>
              </template>
            </div>
          </div>
          <button class="secondary-action" @click="locateSelected">定位节点</button>
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
        <template v-else>
          <p class="aside-title">图谱详情</p><p class="muted">选择节点查看实体，选择连线查看关系 Evidence。</p>
          <div class="split-primary disabled-action"><button class="primary" disabled>请选择实体节点</button><button class="primary" disabled>▾</button></div>
        </template>
        <button v-if="viewMode === 'neighborhood'" class="secondary-action" @click="returnToOverview">返回图谱总览</button>
      </aside>
    </div>
    <p v-if="error && state !== 'error'" class="error">{{ error }}</p>

    <button v-if="menuOpen && coarsePointer" class="menu-backdrop" aria-label="关闭邻居菜单" @click="closeNeighborMenu"></button>
    <div v-if="largeDialog" class="dialog-backdrop" role="presentation" @click.self="largeDialog = null">
      <section class="large-neighbor-dialog" role="dialog" aria-modal="true" aria-labelledby="large-neighbor-title">
        <h3 id="large-neighbor-title">确认展开 {{ largeDialog.depth }} 跳邻居</h3>
        <p><b>{{ largeDialog.entity.name }}</b> 预计包含 <b>{{ (largePreview?.neighbor_count ?? 0).toLocaleString() }}</b> 个邻居、{{ (largePreview?.edge_count ?? 0).toLocaleString() }} 条关系。</p>
        <p v-if="largePreview?.confirmation_required" class="warning-copy">该规模可能造成画布卡顿。可先按实体类型或关系类型缩小范围，也可明确确认后继续。</p>
        <div class="facet-columns">
          <fieldset>
            <legend>实体类型</legend>
            <label v-for="facet in largeDialog.facets.entity_type_facets" :key="facet.key"><input v-model="largeEntityTypes" type="checkbox" :value="facet.key"> {{ facet.label }} <small>{{ facet.count }}</small></label>
          </fieldset>
          <fieldset>
            <legend>关系类型</legend>
            <label v-for="facet in largeDialog.facets.relation_type_facets" :key="facet.key"><input v-model="largeRelationTypes" type="checkbox" :value="facet.key"> {{ facet.label }} <small>{{ facet.count }}</small></label>
          </fieldset>
        </div>
        <div class="dialog-actions">
          <button @click="largeDialog = null">取消</button>
          <button :disabled="largeLoading" @click="recalculateLarge">{{ largeLoading ? '正在估算…' : '更新预计数量' }}</button>
          <button class="primary" :disabled="largeLoading || expanding" @click="confirmLargeExpansion">{{ largePreview?.confirmation_required ? '确认并展开' : '展开筛选结果' }}</button>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.graph-browser { min-width: 0; }
.neighborhood-context { margin-bottom: 14px; padding: 14px 16px; border: 1px solid #c9dafa; border-radius: var(--radius); background: #f5f9ff; }
.graph-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; color: var(--muted); font-size: 13px; }
.graph-breadcrumbs button { min-height: 0; padding: 0; border: 0; color: var(--blue); background: transparent; }
.graph-breadcrumbs b { color: #24364f; }
.neighborhood-heading { display: flex; align-items: end; justify-content: space-between; gap: 14px; margin-top: 10px; }
.neighborhood-heading h3 { margin: 0; font-size: 18px; }
.neighborhood-heading p { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
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
.neighbor-action { position: relative; margin-top: 6px; }
.split-primary { display: grid; grid-template-columns: minmax(0, 1fr) 42px; width: 100%; }
.split-primary .primary { border-color: var(--blue); border-radius: 0; }
.split-primary .action-body, .disabled-action .primary:first-child { border-radius: 9px 0 0 9px; }
.split-primary .action-arrow, .disabled-action .primary:last-child { border-left-color: rgba(255, 255, 255, .35); border-radius: 0 9px 9px 0; }
.split-primary .action-body { justify-content: flex-start; text-align: left; }
.disabled-action { margin-top: 6px; }
.neighbor-menu { position: absolute; z-index: 20; top: calc(100% + 6px); right: 0; left: 0; overflow: hidden; border: 1px solid var(--border); border-radius: 10px; background: #fff; box-shadow: 0 14px 34px rgba(30, 51, 82, .18); }
.neighbor-menu button { display: block; width: 100%; min-height: 0; padding: 12px; border: 0; border-radius: 0; text-align: left; }
.neighbor-menu button + button { border-top: 1px solid var(--border); }
.neighbor-menu button:hover, .neighbor-menu button:focus-visible { background: var(--blue-soft); }
.neighbor-menu b, .neighbor-menu small { display: block; }
.neighbor-menu small { margin-top: 3px; color: var(--muted); font-size: 12px; font-weight: 500; }
.secondary-action { width: 100%; }
.graph-canvas { position: relative; min-width: 0; min-height: 560px; height: calc(100vh - 330px); overflow: hidden; border: 1px solid #dbe3ef; border-radius: var(--radius); background: #f7f9fc; }
.graph-canvas :deep(.vue-flow) { height: 100%; }
.graph-canvas :deep(.vue-flow__node) { border: 0; background: transparent; padding: 0; }
.graph-canvas :deep(.vue-flow__controls), .graph-canvas :deep(.vue-flow__minimap) { overflow: hidden; border: 1px solid #dbe3ef; border-radius: 9px; background: #fff; box-shadow: 0 5px 18px rgba(30, 51, 82, .12); }
.graph-canvas :deep(.vue-flow__controls-button) { min-height: 30px; }
.graph-node { min-width: 130px; max-width: 240px; padding: 10px 12px; border: 1px solid #c9dafa; border-left-width: 4px; border-radius: 10px; color: #24364f; background: #fff; box-shadow: 0 5px 16px rgba(30, 51, 82, .1); }
.graph-node.literal { border-color: #cbd5e1; border-style: dashed; background: #f8fafc; }
.graph-node.center { outline: 3px solid rgba(59, 130, 246, .2); box-shadow: 0 8px 22px rgba(30, 94, 190, .2); }
.graph-node b, .graph-node small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.graph-node b { font-size: 13px; }
.graph-node small { margin-top: 3px; color: var(--muted); font-size: 12px; }
.graph-state { position: absolute; inset: 0; z-index: 5; display: grid; place-content: center; padding: 28px; color: #536177; background: rgba(247, 249, 252, .9); text-align: center; }
.graph-state b { font-size: 16px; }
.graph-state p { max-width: 300px; margin: 8px 0 0; color: var(--muted); line-height: 1.6; }
.error-state { color: var(--red); }
.error-state button { margin-top: 12px; }
.error-state p { color: inherit; }
.menu-backdrop { position: fixed; z-index: 29; inset: 0; width: 100%; height: 100%; padding: 0; border: 0; border-radius: 0; background: rgba(15, 23, 42, .3); }
.dialog-backdrop { position: fixed; z-index: 60; inset: 0; display: grid; place-items: center; padding: 20px; background: rgba(15, 23, 42, .42); }
.large-neighbor-dialog { width: min(680px, 100%); max-height: min(760px, calc(100vh - 40px)); overflow: auto; padding: 22px; border-radius: 14px; background: #fff; box-shadow: 0 22px 60px rgba(15, 23, 42, .28); }
.large-neighbor-dialog h3 { margin: 0 0 12px; }
.large-neighbor-dialog p { line-height: 1.6; }
.warning-copy { padding: 10px 12px; border-radius: 8px; color: #92400e; background: #fff7ed; }
.facet-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 16px; }
.facet-columns fieldset { display: grid; align-content: start; gap: 8px; min-width: 0; max-height: 240px; overflow: auto; margin: 0; padding: 12px; border: 1px solid var(--border); border-radius: 10px; }
.facet-columns legend { padding: 0 5px; font-weight: 800; }
.facet-columns label { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.facet-columns label small { margin-left: auto; color: var(--muted); }
.dialog-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 18px; }
@media (max-width: 1440px) { .graph-layout { grid-template-columns: 210px minmax(0, 1fr); } .graph-inspector { grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (hover: none), (pointer: coarse) {
  .neighbor-menu { position: fixed; z-index: 30; top: auto; right: 0; bottom: 0; left: 0; border-radius: 16px 16px 0 0; box-shadow: 0 -16px 38px rgba(15, 23, 42, .22); }
  .neighbor-menu button { padding: 16px 20px; }
}
@media (max-width: 900px) { .neighborhood-heading { display: grid; align-items: start; } .neighborhood-heading .actions { justify-content: flex-start; } .graph-toolbar { display: grid; } .graph-toolbar .actions { justify-content: flex-start; } .graph-layout { grid-template-columns: 1fr; } .graph-canvas { order: -1; min-height: 480px; height: 65vh; } .graph-inspector { grid-column: auto; grid-template-columns: 1fr; } .facet-columns { grid-template-columns: 1fr; } }
</style>
