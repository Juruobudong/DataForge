<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api/platform'
import DataForgeFlowCanvas from '../../components/flow/DataForgeFlowCanvas.vue'
import RuntimeInspector from '../../components/flow/inspector/RuntimeInspector.vue'
import { deserializeRuntimeDag } from '../../components/flow/flowModel'

const templates = ref([]), runs = ref([]), catalog = ref([]), environment = ref({ profiles: [], managed_collections: [] })
const capabilities = ref({ derived_runs_enabled: false, derived_run_commit_enabled: false })
const runDetail = ref(null), selectedRunId = ref(''), selectedNode = ref(null), selectedArtifact = ref(null), artifactContent = ref(null)
const runtimeNodes = ref([]), runtimeEdges = ref([]), events = ref([]), cursor = ref(0), error = ref(''), loading = ref(false), parameters = ref('{}')
let timer
const activePreview = computed(() => runDetail.value?.sink_previews?.find(item => item.status === 'pending'))

async function load() {
  loading.value = true; error.value = ''
  try {
    const [templateData, runData, catalogData, vectorData, capabilityData] = await Promise.all([api.flowTemplates(), api.flowRuns(), api.operatorCatalog({ include_internal: true }), api.vectorIndexes(), api.flowRunCapabilities()])
    templates.value = templateData; runs.value = runData; catalog.value = catalogData; environment.value = vectorData; capabilities.value = capabilityData
    if (!selectedRunId.value && runs.value.length) await inspectRun(runs.value[0].id)
  } catch (e) { error.value = e.message } finally { loading.value = false }
}

async function inspectRun(id) {
  selectedRunId.value = id; selectedNode.value = null; selectedArtifact.value = null; artifactContent.value = null; events.value = []; cursor.value = 0
  try {
    runDetail.value = await api.flowRun(id)
    const graph = deserializeRuntimeDag(runDetail.value.runtime_dag, catalog.value)
    runtimeNodes.value = graph.nodes; runtimeEdges.value = graph.edges
    await nextTick(); await pollEvents()
  } catch (e) { error.value = e.message }
}

function inspectNode(node) {
  if (!node) { selectedNode.value = null; return }
  selectedArtifact.value = null; artifactContent.value = null
  selectedNode.value = runDetail.value?.nodes.find(item => item.node_id === node.id) || { node_id: node.id, status: node.data.meta.status, operator_code: node.data.meta.code }
  parameters.value = '{}'
}

async function inspectEdge(edge) {
  if (edge.data?.artifactIds?.length) { await inspectArtifact(edge.data.artifactIds[0]); return }
  const sourceRun = runDetail.value?.nodes.find(item => item.node_id === edge.source)
  if (sourceRun?.output_artifact_ids?.length) await inspectArtifact(sourceRun.output_artifact_ids[0])
}

async function inspectArtifact(id) {
  try { selectedNode.value = null; selectedArtifact.value = await api.artifactDetail(id); artifactContent.value = await api.artifactContent(id, 0, 50) } catch (e) { error.value = e.message }
}

async function pollEvents() {
  if (!selectedRunId.value) return
  try { const page = await api.flowRunEvents(selectedRunId.value, cursor.value); events.value.push(...page.items); cursor.value = page.next_cursor } catch (e) { error.value = e.message }
}

async function derive(mode) {
  if (!selectedNode.value) return
  try {
    const override = JSON.parse(parameters.value || '{}')
    const result = await api.createDerivedRun(selectedRunId.value, { mode, node_id: selectedNode.value.node_id, parameter_overrides: { [selectedNode.value.node_id]: override }, idempotency_key: crypto.randomUUID() })
    await load(); await inspectRun(result.id)
  } catch (e) { error.value = e.message }
}

async function cancelRun() { try { await api.cancelFlowRun(selectedRunId.value); await inspectRun(selectedRunId.value) } catch (e) { error.value = e.message } }
async function persistParameters() { try { await api.persistDerivedParameters(selectedRunId.value, { node_id: selectedNode.value.node_id, parameters: JSON.parse(parameters.value || '{}') }); error.value = '' } catch (e) { error.value = e.message } }
async function commitRun() {
  if (!activePreview.value) return
  try { await api.commitFlowRun(selectedRunId.value, { preview_checksum: activePreview.value.preview_checksum, idempotency_key: crypto.randomUUID() }); await inspectRun(selectedRunId.value) } catch (e) { error.value = e.message }
}

onMounted(async () => { await load(); timer = window.setInterval(pollEvents, 2000) })
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <section class="debug-page">
    <div class="page-head"><div><h2>DataFlow 调试台</h2><p>围绕不可变 Execution Snapshot 重建 Runtime DAG，诊断真实算子、Artifact 与跨 Run 血缘。</p></div><div class="page-actions"><span class="badge blue">管理员诊断</span><button :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button></div></div>
    <details class="environment"><summary>环境摘要 · {{ environment.managed_collections?.length || 0 }} Collections / {{ environment.profiles?.length || 0 }} Index Profiles</summary><div class="env-grid"><span v-for="item in environment.managed_collections || []" :key="item.id" class="badge" :class="item.status==='ready'?'green':'amber'">{{ item.collection_name }} · {{ item.status }}</span><span class="badge">协作式取消</span></div></details>
    <div class="workbench">
      <aside class="left-pane">
        <h3>模板</h3><div v-for="item in templates" :key="item.id" class="compact-card"><b>{{ item.name }}</b><small>r{{ item.revision }} · {{ item.status }}</small></div>
        <h3>Run 历史</h3><button v-for="run in runs" :key="run.id" class="run-card" :class="{active:selectedRunId===run.id}" @click="inspectRun(run.id)"><b>{{ run.run_mode || 'production' }}</b><span>{{ run.status }}</span><small>{{ run.id }}</small></button>
      </aside>
      <main class="dag-pane">
        <div class="dag-toolbar"><div><b>Runtime DAG</b><small v-if="runDetail">{{ runDetail.execution_snapshot_id }} · {{ runDetail.status }}</small></div><div class="actions"><button v-if="capabilities.derived_runs_enabled && selectedNode" @click="derive('node_only')">运行此节点</button><button v-if="capabilities.derived_runs_enabled && selectedNode" class="primary" @click="derive('from_node')">从此节点运行</button><button v-if="capabilities.derived_runs_enabled && selectedNode?.status==='failed'" @click="derive('from_node')">重新运行失败节点</button><button v-if="capabilities.derived_runs_enabled && runDetail && ['queued','running'].includes(runDetail.status)" @click="cancelRun">停止</button></div></div>
        <DataForgeFlowCanvas v-if="runDetail" v-model:nodes="runtimeNodes" v-model:edges="runtimeEdges" mode="runtime" height="590" canvas-id="dataforge-runtime-flow" @select-node="inspectNode" @select-edge="inspectEdge" />
        <div v-else class="empty">选择一个 Run 查看完整 Runtime DAG。</div>
      </main>
      <aside class="right-pane">
        <RuntimeInspector :node="selectedNode" :artifact="selectedArtifact" :content="artifactContent" @inspect-artifact="inspectArtifact" />
        <div v-if="selectedNode && capabilities.derived_runs_enabled" class="override"><label>本次运行参数覆盖</label><textarea v-model="parameters" rows="7"></textarea><small>按 Operator Version 参数 Schema 校验；不会修改父 Run 或已发布模板。</small><button v-if="runDetail?.parent_flow_run_id" @click="persistParameters">保存为模板草稿</button></div>
        <div v-if="activePreview" class="preview"><h4>Sink Diff 待确认</h4><pre>{{ JSON.stringify(activePreview.diff, null, 2) }}</pre><button v-if="capabilities.derived_run_commit_enabled" class="primary" @click="commitRun">确认提交正式知识</button><small v-else>正式提交开关未启用。</small></div>
      </aside>
      <section class="console"><header><b>Console</b><span>cursor {{ cursor }}</span></header><div class="console-lines"><p v-for="event in events" :key="event.cursor" :class="event.level"><time>{{ event.created_at }}</time><code>{{ event.type }}</code><span>{{ event.node_id || 'run' }}</span>{{ event.message }}</p><p v-if="!events.length">暂无运行事件。</p></div></section>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.environment{margin-bottom:12px;padding:10px 14px;border:1px solid #dce3ed;border-radius:10px;background:#fff}.environment summary{cursor:pointer;color:#536177;font-weight:700}.env-grid{display:flex;flex-wrap:wrap;gap:7px;padding-top:10px}.workbench{display:grid;grid-template-columns:220px minmax(620px,1fr) 330px;grid-template-rows:minmax(680px,1fr) 190px;gap:12px}.left-pane,.dag-pane,.right-pane,.console{min-width:0;border:1px solid #dbe3ef;border-radius:12px;background:#fff}.left-pane{overflow:auto;padding:12px}.left-pane h3{margin:8px 0;font-size:12px}.compact-card{display:flex;flex-direction:column;padding:9px;border-bottom:1px solid #edf0f4}.compact-card small,.run-card small,.dag-toolbar small{display:block;margin-top:4px;color:#7a8799}.run-card{display:flex;width:100%;flex-wrap:wrap;justify-content:space-between;margin:5px 0;padding:10px;border:1px solid #e0e6ee;background:#fff;text-align:left}.run-card small{width:100%;overflow:hidden;text-overflow:ellipsis}.run-card.active{border-color:#2f6fed;background:#edf4ff}.dag-pane{overflow:hidden}.dag-toolbar{display:flex;align-items:center;justify-content:space-between;padding:11px 13px;border-bottom:1px solid #edf0f4}.actions{display:flex;gap:6px}.right-pane{display:grid;grid-template-rows:minmax(280px,1fr) auto auto;gap:10px;padding:0 0 10px;overflow:auto}.override,.preview{margin:0 10px;padding:11px;border:1px solid #e0e6ee;border-radius:9px}.override label{display:block;margin-bottom:6px;font-weight:700}.override textarea{box-sizing:border-box;width:100%;font:11px monospace}.override small,.preview small{display:block;margin-top:6px;color:#748196}.preview pre{max-height:140px;overflow:auto;background:#f6f8fb;font-size:10px}.console{grid-column:1/-1;overflow:hidden;background:#182231;color:#d9e2ee}.console header{display:flex;justify-content:space-between;padding:9px 12px;border-bottom:1px solid #344155}.console-lines{height:145px;overflow:auto;padding:6px 12px;font:11px monospace}.console-lines p{display:grid;grid-template-columns:170px 150px 130px 1fr;gap:10px;margin:4px 0}.console-lines time,.console-lines span{color:#8fa2ba}.console-lines .error{color:#ff9f9f}.empty{display:grid;height:590px;place-items:center;color:#7c899a}@media(max-width:1100px){.workbench{grid-template-columns:1fr;grid-template-rows:auto}.left-pane,.dag-pane,.right-pane,.console{grid-column:1}.left-pane{max-height:260px}.right-pane{min-height:500px}}
</style>
<style scoped>
.workbench { grid-template-columns: 240px minmax(620px, 1fr) 350px; gap: 16px; }
.left-pane { padding: 14px; }
.left-pane h3 { font-size: var(--font-card); }
.compact-card { padding: 11px; }
.compact-card small,.run-card small,.dag-toolbar small,.override small,.preview small { font-size: var(--font-technical); }
.run-card { padding: 11px; font-size: var(--font-assist); }
.dag-toolbar { padding: 13px 15px; }
.override,.preview { padding: 13px; }
.override textarea,.console-lines { font: var(--font-technical) ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
.preview pre { font-size: var(--font-technical); }
.console header { padding: 11px 14px; }
.console-lines { padding: 8px 14px; }
@media (min-width: 901px) and (max-width: 1440px) {
  .workbench { grid-template-columns: 220px minmax(520px, 1fr) 320px; gap: 12px; }
}
@media (max-width: 900px) {
  .workbench { grid-template-columns: 1fr; grid-template-rows: auto; }
  .left-pane,.dag-pane,.right-pane,.console { grid-column: 1; }
  .left-pane { max-height: 260px; }
  .right-pane { min-height: 500px; }
  .console-lines p { grid-template-columns: 1fr; }
}
</style>
