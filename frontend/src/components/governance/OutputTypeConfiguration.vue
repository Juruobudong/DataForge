<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const types = ref([])
const loading = ref(true)
const error = ref('')
const result = ref(null)
const editingId = ref('')

const emptyForm = () => ({
  code: '', name: '', icon: '知', schema: '{\n  "type": "object",\n  "required": ["title"]\n}',
  canonical_field: 'title', identity_fields: 'title', source_policy: 'single',
})
const form = ref(emptyForm())
const builtinOrder = { text: 0, qa: 1, graph: 2 }
const builtinTypes = computed(() => types.value.filter(item => item.kind !== 'extension').sort((left, right) => (builtinOrder[left.code] ?? 99) - (builtinOrder[right.code] ?? 99)))
const extensionTypes = computed(() => types.value.filter(item => item.kind === 'extension'))
const editingType = computed(() => types.value.find(item => item.id === editingId.value) || null)
function typeRevision(type) { return type.latest_revision || type.current_revision || null }
function typeProfiles(type) { return type.latest_index_profiles || type.index_profiles || [] }

async function load() {
  loading.value = true; error.value = ''
  try { types.value = await api.knowledgeTypes() }
  catch (err) { error.value = err.message }
  finally { loading.value = false }
}

function parseSchema() {
  try { return JSON.parse(form.value.schema) }
  catch (_) { throw new Error('业务 JSON Schema 必须是合法 JSON') }
}

function semanticPayload() {
  return {
    schema: parseSchema(),
    canonical_field: form.value.canonical_field.trim(),
    identity_fields: form.value.identity_fields.split(',').map(item => item.trim()).filter(Boolean),
    source_policy: form.value.source_policy,
  }
}

function resetForm() { editingId.value = ''; form.value = emptyForm(); result.value = null; error.value = '' }

function editType(type) {
  const revision = typeRevision(type) || {}
  editingId.value = type.id
  form.value = {
    code: type.code, name: type.name, icon: type.icon || '知',
    schema: JSON.stringify(revision.schema || { type: 'object' }, null, 2),
    canonical_field: revision.canonical_field || '',
    identity_fields: (revision.identity_fields || []).join(', '),
    source_policy: revision.source_policy || 'single',
  }
  result.value = null; error.value = ''
}

async function saveType() {
  error.value = ''; result.value = null
  try {
    const payload = semanticPayload()
    result.value = editingId.value
      ? await api.reviseKnowledgeType(editingId.value, payload)
      : await api.createKnowledgeType({
          code: form.value.code.trim(), name: form.value.name.trim(), icon: form.value.icon.trim() || '知', ...payload,
        })
    await load()
    if (editingId.value) editType(types.value.find(item => item.id === editingId.value))
    else resetForm()
  } catch (err) { error.value = err.message }
}

async function typeAction(type, action) {
  error.value = ''; result.value = null
  try {
    result.value = action === 'validate'
      ? await api.validateKnowledgeType(type.id)
      : await api.publishKnowledgeType(type.id)
    await load()
  } catch (err) { error.value = err.message }
}

function storageSummary(type) {
  const profiles = typeProfiles(type)
  if (!profiles.length) return '尚无 Storage Profile'
  return `${profiles.length} 个 Profile · ${[...new Set(profiles.map(item => item.collection_name))].join(' / ')}`
}

onMounted(load)
</script>

<template>
  <section class="output-type-configuration">
    <div class="section-heading"><div><h3>正式输出类型</h3><p>流程开发人员只定义正式输出语义；Quality、Index、Storage 与 Collection 运行契约由 DataForge 维护。</p></div><span class="badge blue">受控发布</span></div>
    <p v-if="loading" class="loading">正在加载输出类型…</p>
    <template v-else>
      <section class="output-type-section">
        <div class="subsection-title"><div><h4>内置类型</h4><p>Text、QA 与 Graph 为平台正式契约，只读展示。</p></div></div>
        <div class="output-type-grid">
          <article v-for="type in builtinTypes" :key="type.id" class="panel output-type-card">
            <header><span class="type-icon">{{ type.icon }}</span><div><h4>{{ type.name }}</h4><code>{{ type.code }}</code></div><span class="badge green">内置</span></header>
            <dl><div><dt>Canonical</dt><dd><code>{{ typeRevision(type)?.canonical_field || '—' }}</code></dd></div><div><dt>Identity</dt><dd>{{ typeRevision(type)?.identity_fields?.join(', ') || '—' }}</dd></div><div><dt>来源策略</dt><dd>{{ typeRevision(type)?.source_policy || '—' }}</dd></div></dl>
            <details><summary>技术详情</summary><p>Quality Revision：<code>{{ typeRevision(type)?.quality_profile_revision_id || '—' }}</code></p><p>{{ storageSummary(type) }}</p><pre>{{ JSON.stringify(typeRevision(type)?.schema || {}, null, 2) }}</pre></details>
          </article>
        </div>
      </section>

      <section class="output-type-section">
        <div class="subsection-title"><div><h4>扩展类型</h4><p>创建或修订业务输出 Schema；保存草稿不会访问 Milvus。</p></div></div>
        <div v-if="extensionTypes.length" class="output-type-grid">
          <article v-for="type in extensionTypes" :key="type.id" class="panel output-type-card">
            <header><span class="type-icon">{{ type.icon }}</span><div><h4>{{ type.name }}</h4><code>{{ type.code }}</code></div><span :class="['badge', typeRevision(type)?.status === 'published' ? 'green' : 'amber']">{{ typeRevision(type)?.status || type.status }}</span></header>
            <p>{{ storageSummary(type) }}</p>
            <details><summary>技术详情</summary><p>Revision：r{{ typeRevision(type)?.revision || '—' }}</p><p>Quality Revision：<code>{{ typeRevision(type)?.quality_profile_revision_id || '—' }}</code></p><pre>{{ JSON.stringify(typeRevision(type)?.schema || {}, null, 2) }}</pre></details>
            <div class="actions"><button type="button" @click="editType(type)">编辑契约</button><button type="button" @click="typeAction(type,'validate')">校验</button><button class="primary" type="button" @click="typeAction(type,'publish')">Provision 并发布</button></div>
            <RouterLink :to="`/business/vector-storage?tab=profiles&knowledge_type=${encodeURIComponent(type.code)}`">前往 Storage Profile 高级治理 →</RouterLink>
          </article>
        </div>
        <p v-else class="empty-guidance">尚无扩展输出类型。</p>
      </section>

      <form class="panel output-type-form" @submit.prevent="saveType">
        <div class="panel-head"><div><h3>{{ editingType ? `修订 ${editingType.name}` : '新建扩展输出类型' }}</h3><p>Quality Profile 自动绑定默认已发布 Revision，并冻结到本次类型 Revision。</p></div><button v-if="editingType" type="button" @click="resetForm">取消编辑</button></div>
        <div v-if="!editingType" class="form-grid"><label>类型编码<input v-model="form.code" required></label><label>名称<input v-model="form.name" required></label><label>图标<input v-model="form.icon" required maxlength="8"></label></div>
        <div v-else class="readonly-identity"><span>{{ editingType.name }}</span><code>{{ editingType.code }}</code></div>
        <label>业务 JSON Schema<textarea v-model="form.schema" rows="10" required /></label>
        <div class="form-grid"><label>Canonical field<input v-model="form.canonical_field" required></label><label>Identity fields<input v-model="form.identity_fields" required placeholder="以逗号分隔"></label><label>来源策略<select v-model="form.source_policy"><option value="single">单来源</option><option value="multiple">多来源</option></select></label></div>
        <div class="actions"><button class="primary">{{ editingType ? '保存新 Revision' : '保存草稿' }}</button></div>
      </form>
    </template>
    <div v-if="error" class="error action-error"><p>{{ error }}</p><div class="actions"><RouterLink to="/business/vector-storage?tab=profiles">检查 Storage Profile</RouterLink><RouterLink to="/business/milvus-targets">检查 Milvus 服务</RouterLink></div></div>
    <pre v-if="result" class="action-result">{{ JSON.stringify(result, null, 2) }}</pre>
  </section>
</template>

<style scoped>
.output-type-configuration,.output-type-section,.output-type-form{display:grid;gap:16px}.output-type-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.output-type-card{display:grid;align-content:start;gap:12px}.output-type-card header{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:10px}.output-type-card h4,.subsection-title h4{margin:0}.type-icon{display:grid;width:38px;height:38px;place-items:center;border-radius:10px;color:var(--blue);background:var(--blue-soft);font-weight:850}.output-type-card dl{display:grid;gap:8px}.output-type-card dl>div{display:grid;grid-template-columns:88px minmax(0,1fr);gap:8px}.output-type-card dt{color:var(--muted)}.output-type-card dd{margin:0}.output-type-card pre{max-height:220px;overflow:auto}.subsection-title p,.panel-head p{margin:4px 0 0;color:var(--muted)}.output-type-form label{display:grid;gap:6px}.form-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.readonly-identity{display:flex;align-items:center;gap:10px}.action-error{display:grid;gap:8px}.action-error p{margin:0}@media(max-width:1400px){.output-type-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
