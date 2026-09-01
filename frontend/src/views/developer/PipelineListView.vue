<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/platform'

const router = useRouter(), catalog = ref([]), loading = ref(false), error = ref('')
const documentInput = computed(() => catalog.value.find(item => item.code === 'document-input'))
const chunker = computed(() => catalog.value.find(item => item.code === 'document-chunker'))

async function load() {
  loading.value = true; error.value = ''
  try { catalog.value = await api.operatorCatalog({ include_internal: true }) } catch (value) { error.value = value.message } finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <section class="preprocessing-page">
    <div class="page-head"><div><h2>文档预处理</h2><p>解析、Flow 分块与知识生成是三个独立领域；这里说明流程开发区实际可编排的输入契约。</p></div><span class="badge blue">Flow Contract</span></div>
    <section class="domain-map">
      <article><span>1</span><div><b>文档库解析</b><small>ParseJob → 不可变 ParsedDocument</small></div><em>文档库负责</em></article>
      <i>→</i>
      <article class="active"><span>2</span><div><b>流程输入准备</b><small>document-input → document-chunker → execution_gate</small></div><em>当前工作区</em></article>
      <i>→</i>
      <article><span>3</span><div><b>知识生成</b><small>消费冻结 FlowChunkReviewSnapshot</small></div><em>知识流程负责</em></article>
    </section>

    <section class="panel contract-panel">
      <div class="panel-head"><div><h3>唯一文档输入</h3><p>画布从 ParsedDocument 开始，不再从已审核 SourceChunk 开始。</p></div><code>{{ documentInput?.code || 'document-input' }}</code></div>
      <dl><div><dt>输出</dt><dd>{{ documentInput?.output_ports?.output?.artifact_type || 'parsed_document' }}</dd></div><div><dt>目录可见性</dt><dd>系统输入节点，不作为普通组件重复添加</dd></div></dl>
    </section>

    <section class="panel contract-panel">
      <div class="panel-head"><div><h3>文档切分</h3><p>分块参数冻结在 Flow Revision；模型窗口不由 Chunker 硬编码。</p></div><button class="primary" @click="router.push('/developer/operator-catalog')">查看算子组件</button></div>
      <dl><div><dt>产品身份</dt><dd>{{ chunker?.display_name_zh || '文档切分' }} · <code>{{ chunker?.code || 'document-chunker' }}</code></dd></div><div><dt>技术实现</dt><dd>{{ chunker?.driver || 'dataflow' }} / {{ chunker?.runtime_requirements?.executor || 'KBCChunkGenerator' }}</dd></div><div><dt>输入 / 输出</dt><dd>{{ chunker?.input_ports?.input?.artifact_type || 'parsed_document' }} → {{ chunker?.output_ports?.output?.artifact_type || 'candidate_flow_chunk_set' }}</dd></div></dl>
      <p class="notice">添加文档切分时，编辑器会同时创建不可拖拽的菱形审核 Gate。CSV/XLSX 可解析和预览，但当前通用 Chunker 会在预检返回 TABULAR_CHUNKING_UNSUPPORTED。</p>
    </section>
    <p v-if="loading">正在读取当前算子契约…</p><p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.preprocessing-page{display:grid;gap:14px}.domain-map{display:grid;grid-template-columns:1fr auto 1.25fr auto 1fr;align-items:center;gap:10px}.domain-map article{display:grid;min-height:90px;grid-template-columns:36px 1fr;gap:10px;align-items:center;padding:16px;border:1px solid var(--border);border-radius:12px;background:#fff}.domain-map article.active{border-color:#9fbced;background:#f7faff}.domain-map article>span{display:grid;width:34px;height:34px;place-items:center;border-radius:50%;color:#fff;background:#2f6fed;font-weight:900}.domain-map b,.domain-map small,.domain-map em{display:block}.domain-map small{margin-top:5px;color:var(--muted)}.domain-map em{grid-column:2;color:#728198;font-size:11px;font-style:normal}.domain-map i{color:#8da2bd;font-size:20px}.contract-panel{display:grid;gap:12px}.contract-panel dl{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0}.contract-panel dl div{padding:12px;border-radius:9px;background:#f7f9fc}.contract-panel dt{color:var(--muted);font-size:12px}.contract-panel dd{margin:6px 0 0}.notice{padding:11px 13px;border:1px solid #bad0f5;border-radius:8px;background:var(--blue-soft)}@media(max-width:900px){.domain-map{grid-template-columns:1fr}.domain-map i{display:none}.contract-panel dl{grid-template-columns:1fr}}
</style>
