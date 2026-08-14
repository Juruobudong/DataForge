<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const jobs = ref([]), selectedJobs = ref([])
const error = ref(''), logs = ref([]), generations = ref([]), logJobId = ref(''), logLoading = ref(false), logError = ref('')
let refreshTimer = null, loading = false, disposed = false

async function load() {
  if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null }
  if (loading) return
  loading = true
  try { jobs.value = await api.knowledgeJobs() } catch (e) { error.value = e.message } finally { loading = false; scheduleRefresh() }
}
function scheduleRefresh() {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = !disposed && jobs.value.some(job => ['queued', 'running'].includes(job.status)) ? setTimeout(load, 3000) : null
}
async function action(action, ids = selectedJobs.value) {
  if (!ids.length) return
  try {
    await api.manageKnowledgeJobs({ job_ids: ids, action })
    selectedJobs.value = []
    await load()
  } catch (e) { error.value = e.message }
}
async function showLogs(id) {
  if (logJobId.value === id) { logJobId.value = ''; return }
  logJobId.value = id; logs.value = []; generations.value = []; logError.value = ''; logLoading.value = true
  try { [logs.value, generations.value] = await Promise.all([api.knowledgeJobLogs(id), api.knowledgeJobChunkGenerations(id, true)]) } catch (e) { logError.value = e.message } finally { logLoading.value = false }
}

onMounted(load)
onBeforeUnmount(() => { disposed = true; if (refreshTimer) clearTimeout(refreshTimer) })
</script>

<template>
  <section>
    <div class="page-head"><div><h2>处理任务</h2><p>任务由文档库的模板绑定自动创建；本页只用于监控、查看日志、停止、重试和删除未产出知识的任务。存在警告的任务只会重试失败分块。</p></div><div class="page-actions"><button @click="action('retry')">重试失败分块</button><button class="danger" @click="action('delete')">批量删除</button></div></div>
    <div class="actions"><button class="danger" @click="action('cancel')">批量停止</button><span class="badge amber">选择任务后执行批量操作</span></div>
    <p v-if="error" class="error">{{ error }}</p>
    <table><thead><tr><th>选择</th><th>任务</th><th>目标知识库</th><th>当前阶段</th><th>进度</th><th>状态</th><th>操作</th></tr></thead><tbody><template v-for="job in jobs" :key="job.id"><tr><td><input v-model="selectedJobs" type="checkbox" :value="job.id"></td><td><b>{{ job.id }}</b><small>{{ job.created_at }}</small></td><td>{{ Object.values(job.sink_library_ids || job.output_library_ids).join('、') }}</td><td>{{ job.stage }}</td><td><div class="job-progress" :aria-label="`任务进度 ${job.progress?.percent || 0}%`"><i :style="{ width: `${job.progress?.percent || 0}%` }"></i></div><small>{{ job.progress?.percent || 0 }}%<template v-if="job.progress?.total_nodes"> · {{ job.progress.completed_nodes }}/{{ job.progress.total_nodes }} 节点</template></small></td><td><span class="badge" :class="job.status === 'completed' ? 'green' : job.status === 'failed' ? 'red' : 'amber'">{{ job.status }}</span><small v-if="job.failed_chunk_count">{{ job.failed_chunk_count }} 个分块失败，可重试</small></td><td><button :aria-expanded="logJobId === job.id" @click="showLogs(job.id)">{{ logJobId === job.id ? '收起' : '日志' }}</button></td></tr><tr v-if="logJobId === job.id" class="job-log-row"><td colspan="7"><div class="job-log-panel"><h4>任务日志：{{ job.id }}</h4><p v-if="logLoading">正在加载日志…</p><p v-else-if="logError" class="error">{{ logError }}</p><template v-else><pre v-if="logs.length">{{ JSON.stringify(logs, null, 2) }}</pre><p v-else>暂无审计日志。</p><section v-if="generations.length"><h4>失败分块</h4><table><thead><tr><th>知识类型</th><th>来源版本</th><th>分块</th><th>尝试</th><th>错误</th></tr></thead><tbody><tr v-for="item in generations" :key="item.id"><td>{{ item.knowledge_type }}</td><td>{{ item.source_version_id }}</td><td>#{{ item.chunk_index }}</td><td>{{ item.attempt_count }}</td><td>{{ item.error }}</td></tr></tbody></table></section></template></div></td></tr></template></tbody></table>
  </section>
</template>

<style scoped>
.job-progress { width: 150px; max-width: 100%; height: 7px; overflow: hidden; margin-bottom: 5px; border-radius: 999px; background: #e8edf5; }
.job-progress i { display: block; height: 100%; border-radius: inherit; background: var(--blue); transition: width .25s ease; }
.job-log-row > td { padding: 0 12px 14px; background: var(--panel-muted); }
.job-log-panel { padding: 14px; border: 1px solid var(--border); border-radius: 10px; background: #fff; }
.job-log-panel h4 { margin: 0 0 10px; }
.job-log-panel pre { max-height: 320px; overflow: auto; margin: 0 0 14px; }
</style>
