<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { api } from '../../api/platform'
const emit = defineEmits(['published'])
const plugins = ref([]), manifest = ref(''), error = ref(''), open = ref(false), busy = ref(false), reports = ref({})
let timer, disposed = false
const key = item => `${item.code}:${item.version}`
async function load() { try { plugins.value = await api.operatorPlugins() } catch (e) { error.value = e.message } }
async function register() {
  error.value = ''; busy.value = true
  try { await api.registerOperatorPlugin(JSON.parse(manifest.value)); manifest.value = ''; open.value = false; await load() }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function poll() {
  if (disposed) return
  for (const [id, value] of Object.entries(reports.value)) {
    if (!['queued', 'running'].includes(value.status)) continue
    try { const result = await api.operatorValidation(value.id); if (!disposed) reports.value[id] = result }
    catch (e) { error.value = e.message; reports.value[id] = { ...value, status: 'unknown' } }
  }
  if (!disposed && Object.values(reports.value).some(value => ['queued', 'running'].includes(value.status))) timer = setTimeout(poll, 1500)
}
async function validate(item) {
  error.value = ''
  try { reports.value[key(item)] = await api.validateOperatorPlugin(item.code, item.version); clearTimeout(timer); await poll() }
  catch (e) { error.value = e.message }
}
async function publish(item) {
  error.value = ''
  try { await api.publishOperatorPlugin(item.code, item.version); await load(); emit('published') }
  catch (e) { error.value = e.message }
}
onMounted(load)
onBeforeUnmount(() => { disposed = true; clearTimeout(timer) })
</script>
<template>
  <section class="plugin-manager panel">
    <header><h3>扩展算子</h3><button @click="open = !open">注册 Manifest</button></header>
    <p>仅注册维护人员已安装、已审核的包。验证调用真实插件代码，模型使用 Manifest 中的样例响应；不会写入正式知识。不接收 Python 源码。</p>
    <form v-if="open" @submit.prevent="register"><label>JSON Manifest<textarea v-model="manifest" aria-label="JSON Manifest" rows="12" required spellcheck="false" /></label><button :disabled="busy">{{ busy ? '正在注册…' : '注册草稿版本' }}</button></form>
    <article v-for="item in plugins" :key="key(item)">
      <div><b>{{ item.display_name_zh }}</b><small>{{ item.code }} · v{{ item.version }} · {{ item.version_status }} · Custom</small></div>
      <button :disabled="['queued', 'running'].includes(reports[key(item)]?.status)" @click="validate(item)">运行验证</button>
      <button v-if="item.version_status !== 'published'" :disabled="reports[key(item)]?.status !== 'passed'" @click="publish(item)">审核并发布</button>
      <details v-if="reports[key(item)]"><summary>验证：{{ reports[key(item)].status }}</summary><pre>{{ JSON.stringify(reports[key(item)].report || {}, null, 2) }}</pre></details>
    </article>
    <p v-if="!plugins.length">尚无扩展算子。</p><p v-if="error" role="alert" class="error">{{ error }}</p>
  </section>
</template>
<style scoped>
.plugin-manager{padding:18px}.plugin-manager header,.plugin-manager article{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.plugin-manager header{justify-content:space-between}.plugin-manager p{color:#64748b;line-height:1.6}.plugin-manager article{border-top:1px solid #e2e8f0;padding:14px 0}.plugin-manager article>div{flex:1}.plugin-manager small{display:block;color:#64748b;margin-top:5px}.plugin-manager label{display:grid;gap:8px}.plugin-manager textarea{width:100%;font-family:monospace}.plugin-manager form{display:grid;gap:12px}.plugin-manager details{flex-basis:100%}.plugin-manager pre{max-height:260px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}.plugin-manager .error{color:#b42318}
</style>
