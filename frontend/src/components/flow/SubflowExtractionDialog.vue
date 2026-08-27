<script setup>
import { ref } from 'vue'
import { api } from '../../api/platform'
import { useDialogFocus } from './composables/useDialogFocus'
const props = defineProps({ definition: Object, outputTypes: Array, selectedNodeIds: Array })
const emit = defineEmits(['close', 'created', 'open'])
const code = ref(''), name = ref(''), description = ref(''), error = ref(''), pending = ref(false), created = ref(null)
const panel = ref(null), trapFocus = useDialogFocus(panel)
async function save() {
  pending.value = true; error.value = ''
  try {
    created.value = await api.createFlowSubgraph({ code: code.value, name: name.value, description: description.value,
      definition: props.definition, output_types: props.outputTypes, selected_node_ids: props.selectedNodeIds })
    emit('created', created.value)
  } catch (e) { error.value = e.message }
  finally { pending.value = false }
}
</script>
<template>
  <div class="modal-overlay" @keydown.esc="!pending && emit('close')">
    <section ref="panel" class="subflow-dialog" role="dialog" aria-modal="true" aria-labelledby="extract-title" @keydown="trapFocus">
      <h3 id="extract-title">另存为可复用子流程</h3>
      <template v-if="created"><p>已创建草稿 r{{ created.revision }}。原画布未改变，发布后可在其他流程中添加。</p><button class="primary" @click="emit('open', created)">查看草稿</button><button @click="emit('close')">留在画布</button></template>
      <form v-else @submit.prevent="save">
        <p>保存 {{ selectedNodeIds.length }} 个节点。选区须连通、单入口、单出口，不含流程输入或知识输出。</p>
        <label>名称<input v-model="name" required autofocus></label><label>唯一编码<input v-model="code" required></label><label>描述<textarea v-model="description" rows="3"></textarea></label>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <footer><button type="button" :disabled="pending" @click="emit('close')">取消</button><button class="primary" :disabled="pending">{{ pending ? '保存中…' : '创建子流程草稿' }}</button></footer>
      </form>
    </section>
  </div>
</template>
<style scoped>
.modal-overlay{position:fixed;inset:0;z-index:1000;display:grid;place-items:center;background:#172b4d55}.subflow-dialog{width:520px;max-height:85vh;overflow:auto;padding:24px;border-radius:14px;background:#fff;box-shadow:0 16px 48px #172b4d33}.subflow-dialog h3{margin-top:0;font-size:20px}.subflow-dialog p{line-height:1.6;color:#65748b}.subflow-dialog label{display:grid;gap:6px;margin:12px 0}.subflow-dialog footer{display:flex;gap:10px;justify-content:flex-end;margin-top:16px}.subflow-dialog>button+button{margin-left:10px}
</style>
