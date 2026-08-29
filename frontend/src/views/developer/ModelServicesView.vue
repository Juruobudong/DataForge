<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'
import { blankServiceForm, editServiceForm, serviceStatus } from './modelServices.js'

const CATEGORY_LABELS = { llm: '大模型服务', embedding: 'Embedding 服务', reranker: 'Reranker', 'ocr-vision': 'OCR·视觉' }
const categories = ref([]), activeCategory = ref('llm')
const models = ref([]), embeddings = ref([]), rerankers = ref([]), notice = ref('')
const editing = ref(null), form = ref(blankServiceForm('model')), error = ref(''), result = ref(null), busy = ref(false)
const isLlm = computed(() => activeCategory.value === 'llm')
const isEmbedding = computed(() => activeCategory.value === 'embedding')
const isReranker = computed(() => activeCategory.value === 'reranker')
const activeAvailable = computed(() => categories.value.some(item => item.key === activeCategory.value && item.available))
const rows = computed(() => isLlm.value ? models.value : isReranker.value ? rerankers.value : embeddings.value)
const defaultModel = computed(() => models.value.find(item => item.is_default))
const defaultEmbedding = computed(() => embeddings.value.find(item => item.is_default))
const defaultReranker = computed(() => rerankers.value.find(item => item.is_default))
function currentKind() { return isLlm.value ? 'model' : isReranker.value ? 'reranker' : 'embedding' }
function serviceApi(operation, ...args) {
  const prefix = { model: 'Model', embedding: 'Embedding', reranker: 'Reranker' }[currentKind()]
  const method = operation === 'references' ? `${currentKind()}ServingReferences` : `${operation}${prefix}Serving`
  return api[method](...args)
}

async function load() {
  error.value = ''
  try {
    const [cats, modelRows, embeddingRows, rerankerRows] = await Promise.all([api.servingCategories(), api.modelServings(), api.embeddingServings(), api.rerankerServings()])
    categories.value = cats; models.value = modelRows; embeddings.value = embeddingRows; rerankers.value = rerankerRows
  }
  catch (e) { error.value = e.message }
}
function openCreate() { editing.value = null; form.value = blankServiceForm(currentKind()) }
function openEdit(item) { editing.value = item; form.value = editServiceForm(item, currentKind()) }
async function save() {
  busy.value = true; error.value = ''
  try {
    const payload = { ...form.value }
    if (!payload.api_key) delete payload.api_key
    if (!payload.clear_credential) delete payload.clear_credential
    if (editing.value) delete payload.serving_code
    result.value = editing.value ? await serviceApi('patch', editing.value.id, payload) : await serviceApi('create', payload)
    notice.value = '模型服务已保存；连接配置变更后请重新测试。'
    await load(); openCreate()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function action(item, kind) {
  busy.value = true; error.value = ''
  try {
    if (kind === 'delete' && !window.confirm(`确认删除 ${item.name}？`)) return
    result.value = kind === 'toggle' ? await serviceApi('patch', item.id, { is_enabled: !item.is_enabled }) : await serviceApi(kind, item.id)
    notice.value = kind === 'test' ? (result.value.last_check_status === 'healthy' ? '连接测试成功。' : `连接测试失败：${result.value.last_check_error || result.value.last_check_status}`) : '操作已完成。'
    await load()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
function switchTab(value) { if (busy.value) return; activeCategory.value = value; notice.value = ''; result.value = null; if (activeAvailable.value) openCreate() }
onMounted(async () => { await load(); openCreate() })
</script>

<template>
  <section class="model-services-page">
    <div class="page-head"><div><h2>模型服务</h2><p>独立管理大模型、Embedding 与检索重排使用的 Reranker Serving。</p></div><span class="badge blue">数据库事实源</span></div>
    <div class="default-cards">
      <article><small>大模型默认</small><h3>★ {{ defaultModel?.name || 'Qwen3-32B' }}</h3><p>{{ defaultModel?.model_name || '待加载' }}</p><span v-if="defaultModel" :class="['badge', serviceStatus(defaultModel).tone]">{{ serviceStatus(defaultModel).label }}</span></article>
      <article><small>Embedding 默认</small><h3>★ {{ defaultEmbedding?.name || 'BCE Base 768' }}</h3><p>{{ defaultEmbedding?.model_name || '待加载' }} · {{ defaultEmbedding?.dimension || 768 }} 维</p><span v-if="defaultEmbedding" :class="['badge', serviceStatus(defaultEmbedding).tone]">{{ serviceStatus(defaultEmbedding).label }}</span></article>
    </div>
    <article class="panel"><small>Reranker 默认</small><h3>{{ defaultReranker?.name || '未设置' }}</h3><p>{{ defaultReranker?.model_name || '暂无服务' }} · 默认服务不会自动开启任务重排</p></article>
    <p v-if="notice" role="status">{{ notice }}</p>
    <nav class="tabs"><button v-for="cat in categories" :key="cat.key" :disabled="busy" :class="{active:activeCategory===cat.key}" @click="switchTab(cat.key)">{{ CATEGORY_LABELS[cat.key] || cat.key }}</button></nav>
    <div v-if="activeAvailable" class="service-layout">
      <section class="panel service-list"><div class="section-head"><h3>{{ CATEGORY_LABELS[activeCategory] || activeCategory }}</h3><button class="primary" @click="openCreate">新增</button></div>
        <table><thead><tr><th>服务</th><th>协议 / 模型</th><th>状态</th><th>测试结果</th><th>操作</th></tr></thead><tbody>
          <tr v-for="item in rows" :key="item.id"><td><b>{{ item.is_default ? '★ ' : '' }}{{ item.name }}</b><small><code>{{ item.serving_code }}</code></small></td><td>{{ item.serving_type || item.provider_type }}<small>{{ item.model_name }}<template v-if="item.dimension"> · {{ item.dimension }} 维</template></small></td><td><span :class="['badge', serviceStatus(item).tone]">{{ serviceStatus(item).label }}</span></td><td><template v-if="isEmbedding"><small>配置 {{ item.dimension }} / 实际 {{ item.last_observed_dimension ?? '—' }}</small></template><small>{{ item.last_check_latency_ms != null ? `${item.last_check_latency_ms} ms` : '—' }}</small></td><td class="actions"><button @click="openEdit(item)">编辑</button><button :disabled="busy" @click="action(item,'test')">测试</button><button v-if="!item.is_default" :disabled="busy || !item.is_enabled" @click="action(item,'default')">设为默认</button><button v-if="!item.is_default" :disabled="busy" @click="action(item,'toggle')">{{ item.is_enabled ? '停用' : '启用' }}</button><button @click="action(item,'references')">引用</button><button v-if="!item.is_default" class="danger" @click="action(item,'delete')">删除</button></td></tr>
        </tbody></table>
      </section>
      <form class="panel service-form" @submit.prevent="save"><h3>{{ editing ? '编辑' : '新增' }}{{ CATEGORY_LABELS[activeCategory] }}</h3>
        <label>名称<input v-model="form.name" required></label><label>Serving ID<input v-model="form.serving_code" :disabled="!!editing" required></label>
        <label>协议<input :value="form.serving_type || form.provider_type" disabled></label><label>Base URL<input v-model="form.base_url" placeholder="https://host/v1"></label><label>Model Name<input v-model="form.model_name" required></label>
        <label>API Key<input v-model="form.api_key" type="password" :placeholder="editing?.credential_configured ? '已配置，留空保持' : '可选，不回显'"></label><label v-if="editing?.credential_configured" class="check"><input v-model="form.clear_credential" type="checkbox">显式清除已有 API Key</label>
        <template v-if="isEmbedding"><label>Dimension<input v-model.number="form.dimension" type="number" min="1" required></label><label>Batch Size<input v-model.number="form.batch_size" type="number" min="1" required></label></template>
        <label>Timeout<input v-model.number="form.timeout_seconds" type="number" min="1" required></label><label>Max Retries<input v-model.number="form.max_retries" type="number" min="0" required></label>
        <template v-if="isReranker"><label>最大批量<input v-model.number="form.max_batch_size" type="number" min="1" max="200" required></label><label>最大并发<input v-model.number="form.max_concurrency" type="number" min="1" max="64" required></label></template>
        <template v-if="isLlm"><label>Max Tokens<input v-model.number="form.max_tokens" type="number" min="1" required></label><label class="check"><input v-model="form.disable_thinking" type="checkbox">禁用 Thinking</label></template>
        <button class="primary" :disabled="busy">{{ busy ? '保存中…' : '保存' }}</button>
      </form>
    </div>
    <div v-else class="panel empty-state"><h3>{{ CATEGORY_LABELS[activeCategory] || activeCategory }}</h3><p>该模型分类当前尚未开放，暂无可用服务。</p></div>
    <p v-if="error" class="error">{{ error }}</p><pre v-if="result">{{ JSON.stringify(result, null, 2) }}</pre>
  </section>
</template>

<style scoped>
.model-services-page{display:grid;gap:18px}.default-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.default-cards article{padding:18px;border:1px solid var(--border);border-radius:12px;background:#fff;box-shadow:var(--shadow)}.default-cards small,.service-list small{display:block;margin-top:5px;color:var(--muted)}.tabs{display:flex;gap:8px}.tabs button.active{color:#fff;background:var(--blue)}.service-layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(300px,1fr);gap:16px}.section-head{display:flex;align-items:center;justify-content:space-between}.service-form{display:grid;gap:10px;align-content:start}.service-form label{display:grid;gap:6px}.service-form .check{display:flex;align-items:center;gap:7px}.actions{display:flex;flex-wrap:wrap;gap:5px}.danger{color:#b42318}.empty-state{padding:28px;text-align:center;color:var(--muted)}.empty-state h3{margin-bottom:8px}@media(max-width:1200px){.service-layout{grid-template-columns:1fr}}
</style>
