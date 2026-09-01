<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../../api/platform'

const props = defineProps({
  deploymentId: String, releaseStage: String, institution: Boolean,
  projectCode: String, deploymentCode: String,
})

const viewMode = ref('effect'), routeMode = ref('draft'), version = ref('')
const taskCode = ref(''), orgCode = ref(''), query = ref('')
const options = ref({ tasks: [], versions: [], rerankers: [] })
const effectResult = ref(null), publicEnvelope = ref(null), traceResult = ref(null)
const error = ref(''), busy = ref(false), loading = ref(false), unavailable = ref('')
const experiment = ref(false), topK = ref(10), finalTopK = ref(5), reranker = ref(null)
const filters = ref([]), copyNotice = ref('')
let optionsEpoch = 0, runEpoch = 0

const task = computed(() => options.value.tasks.find(item => item.task_code === taskCode.value))
const orgs = computed(() => task.value?.org_routes || [])
const fields = computed(() => task.value?.filter_fields || {})
const publicBody = computed(() => publicEnvelope.value?.response?.body || null)
const publicPath = computed(() => publicEnvelope.value?.request?.path || (
  props.projectCode && props.deploymentCode && taskCode.value
    ? `/api/runtime/retrieval/v1/${encodeURIComponent(props.projectCode)}/${encodeURIComponent(props.deploymentCode)}/${props.releaseStage}/${encodeURIComponent(taskCode.value)}/query`
    : ''
))
const curlText = computed(() => publicPath.value ? [
  `curl -X POST "${globalThis.location?.origin || '<DATAFORGE_URL>'}${publicPath.value}"`,
  '-H "Authorization: Bearer <DATAFORGE_RETRIEVAL_TOKEN>"',
  '-H "Content-Type: application/json"',
  `-d '${JSON.stringify({ org_code: orgCode.value, query: query.value || '<QUERY>' })}'`,
].join(' \\\n+  ') : '')
const stageNames = {
  routing: '路由与授权', embedding: 'Query Embedding', recall: 'Vector Search 与候选合并',
  reranker: 'Rerank', final: 'Final Results', context: 'Context Preview', evidence: 'Citation / Evidence',
}
const statusNames = { completed: '完成', failed: '失败', skipped: '跳过', blocked: '待本地执行' }
const traceStages = computed(() => traceResult.value?.stages || [])

function clearResults({ keepTrace = false } = {}) {
  runEpoch += 1; effectResult.value = null; publicEnvelope.value = null
  if (!keepTrace) traceResult.value = null
  busy.value = false; error.value = ''; copyNotice.value = ''
}

async function loadOptions() {
  const epoch = ++optionsEpoch
  clearResults({ keepTrace: viewMode.value === 'trace' && !!traceResult.value })
  loading.value = true; unavailable.value = ''
  const priorVersions = options.value.versions
  options.value = { tasks: [], versions: priorVersions, rerankers: [] }
  taskCode.value = ''; orgCode.value = ''; filters.value = []; experiment.value = false
  if (!props.deploymentId || viewMode.value !== 'public' && routeMode.value === 'historical' && !version.value) {
    loading.value = false; return
  }
  try {
    const params = {
      release_stage: props.releaseStage,
      route_mode: viewMode.value === 'public' ? 'published' : routeMode.value,
    }
    if (viewMode.value !== 'public' && routeMode.value === 'historical') params.version_no = version.value
    const response = await api.retrievalDebugOptions(props.deploymentId, params)
    if (epoch !== optionsEpoch) return
    options.value = response
    taskCode.value = response.tasks[0]?.task_code || ''
    if (!response.tasks.length) unavailable.value = viewMode.value === 'public'
      ? '当前环境没有可测试的已发布任务或组织授权。'
      : '所选配置没有可测试的任务或组织授权。'
  } catch (e) {
    if (epoch !== optionsEpoch) return
    unavailable.value = viewMode.value === 'public' ? '当前环境尚无可用的已发布检索版本。' : ''
    if (!unavailable.value) error.value = e.message
  } finally { if (epoch === optionsEpoch) loading.value = false }
}

watch(() => [props.deploymentId, props.releaseStage, props.projectCode, props.deploymentCode], () => {
  routeMode.value = 'draft'; version.value = ''; options.value = { tasks: [], versions: [], rerankers: [] }
  clearResults(); loadOptions()
}, { immediate: true })
watch(viewMode, value => {
  if (value === 'trace' && traceResult.value) return
  loadOptions()
})
watch([routeMode, version], () => { if (viewMode.value !== 'public') loadOptions() })
watch(task, value => {
  clearResults({ keepTrace: viewMode.value === 'trace' })
  filters.value = []; experiment.value = false
  orgCode.value = value?.org_routes?.[0]?.org_code || ''
  topK.value = value?.top_k ?? 10; finalTopK.value = value?.final_top_k ?? 5
  reranker.value = value?.reranker_serving_code ?? null
})
watch([orgCode, query, experiment, topK, finalTopK, reranker], () => clearResults())
watch(filters, () => clearResults(), { deep: true })
onBeforeUnmount(() => { optionsEpoch += 1; runEpoch += 1 })

function addFilter() { filters.value.push({ field: Object.keys(fields.value)[0] || '', op: 'eq', value: '' }) }
function filterPayload(item) {
  const dtype = fields.value[item.field]
  let value = item.value
  if (item.op === 'in' || dtype !== 'VARCHAR') {
    try { value = JSON.parse(value) } catch { throw new Error('数值、布尔或集合过滤值必须使用合法 JSON。') }
  }
  return { field: item.field, op: item.op, value }
}
function debugPayload() {
  const body = {
    release_stage: props.releaseStage, route_mode: routeMode.value,
    task_code: taskCode.value, org_code: orgCode.value, query: query.value,
    filters: filters.value.map(filterPayload),
  }
  if (routeMode.value === 'historical') body.version_no = Number(version.value)
  if (experiment.value) body.overrides = {
    top_k: topK.value, final_top_k: finalTopK.value, reranker_serving_code: reranker.value,
  }
  return body
}
async function runEffect(openTrace = false) {
  const epoch = ++runEpoch
  error.value = ''; effectResult.value = null; busy.value = true
  try {
    const response = await api.retrievalDebug(props.deploymentId, debugPayload())
    if (epoch !== runEpoch) return
    effectResult.value = response; traceResult.value = response
    if (openTrace) viewMode.value = 'trace'
  } catch (e) { if (epoch === runEpoch) error.value = e.message }
  finally { if (epoch === runEpoch) busy.value = false }
}
async function runPublic() {
  const epoch = ++runEpoch
  error.value = ''; publicEnvelope.value = null; busy.value = true
  try {
    const envelope = await api.retrievalPublicTest(props.deploymentId, {
      release_stage: props.releaseStage, task_code: taskCode.value,
      org_code: orgCode.value, query: query.value,
    })
    if (epoch !== runEpoch) return
    publicEnvelope.value = envelope; traceResult.value = envelope.trace
  } catch (e) {
    if (epoch !== runEpoch) return
    if (e.problem?.trace) {
      publicEnvelope.value = e.problem; traceResult.value = e.problem.trace
    }
    error.value = e.detail || e.message
  } finally { if (epoch === runEpoch) busy.value = false }
}
function openTrace() { if (traceResult.value) viewMode.value = 'trace' }
function clearTrace() { traceResult.value = null; loadOptions() }
function stage(key, result = effectResult.value) { return result?.stages?.find(item => item.key === key) || null }
function candidates(value) { return value?.data?.candidates || value?.data?.results || [] }
async function copyValue(value, success) {
  try { await navigator.clipboard.writeText(value); copyNotice.value = success }
  catch { copyNotice.value = '无法访问剪贴板，请手动选择并复制。' }
}
</script>

<template>
  <section class="retrieval-validation stack">
    <header><h3>验证</h3><p>检索效果看结果，公共接口看业务契约，链路调试看内部执行。</p></header>
    <nav class="validation-tabs" aria-label="验证工具">
      <button type="button" :class="{active:viewMode==='effect'}" @click="viewMode='effect'">检索效果</button>
      <button type="button" :class="{active:viewMode==='public'}" @click="viewMode='public'">公共接口</button>
      <button type="button" :class="{active:viewMode==='trace'}" @click="viewMode='trace'">链路调试</button>
    </nav>

    <template v-if="viewMode==='effect'">
      <form class="panel stack" @submit.prevent="runEffect(false)">
        <div class="controls">
          <label>测试对象<select v-model="routeMode"><option value="draft">当前配置</option><option value="published">当前已发布</option><option value="historical">历史版本</option></select></label>
          <label v-if="routeMode==='historical'">历史版本<select v-model="version" required><option value="">选择版本</option><option v-for="item in options.versions.filter(v=>['published','frozen'].includes(v.status))" :key="item.id" :value="item.version_no">V{{ item.version_no }}</option></select></label>
          <label>检索任务<select v-model="taskCode" required :disabled="loading"><option value="">选择任务</option><option v-for="item in options.tasks" :key="item.task_code" :value="item.task_code">{{ item.task_name || item.task_code }}</option></select></label>
          <label>组织授权<select v-model="orgCode" required><option value="">选择 org_code</option><option v-for="item in orgs" :key="item.org_code" :value="item.org_code">{{ item.org_name || item.org_code }} · {{ item.org_code }}</option></select></label>
        </div>
        <label>Query<textarea v-model="query" rows="3" maxlength="8192" required placeholder="输入要验证检索效果的问题"></textarea></label>
        <details class="advanced-box"><summary>高级参数与本次实验</summary>
          <div v-for="(item,index) in filters" :key="index" class="controls">
            <label>过滤字段<select v-model="item.field"><option v-for="(dtype,name) in fields" :key="name" :value="name">{{ name }} · {{ dtype }}</option></select></label>
            <label>条件<select v-model="item.op"><option value="eq">等于</option><option value="in">属于集合</option><option value="gt">大于</option><option value="gte">大于等于</option><option value="lt">小于</option><option value="lte">小于等于</option></select></label>
            <label>值<input v-model="item.value" required></label><button type="button" @click="filters.splice(index,1)">移除</button>
          </div>
          <button type="button" :disabled="!Object.keys(fields).length || filters.length>=32" @click="addFilter">添加过滤条件</button>
          <label><input v-model="experiment" type="checkbox"> 临时覆盖检索策略，不保存</label>
          <div v-if="experiment" class="controls experiment"><label>召回候选数<input v-model.number="topK" type="number" min="1" max="200"></label><label>最终返回数<input v-model.number="finalTopK" type="number" min="1" :max="topK"></label><label>Reranker<select v-model="reranker"><option :value="null">关闭重排</option><option v-for="item in options.rerankers" :key="item.id" :value="item.serving_code" :disabled="!item.is_enabled">{{ item.name }}</option></select></label></div>
        </details>
        <p v-if="unavailable" class="notice amber">{{ unavailable }}</p>
        <button class="primary" :disabled="busy||loading||!!unavailable||!taskCode||!orgCode||!query.trim()">{{ busy?'检索中…':'执行检索效果测试' }}</button>
      </form>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <template v-if="effectResult">
        <article class="panel result-summary"><div><b>{{ effectResult.experimental?'本次实验':'版本配置' }} · {{ statusNames[effectResult.status] }}</b><p>{{ effectResult.route_mode }} · {{ effectResult.version_no==null?'未发布配置':`V${effectResult.version_no}` }}</p></div><span>{{ effectResult.latency_ms }} ms</span></article>
        <div class="result-grid">
          <article v-for="key in ['recall','reranker','final']" :key="key" class="panel stack"><h4>{{ stageNames[key] }}</h4><p>{{ statusNames[stage(key)?.status] }} · {{ stage(key)?.latency_ms || 0 }} ms</p><p v-if="stage(key)?.error" class="error">{{ stage(key).error }}</p><p v-if="!candidates(stage(key)).length">{{ stage(key)?.data?.reason || '没有匹配结果。' }}</p><ol v-else><li v-for="item in candidates(stage(key))" :key="`${item.asset_version_id}:${item.source_knowledge_id}`"><b>{{ item.citation_id || item.source_knowledge_id }}</b><span>{{ item.content }}</span><small>Vector {{ item.vector_score }}<template v-if="item.rerank_score!=null"> · Rerank {{ item.rerank_score }}</template></small></li></ol></article>
        </div>
        <button type="button" @click="openTrace">查看本次链路</button>
      </template>
    </template>

    <template v-else-if="viewMode==='public'">
      <p class="notice">只测试当前环境已发布的 Public Retrieval v1；管理员测试不会把业务 Token 交给浏览器。</p>
      <form class="panel stack" @submit.prevent="runPublic">
        <div class="controls"><label>项目<input :value="projectCode||'—'" readonly></label><label>Deployment<input :value="deploymentCode||'—'" readonly></label><label>检索任务<select v-model="taskCode" required><option value="">选择已发布任务</option><option v-for="item in options.tasks" :key="item.task_code" :value="item.task_code">{{ item.task_name || item.task_code }}</option></select></label><label>组织授权<select v-model="orgCode" required><option value="">选择 org_code</option><option v-for="item in orgs" :key="item.org_code" :value="item.org_code">{{ item.org_name || item.org_code }}</option></select></label></div>
        <label>Query<textarea v-model="query" rows="3" maxlength="8192" required placeholder="输入业务系统要检索的问题"></textarea></label>
        <p v-if="unavailable" class="notice amber">{{ unavailable }}</p>
        <button class="primary" :disabled="busy||loading||!!unavailable||!taskCode||!orgCode||!query.trim()">{{ busy?'请求中…':'发送公共接口请求' }}</button>
      </form>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <template v-if="publicEnvelope">
        <article class="panel api-summary"><div><b>HTTP {{ publicEnvelope.response.status_code }}</b><p>request_id · <code>{{ publicEnvelope.response.request_id }}</code></p></div><div><b>{{ publicBody?.results?.length || 0 }} 条</b><p>{{ publicBody?.latency_ms || traceResult?.latency_ms || 0 }} ms · V{{ publicBody?.route?.route_version || '—' }}</p></div></article>
        <article v-if="publicBody?.results" class="panel stack"><h4>响应结果</h4><p v-if="!publicBody.results.length">没有匹配结果。</p><div v-else class="public-items"><section v-for="item in publicBody.results" :key="item.citation_id"><header><b>[{{ item.citation_id }}] {{ item.source_knowledge_id }}</b><span>{{ item.score.kind }} · {{ item.score.value }}</span></header><p class="content">{{ item.content }}</p><details v-if="item.evidence.length"><summary>引用依据 · {{ item.evidence.length }} 条</summary><p v-for="source in item.evidence" :key="source.source_chunk_id" class="content">{{ source.source_name }} · {{ source.evidence_text }}</p></details></section></div></article>
        <details class="panel raw-contract"><summary>原始 Request / Response / cURL</summary><h4>Request</h4><pre>{{ JSON.stringify(publicEnvelope.request,null,2) }}</pre><h4>Response</h4><pre>{{ JSON.stringify(publicEnvelope.response.body,null,2) }}</pre><div class="actions"><button type="button" @click="copyValue(publicPath,'接口 URL 已复制。')">复制 URL</button><button type="button" @click="copyValue(curlText,'cURL 已复制，Token 保持占位符。')">复制 cURL</button></div><p>{{ copyNotice }}</p></details>
        <button v-if="traceResult" type="button" class="primary" @click="openTrace">查看本次链路</button>
      </template>
    </template>

    <template v-else>
      <p v-if="institution" class="notice">中心不连接机构现场 Milvus；链路将在 Routing 后标记待本地执行。</p>
      <form v-if="!traceResult" class="panel stack" @submit.prevent="runEffect(true)">
        <p>尚无可查看的 Trace。可先从“检索效果”或“公共接口”进入，也可以直接执行一次技术调试。</p>
        <div class="controls"><label>对象<select v-model="routeMode"><option value="draft">当前配置</option><option value="published">当前已发布</option><option value="historical">历史版本</option></select></label><label v-if="routeMode==='historical'">版本<select v-model="version"><option value="">选择版本</option><option v-for="item in options.versions" :key="item.id" :value="item.version_no">V{{ item.version_no }}</option></select></label><label>任务<select v-model="taskCode"><option v-for="item in options.tasks" :key="item.task_code" :value="item.task_code">{{ item.task_name }}</option></select></label><label>org_code<select v-model="orgCode"><option v-for="item in orgs" :key="item.org_code" :value="item.org_code">{{ item.org_code }}</option></select></label></div><label>Query<textarea v-model="query" rows="3" required></textarea></label><button class="primary" :disabled="busy||!taskCode||!orgCode||!query.trim()">执行链路调试</button>
      </form>
      <template v-else>
        <article class="panel trace-summary"><div><b>请求 {{ traceResult.request_id || '管理员调试' }}</b><p>{{ traceResult.route_mode }} · {{ traceResult.version_no==null?'当前配置':`V${traceResult.version_no}` }}</p></div><span :class="['badge',traceResult.status==='completed'?'green':'red']">{{ statusNames[traceResult.status] }} · {{ traceResult.latency_ms }} ms</span></article>
        <div class="trace-list"><details v-for="(item,index) in traceStages" :key="item.key" class="trace-step" :open="item.status==='failed'"><summary><span :class="['trace-dot',item.status]"></span><b>{{ index+1 }}. {{ stageNames[item.key] }}</b><span>{{ statusNames[item.status] }} · {{ item.latency_ms }} ms</span></summary><p v-if="item.error" class="error">{{ item.error }}</p><template v-if="item.key==='routing'&&item.data.project"><p>{{ item.data.project.name }} → {{ item.data.deployment.name }} → {{ item.data.task_code }} → {{ item.data.org_code }}</p><p>授权知识库 {{ item.data.libraries?.length || 0 }} 个</p></template><template v-if="item.key==='recall'"><p>{{ item.data.metric_type || '—' }} · 候选 {{ item.data.candidates?.length || 0 }} 条</p><div v-for="candidate in item.data.candidates || []" :key="candidate.source_knowledge_id"><code>{{ candidate.knowledge_library_id }} · Asset V{{ candidate.asset_version_no }} · {{ candidate.partition_name }}</code></div></template><p v-if="item.data.reason">{{ item.data.reason }}</p><details class="technical-details"><summary>原始阶段数据</summary><pre>{{ JSON.stringify(item.data,null,2) }}</pre></details></details></div>
        <button type="button" @click="clearTrace">清除 Trace</button>
      </template>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
    </template>
  </section>
</template>

<style scoped>
.retrieval-validation{display:grid;gap:16px}.validation-tabs{display:inline-flex;width:max-content;gap:3px;padding:3px;border:1px solid var(--border);border-radius:10px;background:#eef2f7}.validation-tabs button{border:0;background:transparent}.validation-tabs button.active{color:var(--blue);background:#fff}.controls{display:flex;flex-wrap:wrap;gap:14px;align-items:end}.controls label{display:grid;flex:1;min-width:170px;gap:6px}.advanced-box,.raw-contract{padding:14px;border:1px solid var(--border);border-radius:9px;background:#fbfcfe}.experiment{margin-top:12px;padding:12px;background:var(--amber-soft)}.result-summary,.api-summary,.trace-summary{display:flex;justify-content:space-between;gap:16px;align-items:center}.result-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.result-grid ol{display:grid;gap:10px;margin:0;padding-left:20px}.result-grid li span,.result-grid li small{display:block;margin-top:4px}.public-items{display:grid;gap:12px}.public-items>section{padding:14px;border:1px solid var(--border);border-radius:9px}.public-items header{display:flex;justify-content:space-between;gap:12px}.trace-list{display:grid;gap:10px}.trace-step{padding:0 14px;border:1px solid var(--border);border-radius:10px;background:#fff}.trace-step>summary{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;padding:14px 0;cursor:pointer}.trace-dot{width:10px;height:10px;border-radius:50%;background:#9aa6b6}.trace-dot.completed{background:var(--green)}.trace-dot.failed{background:var(--red)}.trace-dot.blocked{background:var(--amber)}.content,pre{white-space:pre-wrap;overflow-wrap:anywhere}.notice.amber{padding:10px;border-radius:8px;background:var(--amber-soft)}.technical-details{margin:10px 0}.actions{display:flex;gap:8px}@media(max-width:1100px){.result-grid{grid-template-columns:1fr}}
</style>
