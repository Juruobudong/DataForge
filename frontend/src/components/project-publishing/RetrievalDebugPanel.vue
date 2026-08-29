<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../../api/platform'
const props = defineProps({ deploymentId: String, releaseStage: String, institution: Boolean })
const mode = ref('draft'), version = ref(''), taskCode = ref(''), orgCode = ref(''), query = ref('')
const options = ref({ tasks: [], versions: [], rerankers: [] }), result = ref(null), error = ref(''), busy = ref(false), loading = ref(false)
const experiment = ref(false), topK = ref(10), finalTopK = ref(5), reranker = ref(null), filters = ref([]), copyNotice = ref('')
let optionsEpoch = 0, runEpoch = 0
const task = computed(() => options.value.tasks.find(item => item.task_code === taskCode.value))
const orgs = computed(() => task.value?.org_routes || [])
const fields = computed(() => task.value?.filter_fields || {})
const stageNames = { routing: '① Routing Resolution', embedding: '② Query Embedding', recall: '③ Vector Recall', reranker: '④ Reranker', final: '⑤ Final Results', context: '⑥ Context Preview', evidence: '⑦ Citation / Evidence' }
const statusNames = { completed: '完成', failed: '失败', skipped: '跳过', blocked: '待本地执行' }
function invalidate() { runEpoch++; result.value = null; busy.value = false; error.value = ''; copyNotice.value = '' }
async function loadOptions() {
  const epoch = ++optionsEpoch
  invalidate(); loading.value = true
  const priorVersions = options.value.versions
  options.value = { tasks: [], versions: priorVersions, rerankers: [] }
  taskCode.value = ''; orgCode.value = ''; filters.value = []; experiment.value = false
  if (!props.deploymentId || mode.value === 'historical' && !version.value) { loading.value = false; return }
  try {
    const params = { release_stage: props.releaseStage, route_mode: mode.value }
    if (mode.value === 'historical') params.version_no = version.value
    const response = await api.retrievalDebugOptions(props.deploymentId, params)
    if (epoch !== optionsEpoch) return
    options.value = response
    taskCode.value = response.tasks[0]?.task_code || ''
  } catch (e) { if (epoch === optionsEpoch) error.value = e.message }
  finally { if (epoch === optionsEpoch) loading.value = false }
}
watch(() => [props.deploymentId, props.releaseStage], () => {
  mode.value = 'draft'; version.value = ''; options.value = { tasks: [], versions: [], rerankers: [] }; loadOptions()
}, { immediate: true })
watch([mode, version], loadOptions)
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
    const body = { release_stage: props.releaseStage, route_mode: mode.value, task_code: taskCode.value, org_code: orgCode.value, query: query.value, filters: filters.value.map(filterPayload) }
    if (mode.value === 'historical') body.version_no = Number(version.value)
    if (experiment.value) body.overrides = { top_k: topK.value, final_top_k: finalTopK.value, reranker_serving_code: reranker.value }
    const response = await api.retrievalDebug(props.deploymentId, body)
    if (epoch === runEpoch) result.value = response
  } catch (e) { if (epoch === runEpoch) error.value = e.message }
  finally { if (epoch === runEpoch) busy.value = false }
}
async function copyContext(text) {
  try { await navigator.clipboard.writeText(text); copyNotice.value = '上下文已复制。' }
  catch { copyNotice.value = '无法访问剪贴板，请手动选择并复制上下文。' }
}
</script>
<template>
  <section class="retrieval-debug stack">
    <header><h3>检索调试</h3><p>真实验证 DataForge 检索链路，不生成回答。消费端仍按现有方式检索，不代表它已启用 Reranker。</p></header>
    <p v-if="institution" class="notice">中心不连接机构现场 Milvus；此处只解析 Routing，完整检索需在机构本地执行。</p>
    <form class="panel stack" @submit.prevent="run">
      <div class="controls">
        <label>Routing 版本<select v-model="mode"><option value="draft">Draft candidate（已保存草稿）</option><option value="published">Published current（当前发布）</option><option value="historical">Historical（历史版本）</option></select></label>
        <label v-if="mode==='historical'">历史版本<select v-model="version" required><option value="">选择版本</option><option v-for="item in options.versions.filter(v=>['published','frozen'].includes(v.status))" :key="item.id" :value="item.version_no">V{{ item.version_no }} · {{ item.status }}</option></select></label>
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
      <div v-if="experiment" class="controls experiment">
        <label>召回候选数<input v-model.number="topK" type="number" min="1" max="200" required></label><label>最终 TopK<input v-model.number="finalTopK" type="number" min="1" :max="topK" required></label>
        <label>Reranker<select v-model="reranker"><option :value="null">关闭重排</option><option v-for="item in options.rerankers" :key="item.id" :disabled="!item.is_enabled" :value="item.serving_code">{{ item.name }} · {{ item.model_name }}</option></select></label>
      </div>
      <p v-else-if="task">版本配置：召回 {{ task.top_k }} · 最终 {{ task.final_top_k }} · {{ task.reranker_serving_code || '未启用重排' }}</p>
      <button class="primary" :disabled="busy || loading || !taskCode || !orgCode || !query.trim()">{{ busy ? '检索中…' : '执行检索调试' }}</button>
      <p v-if="!loading&&!options.tasks.length&&!error" class="muted">该版本暂无可调试的任务或授权。</p>
    </form>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <template v-if="result">
      <div class="panel" role="status"><b>{{ result.experimental ? '本次实验' : '版本配置验证' }} · {{ statusNames[result.status] }}</b><p>{{ releaseStage }} · {{ result.route_mode }} · {{ result.version_no == null ? '未发布草稿' : `V${result.version_no}` }} · {{ result.latency_ms }} ms</p><code>{{ result.checksum }}</code><p>{{ result.notice }}</p><p>原配置：{{ result.baseline }}<br>实际配置：{{ result.effective }}</p></div>
      <article v-for="stage in result.stages" :key="stage.key" class="panel stack" :class="stage.status">
        <header class="stage-head"><h4>{{ stageNames[stage.key] }}</h4><span>{{ statusNames[stage.status] }} · {{ stage.latency_ms }} ms</span></header>
        <p v-if="stage.error" role="alert" class="error">{{ stage.error }}</p><p v-if="stage.data.reason">{{ stage.data.reason }}</p>
        <template v-if="stage.key==='routing'&&stage.data.libraries"><p>{{ stage.data.project.name }} → {{ stage.data.deployment.name }} → {{ stage.data.org_code }} → {{ stage.data.task_code }}</p><div v-for="library in stage.data.libraries" :key="library.knowledge_library_id"><code>{{ library.knowledge_library_id }}</code> → AssetVersion V{{ library.asset_version_no }} → <code>{{ library.partition_name }}</code></div></template>
        <p v-if="stage.key==='embedding'&&stage.data.serving_code">Serving：{{ stage.data.serving_code }} · {{ stage.data.model_name }} · 配置 {{ stage.data.expected_dimension }} / 实际 {{ stage.data.observed_dimension }} 维</p>
        <p v-if="stage.key==='recall'&&stage.data.metric_type">{{ stage.data.metric_type }} · {{ stage.data.score_direction==='ascending'?'分数越小越靠前':'分数越大越靠前' }}</p>
        <p v-if="stage.key==='reranker'&&stage.data.model_name">{{ stage.data.model_name }} · {{ stage.data.batch_count }} 批</p>
        <p v-if="stage.key==='final'&&stage.status==='completed'">TopK = {{ stage.data.top_k }} · 实际 {{ stage.data.count }} 条</p>
        <template v-if="['recall','reranker','final'].includes(stage.key)&&stage.status==='completed'">
          <p v-if="!(stage.data.candidates || stage.data.results || []).length">没有匹配结果。</p>
          <div v-else class="table-wrap"><table><thead><tr><th>候选 / 引用</th><th>排名变化</th><th>Vector score</th><th>Rerank score</th><th>正文与来源</th></tr></thead><tbody><tr v-for="item in (stage.data.candidates || stage.data.results || [])" :key="`${item.asset_version_id}:${item.source_knowledge_id}`"><td>{{ item.citation_id || item.source_knowledge_id }}</td><td>{{ item.vector_rank }}<template v-if="item.rerank_rank"> → {{ item.rerank_rank }}</template></td><td>{{ item.vector_score }}</td><td>{{ item.rerank_score ?? '—' }}</td><td><details><summary>{{ item.content.slice(0,100) }}</summary><p class="content">{{ item.content }}</p><code>{{ item.knowledge_library_id }} · V{{ item.asset_version_no }} · {{ item.partition_name }}</code></details></td></tr></tbody></table></div>
        </template>
        <template v-if="stage.key==='context'&&stage.status==='completed'"><p v-if="stage.data.truncated">预览已截断为 32,000 字符，原文 {{ stage.data.total_characters }} 字符。</p><button type="button" @click="copyContext(stage.data.text)">复制上下文</button><pre>{{ stage.data.text }}</pre><p role="status">{{ copyNotice }}</p></template>
        <template v-if="stage.key==='evidence'&&stage.status==='completed'"><div v-for="citation in stage.data.citations" :key="citation.citation_id"><h5>[{{ citation.citation_id }}] {{ citation.source_knowledge_id }}</h5><div v-for="(source,index) in citation.sources" :key="index"><p>{{ source.source_name }} · 源版本 V{{ source.source_version_no }}</p><p class="content">{{ source.evidence_text }}</p><details><summary>Anchor / 审核与分块版本</summary><pre>{{ source }}</pre></details></div></div></template>
      </article>
    </template>
  </section>
</template>
<style scoped>
.retrieval-debug{display:grid;gap:16px}.controls{display:flex;flex-wrap:wrap;gap:16px;align-items:end}.controls label{flex:1;min-width:160px}.stage-head{display:flex;justify-content:space-between;align-items:center}.stage-head h4{margin:0}.experiment{padding:16px;background:#fff8e6;border:1px solid #e7c979;border-radius:8px}.failed{border-color:#dc6464}.content,pre{white-space:pre-wrap;overflow-wrap:anywhere}pre{max-height:440px;overflow:auto}.table-wrap{overflow:auto}td{vertical-align:top}code{overflow-wrap:anywhere}.notice{color:#805c16}
</style>
