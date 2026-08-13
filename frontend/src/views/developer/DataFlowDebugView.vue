<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const pipelines = ref([])
const profiles = ref([])
const capacity = ref([])
const managedCollections = ref([])
const jobs = ref([])
const flowRuns = ref([])
const selectedRun = ref(null)
const runDetail = ref(null)
const error = ref('')
const loading = ref(false)
const capacityByCollection = computed(() => Object.fromEntries(capacity.value.map(item => [item.collection_name, item])))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [pipelineData, vectorData, jobData, runData] = await Promise.all([
      api.standardPipelines(), api.vectorIndexes(), api.knowledgeJobs(), api.flowRuns(),
    ])
    pipelines.value = pipelineData
    profiles.value = vectorData.profiles || []
    managedCollections.value = vectorData.managed_collections || []
    capacity.value = vectorData.capacity || []
    jobs.value = jobData.slice(0, 10)
    flowRuns.value = runData
  } catch (e) { error.value = e.message } finally { loading.value = false }
}

async function inspectRun(id) {
  try { selectedRun.value = id; runDetail.value = await api.flowRun(id) } catch (e) { error.value = e.message }
}

onMounted(load)
</script>

<template>
  <section>
    <div class="page-head">
      <div><h2>DataFlow 调试台</h2><p>只读查看编译快照、展开 DAG、节点状态、Artifact 血缘、质量 Gate 与 Sink 结果；不提供 DataFlow WebUI 或代码编辑。</p></div>
      <div class="page-actions"><span class="badge blue">V7 受控诊断</span><button :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button></div>
    </div>
    <section class="panel">
      <div class="panel-head"><div><h3>Storage Contract / Managed Collection</h3><p>结构哈希一致才允许共用；Triple 与 Semantic 使用两个专属 Collection。</p></div><span class="badge blue">受管资源</span></div>
      <table><thead><tr><th>Contract</th><th>Collection</th><th>规格</th><th>哈希</th><th>状态</th></tr></thead><tbody><tr v-for="item in managedCollections" :key="item.id"><td>{{ item.storage_contract.code }} · r{{ item.storage_contract.revision }}</td><td>{{ item.collection_name }}</td><td>{{ item.storage_contract.dimension }} / {{ item.storage_contract.metric_type }}</td><td><code>{{ item.desired_spec_hash.slice(0,12) }}</code></td><td><span class="badge" :class="item.status==='ready'?'green':item.status==='incompatible'||item.status==='failed'?'red':'amber'">{{ item.status }}</span><small v-if="item.error">{{ item.error }}</small></td></tr></tbody></table>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h3>标准流水线</h3><p>发布时会将子图展开为可审计的确定性拓扑顺序。</p></div><span class="badge green">只读</span></div>
      <div class="flow" v-for="pipeline in pipelines" :key="pipeline.code"><b>{{ pipeline.code }}</b><span class="arrow">→</span><span>{{ pipeline.steps.join(' → ') }}</span></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h3>已发布 Vector Index Profile</h3><p>已发布 Collection 与知识库 <code>kl_</code> Partition 的运行摘要。</p></div><span class="badge blue">动态配置</span></div>
      <table><thead><tr><th>Profile</th><th>Collection</th><th>知识类型</th><th>容量</th><th>状态</th></tr></thead><tbody><tr v-for="profile in profiles" :key="profile.id"><td><b>{{ profile.code }}</b></td><td>{{ profile.collection_name }}</td><td>{{ profile.knowledge_type }}</td><td>{{ capacityByCollection[profile.collection_name]?.available ? `${capacityByCollection[profile.collection_name].entity_count} / ${capacityByCollection[profile.collection_name].capacity_limit}` : 'Milvus 未配置' }}</td><td><span class="badge" :class="profile.status === 'active' ? 'green' : 'amber'">{{ profile.status }}</span></td></tr></tbody></table>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h3>Flow Run 与 Artifact 血缘</h3><p>每个任务引用一个不可变 execution snapshot；Sink 失败只记录该分支并不会回滚其他已提交 Sink。</p></div><span class="badge blue">最近 50 项</span></div>
      <table><thead><tr><th>Run</th><th>Snapshot</th><th>状态</th><th>完成时间</th></tr></thead><tbody><tr v-for="run in flowRuns" :key="run.id" class="clickable" @click="inspectRun(run.id)"><td>{{ run.id }}</td><td>{{ run.execution_snapshot_id }}</td><td><span class="badge" :class="run.status === 'completed' ? 'green' : 'red'">{{ run.status }}</span></td><td>{{ run.completed_at || '运行中' }}</td></tr><tr v-if="!flowRuns.length"><td colspan="4">暂无 Flow Run。</td></tr></tbody></table>
      <div v-if="runDetail" class="run-detail"><h4>节点与产物：{{ selectedRun }}</h4><p v-if="runDetail.error" class="error">{{ runDetail.error }}</p><ul><li v-for="node in runDetail.nodes" :key="node.id"><b>{{ node.node_id }}</b> · {{ node.status }} · 输入 {{ node.input_artifact_ids.length }} / 输出 {{ node.output_artifact_ids.length }} <span v-if="node.error" class="error">{{ node.error }}</span></li></ul><p>Artifact 数量：{{ runDetail.artifacts.length }}</p></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h3>最近知识任务</h3><p>用于定位运行阶段与失败，不改变任务本身。</p></div><span class="badge amber">最近 10 项</span></div>
      <table><thead><tr><th>任务</th><th>阶段</th><th>状态</th><th>创建时间</th></tr></thead><tbody><tr v-for="job in jobs" :key="job.id"><td>{{ job.id }}</td><td>{{ job.stage }}</td><td><span class="badge" :class="job.status === 'completed' ? 'green' : job.status === 'failed' ? 'red' : 'amber'">{{ job.status }}</span></td><td>{{ job.created_at }}</td></tr><tr v-if="!jobs.length"><td colspan="4">暂无知识任务。</td></tr></tbody></table>
    </section>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.clickable{cursor:pointer}.run-detail{margin-top:14px;border-top:1px solid #e5e7eb;padding-top:12px}.run-detail ul{padding-left:20px}
</style>
