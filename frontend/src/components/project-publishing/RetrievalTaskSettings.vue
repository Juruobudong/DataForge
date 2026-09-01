<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/platform'
const props = defineProps({ deploymentId: String, task: { type: Object, default: null }, tasks: { type: Array, default: () => [] } })
const emit = defineEmits(['saved'])
const taskId = ref(''), rerankers = ref([]), form = ref({}), error = ref(''), notice = ref(''), busy = ref(false)
const task = computed(() => props.task || props.tasks.find(item => item.id === taskId.value))
watch(() => props.deploymentId, () => { taskId.value = ''; notice.value = ''; error.value = '' })
watch(() => props.task?.id, () => { notice.value = ''; error.value = '' })
watch(task, value => { form.value = value ? { top_k: value.top_k, final_top_k: value.final_top_k, reranker_serving_code: value.reranker_serving_code ?? null, enabled: value.enabled } : {} }, { immediate: true })
onMounted(async () => { try { rerankers.value = await api.rerankerServings() } catch (e) { error.value = e.message } })
async function save() {
    const id = props.deploymentId, selected = task.value?.id || taskId.value
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await api.patchDeploymentTask(id, selected, { ...form.value })
    if (id !== props.deploymentId || selected !== (task.value?.id || taskId.value)) return
    notice.value = '检索配置草稿已保存，尚未发布。'; emit('saved')
  } catch (e) { if (id === props.deploymentId) error.value = e.message }
  finally { busy.value = false }
}
</script>
<template>
  <form class="stack" @submit.prevent="save">
    <h4>检索策略</h4><p>保存为当前发布配置草稿，不会自动发布。</p>
    <label v-if="!props.task">检索任务<select v-model="taskId" :disabled="busy"><option value="">选择任务</option><option v-for="item in tasks" :key="item.id" :value="item.id">{{ item.task?.name || item.id }}</option></select></label>
    <template v-if="task">
      <label>召回候选数<input v-model.number="form.top_k" type="number" min="1" max="200" required></label>
      <label>最终 TopK<input v-model.number="form.final_top_k" type="number" min="1" :max="form.top_k" required></label>
      <label>Reranker<select v-model="form.reranker_serving_code"><option :value="null">关闭重排</option><option v-for="item in rerankers" :key="item.id" :value="item.serving_code" :disabled="!item.is_enabled">{{ item.name }} · {{ item.model_name }}</option></select></label>
      <label><input v-model="form.enabled" type="checkbox">启用通道</label><button class="primary" :disabled="busy">保存配置草稿</button>
    </template>
    <p v-if="notice" role="status">{{ notice }}</p><p v-if="error" role="alert">{{ error }}</p>
  </form>
</template>
