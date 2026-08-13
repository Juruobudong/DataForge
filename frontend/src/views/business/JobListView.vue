<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const jobs = ref([]), selectedJobs = ref([])
const error = ref(''), logs = ref([]), generations = ref([]), logJobId = ref('')

async function load() {
  jobs.value = await api.knowledgeJobs()
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
  try { logJobId.value = id; [logs.value, generations.value] = await Promise.all([api.knowledgeJobLogs(id), api.knowledgeJobChunkGenerations(id, true)]) } catch (e) { error.value = e.message }
}
function byType(type) { return libraries.value.filter(item => item.knowledge_type === type && item.status === 'active') }

onMounted(load)
</script>

<template>
  <section>
    <div class="page-head"><div><h2>处理任务</h2><p>任务由文档库的模板绑定自动创建；本页只用于监控、查看日志、停止、重试和删除未产出知识的任务。存在警告的任务只会重试失败分块。</p></div><div class="page-actions"><button @click="action('retry')">重试失败分块</button><button class="danger" @click="action('delete')">批量删除</button></div></div>
    <div class="actions"><button class="danger" @click="action('cancel')">批量停止</button><span class="badge amber">选择任务后执行批量操作</span></div>
    <p v-if="error" class="error">{{ error }}</p>
    <table><thead><tr><th>选择</th><th>任务</th><th>目标知识库</th><th>当前阶段</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="job in jobs" :key="job.id"><td><input v-model="selectedJobs" type="checkbox" :value="job.id"></td><td><b>{{ job.id }}</b><small>{{ job.created_at }}</small></td><td>{{ Object.values(job.sink_library_ids || job.output_library_ids).join('、') }}</td><td>{{ job.stage }}</td><td><span class="badge" :class="job.status === 'completed' ? 'green' : job.status === 'failed' ? 'red' : 'amber'">{{ job.status }}</span><small v-if="job.failed_chunk_count">{{ job.failed_chunk_count }} 个分块失败，可重试</small></td><td><button @click="showLogs(job.id)">日志</button></td></tr></tbody></table>
    <details v-if="logJobId"><summary>任务日志：{{ logJobId }}</summary><pre>{{ JSON.stringify(logs, null, 2) }}</pre><section v-if="generations.length"><h4>失败分块</h4><table><thead><tr><th>知识类型</th><th>来源版本</th><th>分块</th><th>尝试</th><th>错误</th></tr></thead><tbody><tr v-for="item in generations" :key="item.id"><td>{{ item.knowledge_type }}</td><td>{{ item.source_version_id }}</td><td>#{{ item.chunk_index }}</td><td>{{ item.attempt_count }}</td><td>{{ item.error }}</td></tr></tbody></table></section></details>
  </section>
</template>
