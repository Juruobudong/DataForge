<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../../api/platform'

const props = defineProps({ definition: { type: Object, required: true }, selectedNodeId: { type: String, default: '' } })
const emit = defineEmits(['update:selectedNodeId'])
const names = { 'entity-extractor': '实体抽取器', 'relation-extractor': '关系抽取器', 'entity-relation-extractor': '实体关系联合抽取器' }
const nodes = computed(() => (props.definition.nodes || []).filter(node => node.kind === 'operator' && names[node.ref]))
const selected = ref(''), result = ref(null), error = ref(''), loading = ref(false)
watch(selected, value => emit('update:selectedNodeId', value))
let sequence = 0, timer
watch(() => props.selectedNodeId, value => { if (value) selected.value = value }, { immediate: true })
watch(nodes, values => { if (!values.some(node => node.id === selected.value)) selected.value = values[0]?.id || '' }, { immediate: true })
const signature = computed(() => JSON.stringify({ definition: props.definition, node_id: selected.value }))
async function fetchPreview(id) {
  try {
    const value = await api.previewGraphPrompt(JSON.parse(signature.value))
    if (id === sequence) result.value = value
  } catch (e) { if (id === sequence) error.value = e.message }
  finally { if (id === sequence) loading.value = false }
}
function refresh() {
  clearTimeout(timer)
  const id = ++sequence
  result.value = null; error.value = ''; loading.value = Boolean(selected.value)
  if (selected.value) timer = setTimeout(() => fetchPreview(id), 200)
}
watch(signature, refresh, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { clearTimeout(timer); sequence++ })
</script>

<template>
  <section class="prompt-preview" aria-label="完整提示词预览">
    <h4>完整提示词预览</h4>
    <p class="muted">与实际执行共用后端拼装。业务要求在节点设置中编辑；Schema、原文输入和 JSON 格式由系统维护。</p>
    <label v-if="nodes.length">抽取节点<select v-model="selected" aria-label="提示词预览节点"><option v-for="node in nodes" :key="node.id" :value="node.id">{{ names[node.ref] }} · {{ node.id }}</option></select></label>
    <p v-else class="muted">当前流程没有实体或关系抽取节点。</p>
    <p v-if="loading" role="status">正在生成提示词预览…</p>
    <div v-else-if="error" role="alert"><p>{{ error }}</p><button type="button" @click="refresh">重试预览</button></div>
    <template v-else-if="result">
      <p class="muted">v{{ result.operator_version }} · {{ result.notice }}</p>
      <template v-if="result.will_call_model">
        <h5>系统消息（只读）</h5><pre>{{ result.system }}</pre>
        <h5>用户提示词（只读）</h5><pre>{{ result.user }}</pre>
        <dl><template v-for="(label,key) in result.placeholders" :key="key"><dt>{{ key }}</dt><dd>{{ label }}</dd></template></dl>
      </template>
    </template>
  </section>
</template>

<style scoped>
.prompt-preview{min-width:0}.prompt-preview h4{margin:0 0 10px;font-size:16px}.prompt-preview label{display:grid;gap:8px}.prompt-preview select{width:100%}.prompt-preview pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0;max-height:420px;overflow:auto;padding:12px;background:#fff;border:1px solid var(--border);border-radius:8px;font-size:13px;line-height:1.6}.prompt-preview h5{font-size:14px;margin:14px 0 6px}.muted,.prompt-preview dl{color:var(--muted);font-size:13px;line-height:1.6}.prompt-preview [role=alert]{color:#b93838}
</style>
