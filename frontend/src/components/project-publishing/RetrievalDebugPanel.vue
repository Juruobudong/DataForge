<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../../api/platform'

const props = defineProps({
  deploymentId: String, releaseStage: String, institution: Boolean,
  projectCode: String, deploymentCode: String,
})
const viewMode = ref('public'), routeMode = ref('draft'), version = ref(''), publicStage = ref(props.releaseStage || 'test')
const taskCode = ref(''), orgCode = ref(''), query = ref('')
const options = ref({ tasks: [], versions: [], rerankers: [] }), result = ref(null), error = ref('')
const busy = ref(false), loading = ref(false), publicUnavailable = ref('')
const experiment = ref(false), topK = ref(10), finalTopK = ref(5), reranker = ref(null), filters = ref([]), copyNotice = ref('')
let optionsEpoch = 0, runEpoch = 0

const task = computed(() => options.value.tasks.find(item => item.task_code === taskCode.value))
const orgs = computed(() => task.value?.org_routes || [])
const fields = computed(() => task.value?.filter_fields || {})
const activeStage = computed(() => viewMode.value === 'public' ? publicStage.value : props.releaseStage)
const publicPath = computed(() => {
  if (!props.projectCode || !props.deploymentCode || !taskCode.value) return ''
  return `/api/runtime/retrieval/v1/${encodeURIComponent(props.projectCode)}/${encodeURIComponent(props.deploymentCode)}/${publicStage.value}/${encodeURIComponent(taskCode.value)}/query`
})
const curlText = computed(() => publicPath.value ? [
  `curl -X POST "${globalThis.location?.origin || '<DATAFORGE_URL>'}${publicPath.value}"`,
  '-H "Authorization: Bearer <DATAFORGE_RETRIEVAL_TOKEN>"',
  '-H "Content-Type: application/json"',
  `-d '${JSON.stringify({ org_code: orgCode.value, query: query.value || '<QUERY>' })}'`,
].join(' \\\n+  ') : '')
const stageNames = { routing: '① Routing Resolution', embedding: '② Query Embedding', recall: '③ Vector Recall', reranker: '④ Reranker', final: '⑤ Final Results', context: '⑥ Context Preview', evidence: '⑦ Citation / Evidence' }
const statusNames = { completed: '完成', failed: '失败', skipped: '跳过', blocked: '待本地执行' }

function invalidate() { runEpoch++; result.value = null; busy.value = false; error.value = ''; copyNotice.value = '' }
async function loadOptions() {
  const epoch = ++optionsEpoch
  invalidate(); loading.value = true; publicUnavailable.value = ''
  const priorVersions = options.value.versions
  options.value = { tasks: [], versions: priorVersions, rerankers: [] }
  taskCode.value = ''; orgCode.value = ''; filters.value = []; experiment.value = false
  if (!props.deploymentId || viewMode.value === 'technical' && routeMode.value === 'historical' && !version.value) { loading.value = false; return }
  try {
    const params = {
      release_stage: activeStage.value,
      route_mode: viewMode.value === 'public' ? 'published' : routeMode.value,
    }
    if (viewMode.value === 'technical' && routeMode.value === 'historical') params.version_no = version.value
    const response = await api.retrievalDebugOptions(props.deploymentId, params)
    if (epoch !== optionsEpoch) return
    options.value = response
    taskCode.value = response.tasks[0]?.task_code || ''
    if (viewMode.value === 'public' && !response.tasks.length) publicUnavailable.value = '该环境的当前发布版本没有可检索任务或机构授权。'
  } catch (e) {
    if (epoch !== optionsEpoch) return
    if (viewMode.value === 'public') publicUnavailable.value = '该环境尚无可用的已发布检索版本。'
    else error.value = e.message
  } finally { if (epoch === optionsEpoch) loading.value = false }
}

watch(() => [props.deploymentId, props.releaseStage, props.projectCode, props.deploymentCode], () => {
  publicStage.value = props.releaseStage || 'test'; routeMode.value = 'draft'; version.value = ''
  options.value = { tasks: [], versions: [], rerankers: [] }; loadOptions()
}, { immediate: true })
watch([viewMode, routeMode, version, publicStage], loadOptions)
watch(task, value => {
  invalidate(); filters.value = []; experiment.value = false
  orgCode.value = value?.org_routes?.[0]?.org_code || ''
  topK.value = value?.top_k ?? 10; finalTopK.value = value?.final_top_k ?? 5; reranker.value = value?.reranker_serving_code ?? null
})
watch([orgCode, query, experiment, topK, finalTopK, reranker], invalidate)
watch(filters, invalidate, { deep: true })
onBeforeUnmount(() => { optionsEpoch++; runEpoch++ })

function addFilter() { filters.value.push({ field: Object.keys(fields.value)[0] || '', op: 'eq', value: '' }) }
function filterPayload(item) {
  const dtype = fields.value[item.field]
  let value = item.value
  if (item.op === 'in' || dtype !== 'VARCHAR') {
    try { value = JSON.parse(value) } catch { throw new Error('数值、布尔或集合过滤值必须使用合法 JSON。') }
  }
  return { field: item.field, op: item.op, value }
}
async function run() {
  const epoch = ++runEpoch
  error.value = ''; result.value = null; busy.value = true
  try {
    let response
    if (viewMode.value === 'public') {
      response = await api.retrievalPublicTest(props.deploymentId, {
        release_stage: publicStage.value, task_code: taskCode.value,
        org_code: orgCode.value, query: query.value,
      })
    } else {
      const body = { release_stage: props.releaseStage, route_mode: routeMode.value, task_code: taskCode.value, org_code: orgCode.value, query: query.value, filters: filters.value.map(filterPayload) }
      if (routeMode.value === 'historical') body.version_no = Number(version.value)
      if (experiment.value) body.overrides = { top_k: topK.value, final_top_k: finalTopK.value, reranker_serving_code: reranker.value }
      response = await api.retrievalDebug(props.deploymentId, body)
    }
    if (epoch === runEpoch) result.value = response
  } catch (e) { if (epoch === runEpoch) error.value = e.message }
  finally { if (epoch === runEpoch) busy.value = false }
}
async function copyValue(value, success) {
  try { await navigator.clipboard.writeText(value); copyNotice.value = success }
  catch { copyNotice.value = '无法访问剪贴板，请手动选择并复制。' }
}
</script>

<template>
  <section class="retrieval-debug stack">
    <header><h3>检索测试</h3><p>公共模式验证业务消费契约；技术模式用于定位 DataForge 内部检索阶段。</p></header>
    <nav class="view-switch" aria-label="检索测试模式">
      <button type="button" :aria-pressed="viewMode==='public'" :class="{active:viewMode==='public'}" @click="viewMode='public'">公共接口测试</button>
      <button type="button" :aria-pressed="viewMode==='technical'" :class="{active:viewMode==='technical'}" @click="viewMode='technical'">技术链路调试</button>
    </nav>

    <template v-if="viewMode==='public'">
      <p class="notice">浏览器使用管理员测试端点并返回与正式 API 相同的业务 DTO；外部 Bearer Token 请使用下方 curl 单独验收。</p>
      <form class="panel stack public-form" @submit.prevent="run">
        <div class="controls">
          <label>Project<input :value="projectCode || '—'" readonly></label>
          <label>Deployment<input :value="deploymentCode || '—'" readonly></label>
          <label>环境<select v-model="publicStage"><option value="test">测试环境 · test</option><option value="production">生产环境 · production</option></select></label>
          <label>任务<select v-model="taskCode" required :disabled="loading || !options.tasks.length"><option value="">选择已发布任务</option><option v-for="item in options.tasks" :key="item.task_code" :value="item.task_code">{{ item.task_name || item.task_code }} · {{ item.task_code }}</option></select></label>
          <label>机构<select v-model="orgCode" required :disabled="!orgs.length"><option value="">选择已发布授权</option><option v-for="item in orgs" :key="item.org_code" :value="item.org_code">{{ item.org_name || item.org_code }} · {{ item.org_code }}</option></select></label>
        </div>
        <p v-if="publicUnavailable" role="status" class="notice amber">{{ publicUnavailable }}</p>
        <section v-if="publicPath" class="endpoint-box">
          <small>正式公共接口</small><code>{{ publicPath }}</code>
          <div class="actions"><button type="button" @click="copyValue(publicPath,'公共接口 URL 已复制。')">复制 URL</button><button type="button" @click="copyValue(curlText,'curl 示例已复制，Token 保持占位符。')">复制 curl</button></div>
        </section>
        <label>Query<textarea v-model="query" rows="3" maxlength="8192" required placeholder="输入业务系统要检索的问题"></textarea></label>
        <button class="primary" :disabled="busy || loading || !!publicUnavailable || !taskCode || !orgCode || !query.trim()">{{ busy ? '检索中…' : '执行公共接口测试' }}</button>
        <p role="status" class="copy-notice">{{ copyNotice }}</p>
      </form>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <section v-if="result" class="public-result stack">
        <article class="panel"><div class="panel-head"><div><h4>公共检索完成</h4><p>{{ result.route.project_code }} / {{ result.route.deployment_code }} / {{ result.route.release_stage }} / {{ result.route.task_code }}</p></div><span class="badge green">V{{ result.route.route_version }} · {{ result.latency_ms }} ms</span></div><p>召回 {{ result.policy.top_k }} · 最终 {{ result.policy.final_top_k }} · {{ result.policy.reranker_enabled ? '已启用重排' : '未启用重排' }}</p></article>
        <article class="panel stack"><h4>检索结果 · {{ result.results.length }} 条</h4><p v-if="!result.results.length">没有匹配结果。</p><div v-else class="public-items"><section v-for="item in result.results" :key="item.citation_id"><header><b>[{{ item.citation_id }}] {{ item.source_knowledge_id }}</b><span>{{ item.score.kind }} · {{ item.score.value }}</span></header><p class="content">{{ item.content }}</p><details v-if="item.evidence.length"><summary>引用依据 · {{ item.evidence.length }} 条</summary><div v-for="source in item.evidence" :key="`${source.source_version_id}:${source.source_chunk_id}`"><b>{{ source.source_name }}</b><p class="content">{{ source.evidence_text }}</p></div></details></section></div></article>
        <article class="panel stack"><h4>Context</h4><p v-if="result.context.truncated">已截断，原文 {{ result.context.total_characters }} 字符。</p><button type="button" @click="copyValue(result.context.text,'上下文已复制。')">复制 Context</button><pre>{{ result.context.text }}</pre></article>
      </section>
    </template>

    <template v-else>
      <p v-if="institution" class="notice">中心不连接机构现场 Milvus；此处只解析 Routing，完整检索需在机构本地执行。</p>
      <form class="panel stack" @submit.prevent="run">
        <div class="controls">
          <label>Routing 版本<select v-model="routeMode"><option value="draft">Draft candidate（已保存草稿）</option><option value="published">Published current（当前发布）</option><option value="historical">Historical（历史版本）</option></select></label>
          <label v-if="routeMode==='historical'">历史版本<select v-model="version" required><option value="">选择版本</option><option v-for="item in options.versions.filter(v=>['published','frozen'].includes(v.status))" :key="item.id" :value="item.version_no">V{{ item.version_no }} · {{ item.status }}</option></select></label>
          <label>任务<select v-model="taskCode" required><option value="">选择任务</option><option v-for="item in options.tasks" :key="item.task_code" :value="item.task_code">{{ item.task_name || item.task_code }}</option></select></label>
          <label>机构 org_code<select v-model="orgCode" required><option value="">选择机构</option><option v-for="item in orgs" :key="item.org_code" :value="item.org_code">{{ item.org_name || item.org_code }} · {{ item.org_code }}</option></select></label>
        </div>
        <label>Query<textarea v-model="query" rows="3" maxlength="8192" required placeholder="输入要检查召回行为的查询"></textarea></label>
        <div v-for="(item,index) in filters" :key="index" class="controls">
          <label>过滤字段<select v-model="item.field"><option v-for="(dtype,name) in fields" :key="name" :value="name">{{ name }} · {{ dtype }}</option></select></label>
          <label>条件<select v-model="item.op"><option value="eq">等于</option><option value="in">属于集合</option><template v-if="!['VARCHAR','BOOL'].includes(fields[item.field])"><option value="gt">大于</option><option value="gte">大于等于</option><option value="lt">小于</option><option value="lte">小于等于</option></template></select></label>
          <label>值<input v-model="item.value" required placeholder='集合使用 JSON 数组，如 ["A","B"]'></label><button type="button" @click="filters.splice(index,1)">移除条件</button>
        </div>
        <button type="button" :disabled="!Object.keys(fields).length || filters.length>=32" @click="addFilter">添加过滤条件（AND）</button>
        <label><input v-model="experiment" type="checkbox">本次实验：临时覆盖检索参数，不保存配置</label>
        <div v-if="experiment" class="controls experiment"><label>召回候选数<input v-model.number="topK" type="number" min="1" max="200" required></label><label>最终 TopK<input v-model.number="finalTopK" type="number" min="1" :max="topK" required></label><label>Reranker<select v-model="reranker"><option :value="null">关闭重排</option><option v-for="item in options.rerankers" :key="item.id" :disabled="!item.is_enabled" :value="item.serving_code">{{ item.name }} · {{ item.model_name }}</option></select></label></div>
        <p v-else-if="task">版本配置：召回 {{ task.top_k }} · 最终 {{ task.final_top_k }} · {{ task.reranker_serving_code || '未启用重排' }}</p>
        <button class="primary" :disabled="busy || loading || !taskCode || !orgCode || !query.trim()">{{ busy ? '检索中…' : '执行技术链路调试' }}</button>
      </form>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <template v-if="result">
        <div class="panel" role="status"><b>{{ result.experimental ? '本次实验' : '版本配置验证' }} · {{ statusNames[result.status] }}</b><p>{{ releaseStage }} · {{ result.route_mode }} · {{ result.version_no == null ? '未发布草稿' : `V${result.version_no}` }} · {{ result.latency_ms }} ms</p><code>{{ result.checksum }}</code><p>{{ result.notice }}</p></div>
        <article v-for="stage in result.stages" :key="stage.key" class="panel stack" :class="stage.status">
          <header class="stage-head"><h4>{{ stageNames[stage.key] }}</h4><span>{{ statusNames[stage.status] }} · {{ stage.latency_ms }} ms</span></header>
          <p v-if="stage.error" role="alert" class="error">{{ stage.error }}</p><p v-if="stage.data.reason">{{ stage.data.reason }}</p>
          <template v-if="stage.key==='routing'&&stage.data.libraries"><p>{{ stage.data.project.name }} → {{ stage.data.deployment.name }} → {{ stage.data.org_code }} → {{ stage.data.task_code }} · {{ stage.data.libraries.length }} 个知识库</p><details class="technical-details"><summary>技术详情</summary><p>Milvus：<code>{{ stage.data.milvus_target?.milvus_url }}</code></p><div v-for="library in stage.data.libraries" :key="library.knowledge_library_id"><code>{{ library.knowledge_library_id }}</code> → AssetVersion V{{ library.asset_version_no }} → <code>{{ library.partition_name }}</code></div></details></template>
          <details v-if="stage.key==='embedding'&&stage.data.serving_code" class="technical-details"><summary>技术详情</summary><p>Serving：{{ stage.data.serving_code }} · {{ stage.data.model_name }} · 配置 {{ stage.data.expected_dimension }} / 实际 {{ stage.data.observed_dimension }} 维</p></details>
          <details v-if="stage.key==='recall'&&stage.data.metric_type" class="technical-details"><summary>技术详情</summary><p>{{ stage.data.metric_type }} · {{ stage.data.score_direction==='ascending'?'分数越小越靠前':'分数越大越靠前' }}</p></details>
          <p v-if="stage.key==='reranker'&&stage.data.model_name">{{ stage.data.model_name }} · {{ stage.data.batch_count }} 批</p>
          <p v-if="stage.key==='final'&&stage.status==='completed'">TopK = {{ stage.data.top_k }} · 实际 {{ stage.data.count }} 条</p>
          <template v-if="['recall','reranker','final'].includes(stage.key)&&stage.status==='completed'"><p v-if="!(stage.data.candidates || stage.data.results || []).length">没有匹配结果。</p><div v-else class="table-wrap"><table><thead><tr><th>候选 / 引用</th><th>排名变化</th><th>Vector score</th><th>Rerank score</th><th>正文与来源</th></tr></thead><tbody><tr v-for="item in (stage.data.candidates || stage.data.results || [])" :key="`${item.asset_version_id}:${item.source_knowledge_id}`"><td>{{ item.citation_id || item.source_knowledge_id }}</td><td>{{ item.vector_rank }}<template v-if="item.rerank_rank"> → {{ item.rerank_rank }}</template></td><td>{{ item.vector_score }}</td><td>{{ item.rerank_score ?? '—' }}</td><td><details><summary>{{ item.content.slice(0,100) }}</summary><p class="content">{{ item.content }}</p><details class="technical-details"><summary>技术详情</summary><code>{{ item.knowledge_library_id }} · V{{ item.asset_version_no }} · {{ item.partition_name }}</code></details></details></td></tr></tbody></table></div></template>
          <template v-if="stage.key==='context'&&stage.status==='completed'"><p v-if="stage.data.truncated">预览已截断为 32,000 字符，原文 {{ stage.data.total_characters }} 字符。</p><button type="button" @click="copyValue(stage.data.text,'上下文已复制。')">复制上下文</button><pre>{{ stage.data.text }}</pre></template>
          <template v-if="stage.key==='evidence'&&stage.status==='completed'"><div v-for="citation in stage.data.citations" :key="citation.citation_id"><h5>[{{ citation.citation_id }}] {{ citation.source_knowledge_id }}</h5><div v-for="(source,index) in citation.sources" :key="index"><p>{{ source.source_name }} · 源版本 V{{ source.source_version_no }}</p><p class="content">{{ source.evidence_text }}</p><details class="technical-details"><summary>技术详情</summary><pre>{{ source }}</pre></details></div></div></template>
        </article>
      </template>
    </template>
  </section>
</template>

<style scoped>
.retrieval-debug{display:grid;gap:16px}.view-switch{display:inline-flex;width:max-content;gap:3px;padding:3px;border:1px solid #dfe5ed;border-radius:9px;background:#eef2f7}.view-switch button{border:0;background:transparent}.view-switch button.active{color:#2f6fed;background:#fff}.controls{display:flex;flex-wrap:wrap;gap:16px;align-items:end}.controls label{flex:1;min-width:160px}.public-form .controls label{min-width:190px}.endpoint-box{display:grid;gap:8px;padding:14px;border:1px solid #dbe3ef;border-radius:9px;background:#f7f9fc}.endpoint-box code{overflow-wrap:anywhere}.actions{display:flex;gap:8px}.stage-head,.public-items header{display:flex;justify-content:space-between;gap:14px;align-items:center}.stage-head h4{margin:0}.experiment{padding:16px;background:#fff8e6;border:1px solid #e7c979;border-radius:8px}.failed{border-color:#dc6464}.content,pre{white-space:pre-wrap;overflow-wrap:anywhere}pre{max-height:440px;overflow:auto}.table-wrap{overflow:auto}td{vertical-align:top}code{overflow-wrap:anywhere}.notice{color:#805c16}.notice.amber{padding:10px;border-radius:8px;background:#fff8e6}.technical-details{margin-top:8px}.technical-details>summary{cursor:pointer;color:#536177;font-weight:700}.public-items{display:grid;gap:12px}.public-items>section{padding:14px;border:1px solid #e0e6ee;border-radius:9px}.copy-notice{min-height:20px;color:#1d8c65}
</style>
