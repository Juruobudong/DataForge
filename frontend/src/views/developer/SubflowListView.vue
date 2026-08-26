<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/platform'
import { subflowPrimaryName, subflowSubtitle } from '../../components/flow/flowModel'

const router = useRouter()
const subflows = ref([]), loading = ref(false), error = ref('')
async function load() {
  loading.value = true; error.value = ''
  try { subflows.value = await api.flowSubgraphs() } catch (e) { error.value = e.message } finally { loading.value = false }
}
function open(item) { router.push(`/developer/flow-templates/subgraphs/${item.id}/revisions/${item.revision}`) }
onMounted(load)
</script>

<template>
  <section class="subflow-page">
    <div class="page-head"><div><h2>可复用子流程</h2><p>维护可在高级编排中作为单节点引用的受控子流程草稿和 Revision。</p></div><button :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button></div>
    <section class="panel">
      <button v-for="item in subflows" :key="item.id" class="subflow-row" @click="open(item)">
        <span class="icon">◈</span><span><b>{{ subflowPrimaryName(item) }}</b><small>{{ subflowSubtitle(item) }}</small><p>{{ item.description || '可复用受控子流程' }}</p></span><span><span class="badge blue">{{ item.node_count }} 节点 / {{ item.edge_count }} 连线</span><small class="open-label">查看完整 DAG</small></span>
      </button>
      <p v-if="!loading && !subflows.length" class="empty">尚无可复用子流程。</p>
    </section>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.subflow-page{display:grid;gap:14px}.subflow-row{display:grid;width:100%;grid-template-columns:36px minmax(0,1fr) auto;gap:12px;align-items:center;margin-top:7px;padding:13px;text-align:left}.icon{display:grid;width:34px;height:34px;place-items:center;border-radius:9px;background:#eaf1ff;color:#2f6fed;font-size:18px}.subflow-row b,.subflow-row small{display:block}.subflow-row small{margin-top:3px;color:#7d899a}.subflow-row p{margin:5px 0 0;color:#5f6c7e}.open-label{margin-top:5px;text-align:right}.empty{padding:20px;color:#7d899a;text-align:center}
</style>
