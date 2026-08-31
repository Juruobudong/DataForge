<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, createClientRequestId } from '../../api/platform'
import DataForgeFlowCanvas from '../../components/flow/DataForgeFlowCanvas.vue'
import RuntimeInspector from '../../components/flow/inspector/RuntimeInspector.vue'
import FinalResultsPanel from './FinalResultsPanel.vue'
import { deserializeRuntimeDag } from '../../components/flow/flowModel'
import { debugRunPreflightIssue, NO_DEBUG_REVIEW_INPUTS } from './debugRunForm'
import { consoleNodeLabels, consoleNodePresentation, consoleEventMessage } from './debugConsole'

const router = useRouter()
const route = useRoute()
const templates = ref([]), runs = ref([]), catalog = ref([]), environment = ref({ profiles: [], managed_collections: [], authoring_milvus: { status: 'unknown', message: '' } })
const capabilities = ref({ debug_full_enabled: true, debug_replay_enabled: true, debug_sink_policy: 'preview_only' })
const selectedTemplateId = ref(''), selectedRunId = ref(''), runDetail = ref(null), materialization = ref(null)
const selectedNode = ref(null), selectedArtifact = ref(null), artifactContent = ref(null)
const runtimeNodes = ref([]), runtimeEdges = ref([]), events = ref([]), cursor = ref(0), parameters = ref('{}')
const nodeLabels = computed(() => consoleNodeLabels(runtimeNodes.value))
const consoleEvents = computed(() => events.value.map(event => ({
  ...event, message: consoleEventMessage(event), nodePresentation: consoleNodePresentation(event.node_id, nodeLabels.value),
})))
const error = ref(''), dagError = ref(''), loading = ref(false), actionBusy = ref(false), viewMode = ref('dag')
const drawerOpen = ref(false), revisionKind = ref('draft'), debugOptions = ref(null), selectedReviewIds = ref([]), sinkBindings = ref({}), preflight = ref(null)
const inputSource = ref('builtin_sample'), sampleCode = ref('reviewed-medical-v2')
const optionsLoading = ref(false), preflightLoading = ref(false)
const preparationBusy = computed(() => actionBusy.value || optionsLoading.value || preflightLoading.value)
const milvusStatusLabel = computed(() => ({
  not_configured: '知识生产 Milvus 未配置',
  unavailable: '知识生产 Milvus 当前不可用',
}[environment.value?.authoring_milvus?.status] || ''))
let optionsVersion = 0, preflightVersion = 0
const saveOpen = ref(false), saveName = ref(''), saveDescription = ref(''), actionResult = ref(null)
const runtimeCanvas = ref(null)
const runtimeInspector = ref(null)
const consolePanel = ref(null)
let timer
let pollingRunId = null
let inspectVersion = 0, firstPreviewShown = false, viewChosen = false

const selectedTemplate = computed(() => templates.value.find(item => item.id === selectedTemplateId.value) || null)
const debugRuns = computed(() => runs.value.filter(item => item.debug_input_snapshot_id && (!selectedTemplateId.value || item.template_id === selectedTemplateId.value)))
const businessRuns = computed(() => runs.value.filter(item => !item.debug_input_snapshot_id))
const isDebugRun = computed(() => Boolean(runDetail.value?.debug_input_snapshot_id))
const activePreviews = computed(() => runDetail.value?.sink_previews || [])
watch(activePreviews, previews => {
  if (isDebugRun.value && previews.length && !firstPreviewShown) {
    firstPreviewShown = true
    if (!viewChosen) viewMode.value = 'results'
  }
})
function setView(mode) { viewChosen = true; viewMode.value = mode }
async function inspectResultNode(id) {
  const node = runtimeNodes.value.find(item => item.id === id)
  if (!node) return
  inspectNode(node)
  await nextTick()
  if (selectedNode.value?.node_id === id) runtimeInspector.value?.showDiagnostics()
}
const canReplay = computed(() => isDebugRun.value && capabilities.value.debug_replay_enabled && selectedNode.value)
const groupedReviews = computed(() => {
  const groups = new Map()
  for (const item of debugOptions.value?.review_inputs || []) {
    if (!groups.has(item.document_library_id)) groups.set(item.document_library_id, { id: item.document_library_id, name: item.document_library_name, items: [] })
    groups.get(item.document_library_id).items.push(item)
  }
  return [...groups.values()]
})
const hasReviewInputs = computed(() => Boolean(debugOptions.value?.review_inputs?.length))
const preflightIssue = computed(() => debugRunPreflightIssue(debugOptions.value, selectedReviewIds.value, sinkBindings.value, inputSource.value))
const canRunPreflight = computed(() => Boolean(debugOptions.value && !preflightIssue.value))

const stageOrder = ['input', 'generation', 'quality', 'binding', 'submit']
const stageGroups = computed(() => {
  const byCode = new Map()
  for (const node of runDetail.value?.runtime_dag?.nodes || []) {
    const code = node.stage_code || 'other', label = node.stage_label || node.ref || node.id
    if (!byCode.has(code)) byCode.set(code, { code, label, nodes: [] })
    byCode.get(code).nodes.push(node)
  }
  return [...byCode.values()].sort((a, b) => {
    const ai = stageOrder.indexOf(a.code), bi = stageOrder.indexOf(b.code)
    return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi)
  })
})
function stageStatus(group) {
  const statuses = group.nodes.map(node => node.status)
  if (statuses.some(value => value === 'failed')) return 'failed'
  if (statuses.some(value => ['running', 'queued'].includes(value))) return 'running'
  if (statuses.every(value => ['completed', 'preview_ready'].includes(value))) return 'completed'
  if (statuses.every(value => value === 'skipped')) return 'skipped'
  return 'idle'
}
const stageStatusLabel = { completed: '✓', failed: '×', running: '…', skipped: '⊘', idle: '○' }
function autoLayoutRuntimeDag() { runtimeCanvas.value?.autoLayout() }

function messageOf(value) { return value instanceof Error ? value.message : String(value || '请求失败') }
function runTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false, timeZoneName: 'short' }) : '尚未开始' }
async function load({ inspectLatest = true } = {}) {
  loading.value = true; error.value = ''
  try {
    const vectorRequest = api.vectorIndexes()
      .then(value => ({ value }))
      .catch(() => ({ error: true }))
    const [templateData, runData, catalogData, vectorResult, capabilityData] = await Promise.all([
      api.flowTemplates(), api.flowRuns(), api.operatorCatalog({ include_internal: true }), vectorRequest, api.flowRunCapabilities(),
    ])
    templates.value = templateData; runs.value = runData; catalog.value = catalogData
    environment.value = vectorResult.value || {
      profiles: [], managed_collections: [], capacity: [],
      authoring_milvus: { status: 'unavailable', message: '知识生产 Milvus 当前不可用；环境摘要已降级' },
    }
    capabilities.value = capabilityData
    const requestedTemplate = String(route.query.template_id || '')
    if (requestedTemplate && templates.value.some(item => item.id === requestedTemplate)) selectedTemplateId.value = requestedTemplate
    if (!selectedTemplateId.value && templates.value.length) selectedTemplateId.value = templates.value[0].id
    if (inspectLatest && !selectedRunId.value && debugRuns.value.length && !requestedTemplate) await inspectRun(debugRuns.value[0].id)
  } catch (e) { error.value = messageOf(e) } finally { loading.value = false }
}

async function selectTemplate(item) {
  drawerOpen.value = false
  selectedTemplateId.value = item.id
  await inspectRun(debugRuns.value[0]?.id || '')
}
async function openDebugDrawer() {
  if (!selectedTemplate.value) return
  revisionKind.value = selectedTemplate.value.revision_status === 'draft' ? 'draft' : 'published'
  if (route.query.template_id === selectedTemplateId.value && ['draft', 'published'].includes(route.query.revision_kind)) revisionKind.value = route.query.revision_kind
  drawerOpen.value = true; selectedReviewIds.value = []; sinkBindings.value = {}; preflight.value = null; error.value = ''
  await loadDebugOptions()
}
async function loadDebugOptions() {
  if (!selectedTemplateId.value) return
  const version = ++optionsVersion
  const templateId = selectedTemplateId.value, kind = revisionKind.value
  resetPreflight()
  optionsLoading.value = true; debugOptions.value = null; error.value = ''
  try {
    const options = await api.debugRunOptions(templateId, kind)
    if (version !== optionsVersion || !drawerOpen.value) return
    if (route.query.template_id === templateId && kind === 'draft' &&
        route.query.draft_checksum && route.query.draft_checksum !== options.source_definition_checksum) {
      throw new Error('草稿在离开画布后已变化，请返回流程确认当前 DAG 后重新运行。')
    }
    debugOptions.value = options
    inputSource.value = debugOptions.value.default_input?.input_source || 'builtin_sample'
    sampleCode.value = debugOptions.value.default_input?.sample_code || debugOptions.value.builtin_samples?.[0]?.code || 'reviewed-medical-v2'
    selectedReviewIds.value = []
    sinkBindings.value = Object.fromEntries((debugOptions.value.sink_requirements || []).map(item => [item.output_key, '']))
  } catch (e) {
    if (version === optionsVersion) { error.value = messageOf(e); debugOptions.value = null }
  } finally {
    if (version === optionsVersion) optionsLoading.value = false
  }
  if (version === optionsVersion && drawerOpen.value && canRunPreflight.value) await runPreflight()
}
function toggleReview(item) {
  const selected = new Set(selectedReviewIds.value)
  if (selected.has(item.source_review_snapshot_id)) selected.delete(item.source_review_snapshot_id)
  else {
    const currentLibrary = (debugOptions.value?.review_inputs || []).find(value => selected.has(value.source_review_snapshot_id))?.document_library_id
    if (currentLibrary && currentLibrary !== item.document_library_id) selected.clear()
    selected.add(item.source_review_snapshot_id)
  }
  selectedReviewIds.value = [...selected]
  invalidatePreflight()
}
function resetPreflight() {
  ++preflightVersion
  preflight.value = null; preflightLoading.value = false
}
function invalidatePreflight() {
  resetPreflight()
  error.value = ''
  if (drawerOpen.value && !optionsLoading.value && canRunPreflight.value) void runPreflight()
}
function cancelPreparation() {
  ++optionsVersion
  optionsLoading.value = false
  resetPreflight()
}
watch(drawerOpen, open => { if (!open) cancelPreparation() }, { flush: 'sync' })
function openDocumentLibraries() { drawerOpen.value = false; router.push('/business/documents') }
function debugBody() {
  return {
    template_id: selectedTemplateId.value, revision_id: debugOptions.value.revision.id,
    expected_compiled_checksum: debugOptions.value.compiled_checksum,
    input_source: inputSource.value, sample_code: inputSource.value === 'builtin_sample' ? sampleCode.value : null,
    source_review_snapshot_ids: inputSource.value === 'source_review_snapshot' ? selectedReviewIds.value : [],
    sink_library_bindings: inputSource.value === 'source_review_snapshot' ? { ...sinkBindings.value } : {},
  }
}
async function runPreflight() {
  if (!drawerOpen.value || optionsLoading.value) return
  resetPreflight()
  if (preflightIssue.value) {
    preflight.value = null
    error.value = preflightIssue.value
    return
  }
  const version = preflightVersion
  preflightLoading.value = true; error.value = ''
  try {
    const result = await api.debugRunPreflight(debugBody())
    if (version === preflightVersion) preflight.value = result
  } catch (e) {
    if (version === preflightVersion) error.value = messageOf(e)
  } finally {
    if (version === preflightVersion) preflightLoading.value = false
  }
}
async function createDebugRun() {
  if (preparationBusy.value || preflight.value?.valid !== true) return
  const version = inspectVersion, templateId = selectedTemplateId.value
  actionBusy.value = true; error.value = ''
  try {
    const created = await api.createDebugRun({ ...debugBody(), idempotency_key: createClientRequestId() })
    if (version !== inspectVersion || templateId !== selectedTemplateId.value) return
    drawerOpen.value = false
    await load({ inspectLatest: false })
    if (version !== inspectVersion || templateId !== selectedTemplateId.value) return
    await inspectRun(created.id, { scrollToConsole: true })
  } catch (e) { error.value = messageOf(e) } finally { actionBusy.value = false }
}

async function inspectRun(id, { scrollToConsole = false } = {}) {
  const version = ++inspectVersion
  firstPreviewShown = false; viewChosen = false; viewMode.value = 'dag'
  selectedRunId.value = id; selectedNode.value = null; selectedArtifact.value = null; artifactContent.value = null
  runDetail.value = null; materialization.value = null; runtimeNodes.value = []; runtimeEdges.value = []
  events.value = []; cursor.value = 0; dagError.value = ''; actionResult.value = null
  if (!id) return
  try {
    const detail = await api.flowRun(id)
    if (version !== inspectVersion) return
    runDetail.value = detail
    const graph = deserializeRuntimeDag(detail.runtime_dag, catalog.value)
    runtimeNodes.value = graph.nodes; runtimeEdges.value = graph.edges
    await nextTick()
    if (version !== inspectVersion || selectedRunId.value !== id) return
    if (scrollToConsole) {
      consolePanel.value?.scrollIntoView({
        behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
        block: 'end', inline: 'nearest',
      })
    }
    if (detail.debug_input_snapshot_id && detail.status === 'completed') {
      try {
        const value = await api.debugRunMaterialization(id)
        if (version !== inspectVersion) return
        materialization.value = value
      } catch (e) {
        if (version === inspectVersion) error.value = `流程演化信息暂不可用，不影响查看最终结果：${messageOf(e)}`
      }
    }
    await nextTick()
  } catch (e) { if (version === inspectVersion) dagError.value = messageOf(e) }
  if (version !== inspectVersion) return
  await pollEvents()
}
function inspectNode(node) {
  if (!node) { selectedNode.value = null; return }
  selectedArtifact.value = null; artifactContent.value = null
  selectedNode.value = runDetail.value?.nodes.find(item => item.node_id === node.id) || { node_id: node.id, status: node.data.meta.status, operator_code: node.data.meta.code }
  parameters.value = JSON.stringify(runDetail.value?.parameter_overrides?.[node.id] || {}, null, 2)
}
async function inspectEdge(edge) {
  if (edge.data?.artifactIds?.length) return inspectArtifact(edge.data.artifactIds[0])
  const sourceRun = runDetail.value?.nodes.find(item => item.node_id === edge.source)
  if (sourceRun?.output_artifact_ids?.length) await inspectArtifact(sourceRun.output_artifact_ids[0])
}
async function inspectArtifact(id) {
  try {
    selectedNode.value = null; selectedArtifact.value = await api.artifactDetail(id)
    artifactContent.value = await api.artifactContent(id, 0, 50)
  } catch (e) { error.value = messageOf(e) }
}
async function pollEvents() {
  const runId = selectedRunId.value
  const version = inspectVersion
  if (!runId || pollingRunId === runId) return
  pollingRunId = runId
  try {
    const page = await api.flowRunEvents(runId, cursor.value)
    if (selectedRunId.value !== runId || version !== inspectVersion) return
    events.value.push(...page.items); cursor.value = page.next_cursor
    if (page.items.some(event => event.type.startsWith('node.') || event.type.startsWith('run.') || event.type === 'sink.preview_ready')) {
      const detail = await api.flowRun(runId)
      if (selectedRunId.value !== runId || version !== inspectVersion) return
      runDetail.value = detail
      const graph = deserializeRuntimeDag(detail.runtime_dag, catalog.value)
      const positions = new Map(runtimeNodes.value.map(node => [node.id, node.position]))
      for (const node of graph.nodes) if (positions.has(node.id)) node.position = positions.get(node.id)
      runtimeNodes.value = graph.nodes; runtimeEdges.value = graph.edges
      if (selectedNode.value) selectedNode.value = [...detail.nodes].reverse().find(node => node.node_id === selectedNode.value.node_id) || selectedNode.value
    }
  } catch (e) { if (selectedRunId.value === runId && version === inspectVersion) error.value = messageOf(e) }
  finally { if (pollingRunId === runId) pollingRunId = null }
}
async function derive(mode) {
  if (!selectedNode.value || !isDebugRun.value) return
  const version = inspectVersion, templateId = selectedTemplateId.value
  actionBusy.value = true
  try {
    const override = JSON.parse(parameters.value || '{}')
    const created = await api.createDerivedRun(selectedRunId.value, {
      mode, node_id: selectedNode.value.node_id,
      parameter_overrides: { [selectedNode.value.node_id]: override }, idempotency_key: createClientRequestId(),
    })
    if (version !== inspectVersion || templateId !== selectedTemplateId.value) return
    await load({ inspectLatest: false })
    if (version !== inspectVersion || templateId !== selectedTemplateId.value) return
    await inspectRun(created.id, { scrollToConsole: true })
  } catch (e) { error.value = messageOf(e) } finally { actionBusy.value = false }
}
async function cancelRun() {
  try { await api.cancelFlowRun(selectedRunId.value); await inspectRun(selectedRunId.value) } catch (e) { error.value = messageOf(e) }
}
async function applyToDraft() {
  if (!materialization.value?.can_apply_to_current_draft) return
  actionBusy.value = true
  try {
    actionResult.value = await api.applyDebugRunToDraft(selectedRunId.value, {
      expected_revision_id: materialization.value.source.revision_id,
      expected_definition_checksum: materialization.value.source.definition_checksum,
      idempotency_key: createClientRequestId(),
    })
    materialization.value = await api.debugRunMaterialization(selectedRunId.value)
  } catch (e) { error.value = messageOf(e) } finally { actionBusy.value = false }
}
function openSaveDialog() { saveName.value = ''; saveDescription.value = ''; saveOpen.value = true }
async function saveAsFlow() {
  actionBusy.value = true
  try {
    actionResult.value = await api.saveDebugRunAsFlow(selectedRunId.value, {
      name: saveName.value, description: saveDescription.value, idempotency_key: createClientRequestId(),
    })
    saveOpen.value = false
  } catch (e) { error.value = messageOf(e) } finally { actionBusy.value = false }
}
function openFlow(url) { router.push(url) }

onMounted(async () => { await load(); if (route.query.template_id) await openDebugDrawer(); timer = window.setInterval(pollEvents, 2000) })
onBeforeUnmount(() => { ++inspectVersion; cancelPreparation(); window.clearInterval(timer) })
</script>

<template>
  <section class="debug-page">
    <div class="page-head"><div><h2>运行调试<span v-if="selectedTemplate"> / {{ selectedTemplate.name }}</span></h2><p>内置示例或真实审核快照都会冻结为 DebugInputSnapshot，并进入同一不可变 Runner。</p></div><div class="page-actions"><span class="badge blue">Preview Only</span><button v-if="selectedTemplate" @click="router.push(`/developer/flow-templates?template_id=${selectedTemplate.id}&edit=1`)">返回流程</button><button class="primary" :disabled="!selectedTemplate || loading" @click="openDebugDrawer">准备运行</button><button :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button></div></div>
    <details class="environment"><summary>环境摘要 · {{ environment.managed_collections?.length || 0 }} Collections / {{ environment.profiles?.length || 0 }} Index Profiles <span v-if="milvusStatusLabel" class="badge amber">{{ milvusStatusLabel }}</span></summary><div class="env-grid"><p v-if="milvusStatusLabel" class="milvus-guidance" role="status">{{ environment.authoring_milvus?.message || milvusStatusLabel }} <button class="text-link" @click="router.push('/business/milvus-targets')">管理 Milvus 服务</button></p><span v-for="item in environment.managed_collections || []" :key="item.id" class="badge" :class="item.status==='ready'?'green':'amber'">{{ item.collection_name }} · {{ item.status }}</span><span class="badge">协作式取消</span><span class="badge blue">调试不写正式知识</span></div></details>
    <div class="workbench">
      <aside class="left-pane">
        <h3>知识流程</h3>
        <button v-for="item in templates" :key="item.id" class="template-card" :class="{active:selectedTemplateId===item.id}" @click="selectTemplate(item)"><b>{{ item.name }}</b><small>r{{ item.revision }} · {{ item.revision_status }} · {{ item.authoring_mode }}</small></button>
        <h3>调试 Run</h3>
        <button v-for="run in debugRuns" :key="run.id" class="run-card" :class="{active:selectedRunId===run.id}" @click="inspectRun(run.id)"><b>{{ run.run_mode }}</b><span>{{ run.status }}</span><small>{{ run.id }}</small></button>
        <p v-if="!debugRuns.length" class="muted">该流程还没有调试 Run。</p>
        <details class="business-history"><summary>业务 Run（只读） · {{ businessRuns.length }}</summary><button v-for="run in businessRuns" :key="run.id" class="run-card" :class="{active:selectedRunId===run.id}" @click="inspectRun(run.id)"><b>{{ run.run_mode || 'full' }}</b><span>{{ run.status }}</span><small>{{ run.id }}</small></button></details>
      </aside>
      <main class="dag-pane">
        <div class="dag-toolbar"><div><div class="dag-view-controls"><div class="view-switch"><button :class="{active:viewMode==='stages'}" @click="setView('stages')">阶段视图</button><button :class="{active:viewMode==='dag'}" @click="setView('dag')">执行 DAG</button><button v-if="isDebugRun" :class="{active:viewMode==='results'}" @click="setView('results')">最终结果</button></div><button v-if="runDetail && viewMode==='dag' && !dagError" :disabled="!runtimeNodes.length" @click="autoLayoutRuntimeDag">自动布局</button></div><small v-if="runDetail">运行状态：{{ runDetail.status }}</small></div><div class="actions"><button v-if="canReplay" :disabled="actionBusy" @click="derive('node_only')">运行此节点</button><button v-if="canReplay" class="primary" :disabled="actionBusy" @click="derive('from_node')">从此节点运行</button><button v-if="canReplay && selectedNode?.status==='failed'" :disabled="actionBusy" @click="derive('from_node')">重新运行失败节点</button><button v-if="isDebugRun && runDetail && ['queued','running'].includes(runDetail.status)" @click="cancelRun">停止</button></div></div>
        <section v-if="runDetail" class="run-provenance" aria-label="本次运行快照">
          <details :key="runDetail.id" class="run-technical-details">
            <summary>技术详情</summary>
            <div class="run-technical-values">
              <b>Run {{ runDetail.id }}</b>
              <span>来源：{{ runDetail.revision_kind === 'draft' ? '当前草稿（启动时冻结）' : runDetail.revision_kind === 'published' ? '已发布 Revision' : '执行快照' }}<template v-if="runDetail.source_revision"> · r{{ runDetail.source_revision }}</template></span>
              <span>{{ runDetail.node_count ?? runtimeNodes.length }} 节点 · {{ runDetail.edge_count ?? runtimeEdges.length }} 连线</span>
              <span>启动时间：{{ runTime(runDetail.started_at) }} · 入队：{{ runDetail.created_at ? runTime(runDetail.created_at) : '—' }}</span>
              <small>本次运行使用不可变执行快照；继续编辑草稿不会改变本次运行。</small>
              <code v-if="runDetail.source_definition_checksum">Draft checksum：{{ runDetail.source_definition_checksum }}</code>
              <code>Flow checksum：{{ runDetail.compiled_checksum || runDetail.execution_checksum || '—' }}</code>
              <code>执行快照 ID：{{ runDetail.execution_snapshot_id || '—' }}</code>
              <code v-if="runDetail.parent_flow_run_id">父 Run ID：{{ runDetail.parent_flow_run_id }}；参数覆盖见节点记录。</code>
            </div>
          </details>
        </section>
        <div v-if="dagError" class="dag-error"><b>Runtime DAG 加载失败</b><p>{{ dagError }}</p><button @click="inspectRun(selectedRunId)">重新加载</button></div>
        <template v-else-if="runDetail">
          <div v-show="viewMode==='stages'" class="stage-view"><button v-for="group in stageGroups" :key="group.code" class="stage-row" :class="stageStatus(group)" @click="setView('dag')"><span class="stage-status">{{ stageStatusLabel[stageStatus(group)] }}</span><span class="stage-name">{{ group.label }}</span><span class="stage-count">{{ group.nodes.length }} 节点</span></button><p v-if="!stageGroups.length" class="empty">该 Run 没有阶段元数据。</p></div>
          <DataForgeFlowCanvas v-show="viewMode==='dag'" ref="runtimeCanvas" v-model:nodes="runtimeNodes" v-model:edges="runtimeEdges" mode="runtime" height="var(--debug-canvas-height)" canvas-id="dataforge-runtime-flow" @select-node="inspectNode" @select-edge="inspectEdge" />
          <FinalResultsPanel v-if="isDebugRun" v-show="viewMode==='results'" :key="runDetail.id" :run="runDetail" @inspect-node="inspectResultNode" />
        </template>
        <div v-else class="ready-state"><b>准备运行</b><p>流程：{{ selectedTemplate?.name || '请选择知识流程' }}</p><p>输入：DataForge 内置示例审核数据</p><p>运行后将在这里显示不可变 Runtime DAG、最终结果、Artifact 与 Sink Diff。</p><button class="primary" :disabled="!selectedTemplate" @click="openDebugDrawer">开始配置</button></div>
      </main>
      <aside class="right-pane">
        <RuntimeInspector ref="runtimeInspector" :node="selectedNode" :operator="runtimeNodes.find(node => node.id === selectedNode?.node_id)?.data.meta" :artifact="selectedArtifact" :content="artifactContent" @inspect-artifact="inspectArtifact" />
        <div v-if="selectedNode && isDebugRun" class="override"><label>本次运行参数覆盖</label><textarea v-model="parameters" rows="7"></textarea><small>只接受 Operator Version Schema 已支持参数；不修改源流程。</small></div>
        <div v-for="preview in activePreviews" :key="preview.id" class="preview"><h4>{{ preview.output_key }} · Sink Diff Preview</h4><div class="diff-grid"><span>新增<b>{{ preview.diff.ADD || 0 }}</b></span><span>更新<b>{{ preview.diff.UPDATE || 0 }}</b></span><span>删除<b>{{ preview.diff.INACTIVE || 0 }}</b></span><span>不变<b>{{ preview.diff.UNCHANGED || 0 }}</b></span></div><small>本次调试不会写入正式知识。</small></div>
        <div v-if="materialization" class="evolution"><h4>流程演化</h4><button v-if="materialization.can_apply_to_current_draft" :disabled="actionBusy" @click="applyToDraft">应用到当前草稿</button><button v-if="materialization.can_save_as_flow" class="primary" :disabled="actionBusy" @click="openSaveDialog">保存为自定义流程</button><button @click="openFlow(`/developer/flow-templates?template_id=${materialization.source.template_id}&edit=1`)">打开源流程</button><small v-if="!materialization.can_apply_to_current_draft">{{ materialization.apply_blockers?.[0]?.reason || materialization.apply_blockers?.[0] }}</small></div>
        <div v-if="actionResult" class="action-result"><b>{{ actionResult.name ? `已创建“${actionResult.name}”` : '草稿已更新' }}</b><span>状态：{{ actionResult.status }}</span><button v-if="actionResult.open_url" class="primary" @click="openFlow(actionResult.open_url)">打开流程</button></div>
      </aside>
    </div>
    <section ref="consolePanel" class="console">
      <header><b>Console</b><span>cursor {{ cursor }}</span></header>
      <div class="console-lines" tabindex="0" role="region" aria-label="运行日志">
        <p v-for="event in consoleEvents" :key="event.cursor" class="console-row" :class="event.level">
          <time>{{ event.created_at }}</time>
          <code>{{ event.type }}</code>
          <span class="console-node">
            <span class="console-node-name">{{ event.nodePresentation.label }}</span>
            <span v-if="event.nodePresentation.technicalId" class="console-node-id">{{ event.nodePresentation.technicalId }}</span>
          </span>
          <span class="console-message">{{ event.message }}</span>
        </p>
        <p v-if="!events.length">暂无运行事件。</p>
      </div>
    </section>
    <div v-if="drawerOpen" class="overlay" @click.self="drawerOpen=false"><section class="drawer"><header><div><h3>准备运行</h3><p>{{ selectedTemplate?.name }} · 真实 Runner · Preview Only</p></div><button @click="drawerOpen=false">关闭</button></header><label>Revision<select v-model="revisionKind" :disabled="actionBusy" @change="loadDebugOptions"><option value="draft">当前草稿</option><option value="published">最新已发布 Revision</option></select></label><template v-if="debugOptions"><div class="revision-summary">r{{ debugOptions.revision.revision }} · {{ debugOptions.revision.status }} · {{ debugOptions.revision.authoring_mode }}</div><h4>测试数据</h4><label class="input-mode"><input v-model="inputSource" type="radio" value="builtin_sample" @change="invalidatePreflight"> 内置示例数据</label><section v-if="inputSource==='builtin_sample'" class="sample-card"><b>DataForge 示例审核数据</b><select v-model="sampleCode" @change="invalidatePreflight"><option v-for="item in debugOptions.builtin_samples || []" :key="item.code" :value="item.code">{{ item.name }} · v{{ item.version }}</option></select><p>使用虚拟空库计算 Diff；候选结果只显示为预计新增，不创建 KnowledgeLibrary。</p></section><label class="input-mode"><input v-model="inputSource" type="radio" value="source_review_snapshot" @change="invalidatePreflight"> 使用业务审核数据</label><template v-if="inputSource==='source_review_snapshot'"><p class="muted">可选择同一文档库中的多份当前审核冻结结果。</p><div v-if="!hasReviewInputs" class="form-empty"><b>没有可用的审核输入</b><p>{{ NO_DEBUG_REVIEW_INPUTS }}</p><button class="primary" @click="openDocumentLibraries">前往文档库</button></div><template v-else><section v-for="group in groupedReviews" :key="group.id" class="review-group"><b>{{ group.name }}</b><label v-for="item in group.items" :key="item.source_review_snapshot_id" class="review-option"><input type="checkbox" :checked="selectedReviewIds.includes(item.source_review_snapshot_id)" @change="toggleReview(item)"><span>{{ item.filename }}<small>Review #{{ item.review_no }} · {{ item.chunk_count }} chunks</small></span></label></section><p v-if="!selectedReviewIds.length" class="field-error">至少选择一份同一文档库中的审核文档。</p></template><h4>输出预览目标</h4><label v-for="sink in debugOptions.sink_requirements" :key="sink.output_key">{{ sink.output_key }}<select v-model="sinkBindings[sink.output_key]" @change="invalidatePreflight"><option value="">请选择 KnowledgeLibrary</option><option v-for="item in debugOptions.sink_options[sink.output_key] || []" :key="item.id" :value="item.id">{{ item.name }}</option></select><small v-if="!sinkBindings[sink.output_key]" class="field-error">请选择该输出的预览知识库。</small></label></template><div class="preview-policy"><b>Sink 模式：Preview Only</b><span>{{ inputSource==='builtin_sample' ? '虚拟空库 Diff，不写业务数据。' : '只计算真实知识库 Diff，不提交。' }}</span></div><section v-if="preflight" role="status"><p :class="preflight.valid ? 'success' : 'error'">{{ preflight.valid ? (preflight.issues?.length ? '基础预检通过，仍有执行时检查项' : '预检通过') : '预检未通过' }}：{{ preflight.input_count }} 个文档块 · {{ preflight.output_keys.join('、') }}</p><p v-for="(issue,index) in preflight.issues || []" :key="index" :class="issue.severity === 'error' ? 'error' : 'preflight-warning'">{{ issue.node_id }}：{{ issue.message }}</p></section></template><p v-if="optionsLoading" role="status">正在加载运行配置…</p><p v-else-if="preflightLoading" role="status">正在运行预检…</p><p v-if="error" class="error" role="alert">{{ error }}</p><footer><button :disabled="preparationBusy || !canRunPreflight" @click="runPreflight">运行预检</button><button class="primary" :disabled="preparationBusy || preflight?.valid !== true" @click="createDebugRun">开始运行</button></footer></section></div>
    <div v-if="saveOpen" class="overlay" @click.self="saveOpen=false"><section class="save-dialog"><h3>保存为自定义流程</h3><label>流程名称<input v-model="saveName" placeholder="医疗语义图谱流程"></label><label>描述<textarea v-model="saveDescription" rows="3"></textarea></label><div class="save-scope"><b>将保存</b><p>{{ materialization?.saved_content?.join('、') }}</p><b>不会保存</b><p>{{ materialization?.excluded_content?.join('、') }}</p></div><footer><button @click="saveOpen=false">取消</button><button class="primary" :disabled="actionBusy || !saveName.trim()" @click="saveAsFlow">保存</button></footer></section></div>
    <p v-if="error" class="error global-error">{{ error }}</p>
  </section>
</template>

<style scoped>
.debug-page { --debug-canvas-height: max(720px, calc(100dvh - 240px)); }
.left-pane, .right-pane { min-height: 0; contain: size; }
.debug-page :deep(.final-results), .stage-view { height: var(--debug-canvas-height); overflow: auto; }
.stage-view { align-content: start; }
.run-provenance { margin: 0 12px 12px; padding: 8px 12px; border: 1px solid #dbe3ef; border-radius: 8px; background: #f7f9fc; font-size: var(--font-technical); }
.run-provenance code, .run-provenance small { width: 100%; overflow-wrap: anywhere; color: #627189; }
.run-technical-details { width: 100%; min-width: 0; }
.run-technical-details summary { cursor: pointer; color: #536177; font-weight: 600; }
.run-technical-details summary:focus-visible { outline: 2px solid #2f6fed; outline-offset: 3px; border-radius: 3px; }
.run-technical-values { display: grid; gap: 8px; min-width: 0; padding-top: 12px; }
.run-technical-values > * { min-width: 0; overflow-wrap: anywhere; }
.run-technical-values code { min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.environment{margin-bottom:12px;padding:10px 14px;border:1px solid #dce3ed;border-radius:10px;background:#fff}.environment summary{cursor:pointer;color:#536177;font-weight:700}.environment summary .badge{margin-left:7px}.env-grid{display:flex;flex-wrap:wrap;gap:7px;padding-top:10px}.milvus-guidance{display:flex;width:100%;align-items:center;justify-content:space-between;gap:12px;margin:0 0 3px;padding:9px 11px;border-radius:8px;background:#fff7e8;color:#7a5315}.milvus-guidance button{font-weight:700}.workbench{display:grid;grid-template-columns:240px minmax(620px,1fr) 350px;grid-template-rows:minmax(0,1fr);gap:16px}.left-pane,.dag-pane,.right-pane,.console{min-width:0;border:1px solid #dbe3ef;border-radius:12px;background:#fff}.left-pane{overflow:auto;padding:14px}.left-pane h3{margin:10px 0;font-size:var(--font-card)}.template-card,.run-card{display:flex;width:100%;flex-wrap:wrap;justify-content:space-between;margin:5px 0;padding:11px;border:1px solid #e0e6ee;background:#fff;text-align:left}.template-card b,.template-card small{width:100%}.template-card small,.run-card small,.dag-toolbar small{display:block;margin-top:4px;color:#7a8799;font-size:var(--font-technical)}.template-card.active,.run-card.active{border-color:#2f6fed;background:#edf4ff}.run-card small{width:100%;overflow:hidden;text-overflow:ellipsis}.business-history{margin-top:12px}.business-history summary{cursor:pointer;color:#6c7a8e;font-size:11px}.dag-pane{overflow:hidden}.dag-toolbar{display:flex;align-items:center;justify-content:space-between;padding:13px 15px;border-bottom:1px solid #edf0f4}.actions{display:flex;gap:6px}.view-switch{display:inline-flex;gap:2px;padding:2px;border:1px solid #dfe5ed;border-radius:8px;background:#eef2f7}.view-switch button{border:0;border-radius:6px;padding:4px 11px;background:transparent;color:#66758a;font-weight:700}.view-switch button.active{background:#fff;color:#2f6fed}.stage-view{display:grid;gap:8px;padding:16px}.stage-row{display:flex;align-items:center;gap:12px;padding:13px 15px;border:1px solid #e0e6ee;border-radius:10px;background:#fafbfd;text-align:left}.stage-row.completed .stage-status{color:#1d8c65}.stage-row.failed .stage-status{color:#c0392b}.stage-name{flex:1;font-weight:700}.stage-count{color:#8290a3;font-size:11px}.dag-error{display:grid;height:var(--debug-canvas-height);place-content:center;padding:30px;color:#9b3434;text-align:center}.dag-error p{max-width:760px;line-height:1.6}.right-pane{display:grid;grid-template-rows:minmax(280px,1fr) auto auto auto;gap:10px;padding-bottom:10px;overflow:auto}.override,.preview,.evolution,.action-result{margin:0 10px;padding:13px;border:1px solid #e0e6ee;border-radius:9px}.override label{display:block;margin-bottom:6px;font-weight:700}.override textarea{box-sizing:border-box;width:100%;font:var(--font-technical) ui-monospace,monospace}.override small,.preview small,.evolution small{display:block;margin-top:6px;color:#748196}.diff-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}.diff-grid span{padding:8px;border-radius:8px;background:#f5f7fb;color:#6c788a;font-size:10px}.diff-grid b{display:block;margin-top:4px;color:#24364f;font-size:16px}.evolution{display:grid;gap:7px}.action-result{display:grid;gap:7px;border-color:#b9dfce;background:#f2fbf7}
.console{height:260px;margin-top:24px;scroll-margin-block:88px 16px;display:flex;flex-direction:column;overflow:hidden;background:#182231;color:#d9e2ee}
.console header{display:flex;flex:none;justify-content:space-between;padding:11px 16px;border-bottom:1px solid #344155;font-size:var(--font-body)}
.console-lines{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:10px 16px;font:var(--font-body)/1.7 ui-monospace,SFMono-Regular,Consolas,"Microsoft YaHei",monospace}
.console-lines:focus-visible{outline:2px solid #8fb8ff;outline-offset:-2px}
.console-row{display:grid;grid-template-columns:200px 180px 160px minmax(0,1fr);align-items:start;gap:16px;margin:6px 0}
.console-row>*{min-width:0;overflow-wrap:anywhere}
.console-row code{font:inherit}
.console-row time,.console-node{color:#aebed2}
.console-node-name,.console-node-id{display:block;overflow-wrap:anywhere}
.console-node-name{color:#d9e2ee}
.console-node-id{color:#aebed2}
.console-message{white-space:pre-wrap}
.empty{display:grid;height:590px;place-items:center;color:#7c899a}.muted{color:#7c899a;font-size:11px}.overlay{position:fixed;z-index:30;inset:0;display:flex;justify-content:flex-end;background:rgba(16,24,40,.4)}.drawer{box-sizing:border-box;width:min(620px,90vw);height:100%;overflow:auto;padding:22px;background:#fff;box-shadow:-10px 0 28px rgba(15,23,42,.18)}.drawer header{display:flex;justify-content:space-between}.drawer label,.save-dialog label{display:grid;gap:6px;margin:12px 0;font-weight:700}.drawer select,.save-dialog input,.save-dialog textarea{width:100%}.review-group{margin:10px 0;padding:12px;border:1px solid #e2e7ee;border-radius:10px}.review-option{display:flex!important;grid-template-columns:none!important;flex-direction:row;align-items:flex-start}.review-option small{display:block;color:#7c899a}.preview-policy,.save-scope{display:grid;gap:4px;margin-top:14px;padding:12px;border-radius:10px;background:#edf4ff;color:#365477}.drawer footer,.save-dialog footer{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}.success{color:#1d8c65}.save-dialog{width:min(520px,90vw);margin:auto;padding:22px;border-radius:14px;background:#fff}.global-error{position:sticky;bottom:10px;padding:12px;border:1px solid #efcccc;border-radius:9px;background:#fff0f0;white-space:pre-wrap}@media(max-width:1200px){.workbench{grid-template-columns:220px minmax(520px,1fr) 320px;gap:12px}}
.form-empty{display:grid;gap:8px;padding:18px;border:1px dashed #b9c8dc;border-radius:10px;background:#f7f9fc;color:#536177}.form-empty p{margin:0;line-height:1.6}.form-empty button{justify-self:start}.field-error{color:#b5473c;font-size:11px;font-weight:500}
.ready-state{display:grid;height:var(--debug-canvas-height);place-content:center;justify-items:center;gap:8px;color:#647287;text-align:center}.ready-state b{font-size:20px;color:#2f4058}.ready-state p{margin:0}.ready-state button{margin-top:8px}.input-mode{display:flex!important;grid-template-columns:none!important;align-items:center;gap:7px}.sample-card{display:grid;gap:8px;padding:14px;border:1px solid #c9daf7;border-radius:10px;background:#f4f8ff}.sample-card p{margin:0;color:#64758c;line-height:1.5}
.dag-view-controls{display:flex;align-items:center;gap:6px}
</style>
