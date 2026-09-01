<script setup>
import { ref, watch } from 'vue'
import { api } from '../../api/platform'
import { subflowPrimaryName } from './flowModel'
const props = defineProps({ item: { type: Object, required: true } })
const emit = defineEmits(['close'])
const data = ref(null), error = ref(''), pending = ref(false)
let request = 0
async function load() {
  const current = ++request
  data.value = null; error.value = ''; pending.value = true
  try { const value = await api.flowSubgraphReferences(props.item.id, props.item.revision); if (current === request) data.value = value }
  catch (e) { if (current === request) error.value = e.message }
  finally { if (current === request) pending.value = false }
}
watch(() => `${props.item.id}:${props.item.revision}`, load, { immediate: true })
const label = row => `${row.authoring_mode === 'standard' ? 'Standard' : 'Advanced'} · ${row.is_builtin ? '内置' : '自定义'}`
</script>
<template>
  <section class="references panel" aria-label="子流程引用">
    <header><h3>{{ subflowPrimaryName(item) }} · r{{ item.revision }} 的引用</h3><button @click="emit('close')">关闭</button></header>
    <p v-if="pending" role="status">正在加载引用…</p><p v-else-if="error" role="alert">{{ error }} <button @click="load">重试</button></p>
    <template v-else-if="data"><p>被 {{ data.reference_count }} 个流程引用 · 按流程去重，包含当前草稿及最新发布版</p>
      <table v-if="data.references.length"><thead><tr><th>流程</th><th>类型</th><th>版本</th><th>引用位置</th></tr></thead><tbody><tr v-for="(row, index) in data.references" :key="index"><td>{{ row.template_name }}</td><td>{{ label(row) }}</td><td>r{{ row.template_revision }} · {{ row.revision_status === 'draft' ? '草稿' : '已发布' }}</td><td>{{ row.indirect ? '间接' : '直接' }} · {{ row.node_path.join(' → ') }}</td></tr></tbody></table>
      <p v-else>当前 Revision 尚未被流程引用。</p>
      <details v-if="data.unlocked_references.length"><summary>版本未锁定（不计入当前 Revision）</summary><p v-for="(row,index) in data.unlocked_references" :key="index">{{ row.template_name }} · r{{ row.template_revision }} · {{ row.node_path.join(' → ') }}</p></details>
    </template>
  </section>
</template>
<style scoped>.references{margin:14px 0;padding:18px;overflow:auto}.references header{display:flex;align-items:center;justify-content:space-between;gap:20px}.references h3{margin:0;font-size:20px}.references table{width:100%;text-align:left;border-collapse:collapse;font-size:14px}.references th,.references td{padding:10px;border-bottom:1px solid #e2e8f0}.references p{color:#65748b;line-height:1.6}.references summary{cursor:pointer;color:#99631a}</style>
