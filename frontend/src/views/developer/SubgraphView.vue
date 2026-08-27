<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'
import DataForgeFlowCanvas from '../../components/flow/DataForgeFlowCanvas.vue'
import OperatorInspector from '../../components/flow/inspector/OperatorInspector.vue'
import AdvancedFlowEditor from '../../components/flow/advanced/AdvancedFlowEditor.vue'
import UnsavedNavigationDialog from '../../components/flow/UnsavedNavigationDialog.vue'
import SubflowReferences from '../../components/flow/SubflowReferences.vue'
import { deserializeDefinition, serializeDefinition, subflowEnglishName, subflowPrimaryName, resolveSubflow } from '../../components/flow/flowModel'

const route = useRoute(), router = useRouter(), detail = ref(null), catalog = ref([]), subflows = ref([]), nodes = ref([]), edges = ref([])
const selectedNode = ref(null), description = ref(''), inputContract = ref('{}'), outputContract = ref('{}'), result = ref(null), canvas = ref(null), dirty = ref(false)
const loadState = ref('loading-revision'), loadError = ref(''), actionError = ref(''), actionPending = ref(false)
const editor = ref(null), entryNode = ref(''), exitNode = ref(''), showReferences = ref(false), pendingNavigation = ref(null)
const versions = computed(() => subflows.value.find(item => item.id === detail.value?.id)?.revisions || [])
const editableNodes = computed(() => editor.value?.nodes || nodes.value)
const returnTarget = computed(() => route.query.return_template_id ? { path: '/developer/flow-templates', query: { template_id: route.query.return_template_id, edit: '1' } } : '/developer/subflows')
let loadRequest = 0
const editable = computed(() => detail.value?.revision_status === 'draft')
const workspaceReady = computed(() => loadState.value === 'ready')
const loading = computed(() => loadState.value === 'loading-revision' || loadState.value === 'loading-dependencies')
const selectedOperator = computed(() => catalog.value.find(item => item.code === selectedNode.value?.data?.meta?.code))
const detailName = computed(() => detail.value ? subflowPrimaryName(detail.value) : '可复用子图')
const detailEnglishName = computed(() => subflowEnglishName(detail.value || {}))
const trail = computed(() => { try { const value = JSON.parse(String(route.query.trail || '[]')); return Array.isArray(value) ? value : [] } catch { return [] } })

function messageOf(error) { return error instanceof Error ? error.message : String(error || '请求失败') }
function resetWorkspace() {
  detail.value = null; catalog.value = []; subflows.value = []; nodes.value = []; edges.value = []; selectedNode.value = null
  description.value = ''; inputContract.value = '{}'; outputContract.value = '{}'; dirty.value = false
  loadError.value = ''; actionError.value = ''; result.value = null
}
async function load() {
  const request = ++loadRequest
  resetWorkspace(); loadState.value = 'loading-revision'
  try {
    const value = await api.flowSubgraphRevision(route.params.subflowId, route.params.revision)
    if (request !== loadRequest) return
    detail.value = value; description.value = value.description || ''; inputContract.value = JSON.stringify(value.input_contract || {}, null, 2); outputContract.value = JSON.stringify(value.output_contract || {}, null, 2)
    entryNode.value = value.definition.entry_node; exitNode.value = value.definition.exit_node
    loadState.value = 'loading-dependencies'
    const [operatorsResult, reusableResult] = await Promise.allSettled([api.operatorCatalog({ include_internal: true }), api.flowSubgraphs()])
    if (request !== loadRequest) return
    const failures = []
    if (operatorsResult.status === 'rejected') failures.push(`算子目录加载失败：${messageOf(operatorsResult.reason)}`)
    if (reusableResult.status === 'rejected') failures.push(`子图目录加载失败：${messageOf(reusableResult.reason)}`)
    if (failures.length) {
      loadState.value = 'error'; loadError.value = failures.join('；')
      return
    }
    catalog.value = operatorsResult.value; subflows.value = reusableResult.value
    const graph = deserializeDefinition(value.definition, catalog.value, subflows.value)
    nodes.value = graph.nodes; edges.value = graph.edges; dirty.value = false
    loadState.value = graph.nodes.length ? 'ready' : 'empty'
    if (loadState.value === 'ready') { await nextTick(); if (request === loadRequest) { canvas.value?.fit(); editor.value?.loadDefinition(value.definition) } }
  } catch (error) {
    if (request !== loadRequest) return
    loadState.value = 'error'; loadError.value = `子图 Revision 加载失败：${messageOf(error)}`
  }
}
async function copyDraft() {
  if (!detail.value || !workspaceReady.value || actionPending.value) return
  actionError.value = ''; actionPending.value = true
  try { const value = await api.copyFlowSubgraphDraft(detail.value.id, detail.value.revision); await router.push({ path: `/developer/flow-templates/subgraphs/${detail.value.id}/revisions/${value.revision}`, query: route.query }) }
  catch (error) { actionError.value = messageOf(error) }
  finally { actionPending.value = false }
}
function payload() { return { definition: { ...(editor.value?.serialize() || serializeDefinition(nodes.value, edges.value)), entry_node: entryNode.value, exit_node: exitNode.value }, description: description.value, input_contract: {}, output_contract: {} } }
async function action(kind) {
  if (!detail.value || !workspaceReady.value || actionPending.value) return
  actionError.value = ''; actionPending.value = true
  try {
    const saved = editable.value ? await api.updateFlowSubgraphDraft(detail.value.id, detail.value.revision, payload()) : null
    const value = kind === 'save' ? saved : kind === 'validate' ? await api.validateFlowSubgraphDraft(detail.value.id, detail.value.revision) : await api.publishFlowSubgraphDraft(detail.value.id, detail.value.revision)
    await load(); result.value = value
    return true
  } catch (error) { actionError.value = messageOf(error) }
  finally { actionPending.value = false }
}
function requestNavigation(target) { if (dirty.value) pendingNavigation.value = target; else router.push(target) }
function discardNavigation() { const target = pendingNavigation.value; pendingNavigation.value = null; dirty.value = false; router.push(target) }
async function saveNavigation() { if (await action('save')) discardNavigation() }
function openItem(target) {
  requestNavigation({ path: `/developer/flow-templates/subgraphs/${target.id}/revisions/${target.revision}`, query: { ...route.query, trail: JSON.stringify([...trail.value, { id: detail.value.id, revision: detail.value.revision, name: detail.value.name, display_name_zh: detail.value.display_name_zh }]) } })
}
function openNested(node) {
  if (!node.data.definition.subflow_revision_id) { actionError.value = '版本未锁定，请复制为草稿并锁定版本后查看'; return }
  const target = resolveSubflow(node.data.definition, subflows.value)
  if (target) openItem(target); else actionError.value = '子流程修订不存在'
}
function selectRevision(event) { const revision = event.target.value; event.target.value = String(detail.value.revision); requestNavigation({ path: `/developer/flow-templates/subgraphs/${detail.value.id}/revisions/${revision}`, query: route.query }) }
watch(() => `${route.params.subflowId}:${route.params.revision}`, () => load())
onMounted(load)
onBeforeUnmount(() => { loadRequest += 1 })
</script>

<template>
  <section>
    <UnsavedNavigationDialog v-if="pendingNavigation" :pending="actionPending" :error="actionError" @cancel="pendingNavigation=null" @discard="discardNavigation" @save="saveNavigation" />
    <nav class="breadcrumbs"><button @click="requestNavigation(returnTarget)">{{ route.query.return_template_id ? '返回来源知识流程' : '可复用子流程' }}</button><template v-for="(item,index) in trail" :key="`${item.id}-${item.revision}`"><span>›</span><button @click="requestNavigation({ path: `/developer/flow-templates/subgraphs/${item.id}/revisions/${item.revision}`, query: { ...route.query, trail: JSON.stringify(trail.slice(0,index)) } })">{{ subflowPrimaryName(item) }} · r{{ item.revision }}</button></template><span>›</span><span>{{ detailName }}</span><span>›</span><b>{{ detail ? `r${detail.revision}` : '加载中' }}</b></nav>
    <div class="page-head"><div><h2>{{ detailName }}</h2><small v-if="detailEnglishName" class="subgraph-english-name">{{ detailEnglishName }}</small><p>{{ description || '查看当前可复用子图 revision 的完整 DAG；双击嵌套 Subgraph 可继续钻取。' }}</p></div><div v-if="detail" class="page-actions"><span class="badge" :class="editable?'amber':'green'">{{ editable ? '草稿' : '只读 revision' }}</span><button v-if="!editable" class="primary" :disabled="!workspaceReady || actionPending" @click="copyDraft">复制为草稿</button><template v-else><button :disabled="!workspaceReady || actionPending" @click="action('validate')">校验</button><button :disabled="!workspaceReady || actionPending" @click="action('save')">保存草稿</button><button class="primary" :disabled="!workspaceReady || actionPending" @click="action('publish')">发布</button></template></div></div>
    <div v-if="detail" class="revision-tools"><label>Revision <select aria-label="查看子流程版本" :value="detail.revision" @change="selectRevision"><option v-for="version in versions" :key="version.revision_id" :value="version.revision">r{{ version.revision }} · {{ version.revision_status === 'draft' ? '草稿' : '已发布' }}</option></select></label><button @click="showReferences=!showReferences">查看引用</button></div>
    <SubflowReferences v-if="showReferences && detail" :item="detail" @close="showReferences=false" />
    <div v-if="loading" class="workspace-state panel" role="status"><span class="state-icon">◌</span><h3>{{ loadState === 'loading-revision' ? '正在加载子图 Revision' : '正在加载完整 DAG' }}</h3><p>{{ loadState === 'loading-revision' ? '正在读取子图定义。' : '正在读取算子目录和可复用子图引用。' }}</p></div>
    <div v-else-if="loadState === 'error'" class="workspace-state panel error-state" role="alert"><span class="state-icon">!</span><h3>子图加载失败</h3><p>{{ loadError }}</p><button class="primary" @click="load()">重新加载</button></div>
    <div v-else-if="loadState === 'empty'" class="workspace-state panel"><span class="state-icon">◇</span><h3>当前 Revision 没有节点</h3><p>该子图定义为空，无法显示完整 DAG。</p><button @click="load()">重新加载</button></div>
    <template v-else-if="workspaceReady">
      <template v-if="editable"><section class="contracts draft-contracts"><label>描述<textarea v-model="description" rows="2" @input="dirty=true"></textarea></label><label>入口<select v-model="entryNode" @change="dirty=true"><option v-for="node in editableNodes" :key="node.id" :value="node.id">{{ node.data.meta.name }} · {{ node.id }}</option></select></label><label>出口<select v-model="exitNode" @change="dirty=true"><option v-for="node in editableNodes" :key="node.id" :value="node.id">{{ node.data.meta.name }} · {{ node.id }}</option></select></label><p>输入输出契约在保存时按边界自动生成；修改入口/出口后须重新保存校验。</p></section><AdvancedFlowEditor ref="editor" fragment :purpose="detail.usage" :catalog="catalog" :subflows="subflows" @dirty="dirty=true" @error="actionError=$event" @open-subflow="openItem" /></template>
      <div v-else class="subgraph-workspace"><main><DataForgeFlowCanvas :key="`${detail.id}-${detail.revision}`" ref="canvas" v-model:nodes="nodes" v-model:edges="edges" mode="readonly" height="720" :canvas-id="`subgraph-${detail.id}-${detail.revision}`" @select-node="selectedNode=$event" @open-subflow="openNested" /></main><aside><OperatorInspector :operator="selectedOperator" /><button v-if="selectedNode?.data.meta.kind === 'subflow'" @click="openNested(selectedNode)">查看内部 DAG</button><details class="contracts"><summary>输入输出契约</summary><pre>{{ inputContract }}</pre><pre>{{ outputContract }}</pre></details></aside></div>
    </template>
    <p v-if="actionError" class="error" role="alert">{{ actionError }}</p><pre v-if="result">{{ JSON.stringify(result, null, 2) }}</pre>
  </section>
</template>

<style scoped>
.breadcrumbs{display:flex;align-items:center;gap:8px;margin-bottom:10px;color:#748196}.breadcrumbs a{color:#2f6fed}.subgraph-english-name{display:block;margin-top:3px;color:#748196;font-size:12px}.subgraph-workspace{display:grid;grid-template-columns:minmax(720px,1fr) 340px;gap:12px}.workspace-state{display:grid;justify-items:center;align-content:center;min-height:420px;padding:32px;text-align:center}.workspace-state h3{margin:10px 0 4px}.workspace-state p{max-width:680px;margin:0 0 16px;color:#66758a}.workspace-state .state-icon{display:grid;place-items:center;width:42px;height:42px;border-radius:50%;color:#2f6fed;background:#eaf1ff;font-size:20px;font-weight:800}.workspace-state.error-state .state-icon{color:#b53b32;background:#fff0ee}.workspace-state.error-state p{color:#9d3d35}.contracts{margin-top:12px;padding:12px;border:1px solid #dbe3ef;border-radius:12px;background:#fff}.contracts label{display:grid;gap:5px;margin-bottom:10px;font-size:11px;font-weight:700}.contracts textarea{font:11px monospace}@media(max-width:1050px){.subgraph-workspace{grid-template-columns:1fr}.subgraph-workspace aside{min-height:380px}}
</style>
<style scoped>.revision-tools{display:flex;gap:12px;align-items:center;margin-bottom:14px}.revision-tools label{display:flex;gap:8px;align-items:center}.draft-contracts{display:grid;grid-template-columns:2fr 1fr 1fr;gap:14px;margin-bottom:14px}.draft-contracts p{grid-column:1/-1;margin:0;color:#65748b}.contracts pre{max-width:320px;overflow:auto}.breadcrumbs button{color:#2f6fed}</style>
