<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/platform'
import { subflowPrimaryName, subflowSubtitle } from '../../components/flow/flowModel'
import SubflowReferences from '../../components/flow/SubflowReferences.vue'

const router = useRouter()
const subflows = ref([]), loading = ref(false), error = ref('')
const references = ref(null)
async function load() {
  loading.value = true; error.value = ''
  try { subflows.value = await api.flowSubgraphs() } catch (e) { error.value = e.message } finally { loading.value = false }
}
function open(item) { router.push(`/developer/flow-templates/subgraphs/${item.id}/revisions/${item.revision}`) }
onMounted(load)
</script>

<template>
  <section class="subflow-page">
    <div class="page-head"><div><h2>可复用子流程</h2><p>将常用的一组算子保存为子流程，可在多个高级编排中作为一个节点重复使用。</p></div><button :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button></div>
    <section class="panel">
      <article v-for="item in subflows" :key="item.id" class="subflow-row">
        <span class="icon">◈</span><span><b>{{ subflowPrimaryName(item) }}</b><small>{{ subflowSubtitle(item) }} · {{ item.revision_status === 'draft' ? '草稿' : '已发布' }}</small><p>{{ item.description || '可复用受控子流程' }}</p><small v-if="item.usage === 'source_preparation'">审核前 · 文档预处理</small></span><span><span class="badge blue">{{ item.node_count }} 节点 / {{ item.edge_count }} 连线</span><small>r{{ item.revision }} 被 {{ item.reference_count || 0 }} 个流程引用</small><div class="row-actions"><button title="查看完整 DAG" @click="open(item)">查看 DAG</button><button @click="references=item">查看引用</button><button v-if="item.draft_revision" @click="open({ ...item, revision: item.draft_revision })">编辑草稿 r{{ item.draft_revision }}</button></div></span>
      </article>
      <p v-if="!loading && !subflows.length" class="empty">尚无可复用子流程。</p>
    </section>
    <p v-if="error" class="error">{{ error }}</p>
    <SubflowReferences v-if="references" :key="`${references.id}:${references.revision}`" :item="references" @close="references=null" />
  </section>
</template>

<style scoped>
.subflow-page{display:grid;gap:14px}.subflow-row{display:grid;width:100%;grid-template-columns:36px minmax(0,1fr) auto;gap:12px;align-items:center;margin-top:7px;padding:13px;text-align:left}.icon{display:grid;width:34px;height:34px;place-items:center;border-radius:9px;background:#eaf1ff;color:#2f6fed;font-size:18px}.subflow-row b,.subflow-row small{display:block}.subflow-row small{margin-top:3px;color:#7d899a}.subflow-row p{margin:5px 0 0;color:#5f6c7e}.open-label{margin-top:5px;text-align:right}.empty{padding:20px;color:#7d899a;text-align:center}
</style>
<style scoped>.row-actions{display:flex;gap:8px;margin-top:8px}.subflow-row{border-bottom:1px solid #e2e8f0}.subflow-row b{font-size:15px}.subflow-row small{font-size:13px}</style>
