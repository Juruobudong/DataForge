<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'
import DataForgeFlowCanvas from '../../components/flow/DataForgeFlowCanvas.vue'
import OperatorInspector from '../../components/flow/inspector/OperatorInspector.vue'
import { deserializeDefinition, serializeDefinition, subflowEnglishName, subflowPrimaryName } from '../../components/flow/flowModel'

const route = useRoute(), router = useRouter(), detail = ref(null), catalog = ref([]), subflows = ref([]), nodes = ref([]), edges = ref([])
const selectedNode = ref(null), description = ref(''), inputContract = ref('{}'), outputContract = ref('{}'), result = ref(null), canvas = ref(null), dirty = ref(false)
const loadState = ref('loading-revision'), loadError = ref(''), actionError = ref(''), actionPending = ref(false)
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
    if (loadState.value === 'ready') { await nextTick(); if (request === loadRequest) canvas.value?.fit() }
  } catch (error) {
    if (request !== loadRequest) return
    loadState.value = 'error'; loadError.value = `子图 Revision 加载失败：${messageOf(error)}`
  }
}
async function copyDraft() {
  if (!detail.value || !workspaceReady.value || actionPending.value) return
  actionError.value = ''; actionPending.value = true
  try { const value = await api.copyFlowSubgraphDraft(detail.value.id, detail.value.revision); await router.push(`/developer/flow-templates/subgraphs/${detail.value.id}/revisions/${value.revision}`) }
  catch (error) { actionError.value = messageOf(error) }
  finally { actionPending.value = false }
}
function payload() { return { definition: { ...serializeDefinition(nodes.value, edges.value), entry_node: detail.value.definition.entry_node, exit_node: detail.value.definition.exit_node }, description: description.value, input_contract: JSON.parse(inputContract.value || '{}'), output_contract: JSON.parse(outputContract.value || '{}') } }
async function action(kind) {
  if (!detail.value || !workspaceReady.value || actionPending.value) return
  actionError.value = ''; actionPending.value = true
  try {
    const value = kind === 'save' ? await api.updateFlowSubgraphDraft(detail.value.id, detail.value.revision, payload()) : kind === 'validate' ? await api.validateFlowSubgraphDraft(detail.value.id, detail.value.revision) : await api.publishFlowSubgraphDraft(detail.value.id, detail.value.revision)
    await load(); result.value = value
  } catch (error) { actionError.value = messageOf(error) }
  finally { actionPending.value = false }
}
function openNested(node) { const target = subflows.value.find(item => item.code === node.data.definition.ref); if (target) router.push({ path: `/developer/flow-templates/subgraphs/${target.id}/revisions/${target.revision}`, query: { trail: JSON.stringify([...trail.value, { id: detail.value.id, revision: detail.value.revision, name: detail.value.name, display_name_zh: detail.value.display_name_zh }]) } }) }
watch(() => `${route.params.subflowId}:${route.params.revision}`, () => load())
onMounted(load)
onBeforeUnmount(() => { loadRequest += 1 })
</script>

<template>
  <section>
    <nav class="breadcrumbs"><RouterLink to="/developer/flow-templates">知识流程</RouterLink><template v-for="item in trail" :key="`${item.id}-${item.revision}`"><span>›</span><RouterLink :to="`/developer/flow-templates/subgraphs/${item.id}/revisions/${item.revision}`">{{ subflowPrimaryName(item) }} · r{{ item.revision }}</RouterLink></template><span>›</span><span>{{ detailName }}</span><span>›</span><b>{{ detail ? `r${detail.revision}` : '加载中' }}</b></nav>
    <div class="page-head"><div><h2>{{ detailName }}</h2><small v-if="detailEnglishName" class="subgraph-english-name">{{ detailEnglishName }}</small><p>{{ description || '查看当前可复用子图 revision 的完整 DAG；双击嵌套 Subgraph 可继续钻取。' }}</p></div><div v-if="detail" class="page-actions"><span class="badge" :class="editable?'amber':'green'">{{ editable ? '草稿' : '只读 revision' }}</span><button v-if="!editable" class="primary" :disabled="!workspaceReady || actionPending" @click="copyDraft">复制为草稿</button><template v-else><button :disabled="!workspaceReady || actionPending" @click="action('validate')">校验</button><button :disabled="!workspaceReady || actionPending" @click="action('save')">保存草稿</button><button class="primary" :disabled="!workspaceReady || actionPending" @click="action('publish')">发布</button></template></div></div>
    <div v-if="loading" class="workspace-state panel" role="status"><span class="state-icon">◌</span><h3>{{ loadState === 'loading-revision' ? '正在加载子图 Revision' : '正在加载完整 DAG' }}</h3><p>{{ loadState === 'loading-revision' ? '正在读取子图定义。' : '正在读取算子目录和可复用子图引用。' }}</p></div>
    <div v-else-if="loadState === 'error'" class="workspace-state panel error-state" role="alert"><span class="state-icon">!</span><h3>子图加载失败</h3><p>{{ loadError }}</p><button class="primary" @click="load()">重新加载</button></div>
    <div v-else-if="loadState === 'empty'" class="workspace-state panel"><span class="state-icon">◇</span><h3>当前 Revision 没有节点</h3><p>该子图定义为空，无法显示完整 DAG。</p><button @click="load()">重新加载</button></div>
    <div v-else-if="workspaceReady" class="subgraph-workspace"><main><DataForgeFlowCanvas :key="`${detail.id}-${detail.revision}`" ref="canvas" v-model:nodes="nodes" v-model:edges="edges" :mode="editable?'edit':'readonly'" height="720" :canvas-id="`subgraph-${detail.id}-${detail.revision}`" @before-change="dirty=true" @select-node="selectedNode=$event" @open-subflow="openNested" /></main><aside><OperatorInspector :operator="selectedOperator" /><div v-if="editable" class="contracts"><label>描述<textarea v-model="description" rows="3"></textarea></label><label>输入契约<textarea v-model="inputContract" rows="7"></textarea></label><label>输出契约<textarea v-model="outputContract" rows="7"></textarea></label></div></aside></div>
    <p v-if="actionError" class="error" role="alert">{{ actionError }}</p><pre v-if="result">{{ JSON.stringify(result, null, 2) }}</pre>
  </section>
</template>

<style scoped>
.breadcrumbs{display:flex;align-items:center;gap:8px;margin-bottom:10px;color:#748196}.breadcrumbs a{color:#2f6fed}.subgraph-english-name{display:block;margin-top:3px;color:#748196;font-size:12px}.subgraph-workspace{display:grid;grid-template-columns:minmax(720px,1fr) 340px;gap:12px}.workspace-state{display:grid;justify-items:center;align-content:center;min-height:420px;padding:32px;text-align:center}.workspace-state h3{margin:10px 0 4px}.workspace-state p{max-width:680px;margin:0 0 16px;color:#66758a}.workspace-state .state-icon{display:grid;place-items:center;width:42px;height:42px;border-radius:50%;color:#2f6fed;background:#eaf1ff;font-size:20px;font-weight:800}.workspace-state.error-state .state-icon{color:#b53b32;background:#fff0ee}.workspace-state.error-state p{color:#9d3d35}.contracts{margin-top:12px;padding:12px;border:1px solid #dbe3ef;border-radius:12px;background:#fff}.contracts label{display:grid;gap:5px;margin-bottom:10px;font-size:11px;font-weight:700}.contracts textarea{font:11px monospace}@media(max-width:1050px){.subgraph-workspace{grid-template-columns:1fr}.subgraph-workspace aside{min-height:380px}}
</style>
