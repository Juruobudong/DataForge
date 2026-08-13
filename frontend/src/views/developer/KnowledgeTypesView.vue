<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const types = ref([]), indexes = ref([]), managedCollections = ref([]), quality = ref([]), error = ref(''), result = ref(null)
const typeForm = ref({ code: '', name: '', icon: '知', schema: '{"type":"object","required":["title"]}', canonical_field: 'title', identity_fields: 'title', source_policy: 'single', quality_profile_revision_id: '', index_profile_ids: [] })
const indexForm = ref({ code: '', knowledge_type: 'text', collection_name: '', embedding_code: 'bce_base_768_v1', embedding_model: 'bce-embedding-base', dimension: 768, metric_type: 'COSINE', endpoint_ref: 'EMBEDDING_API_BASE', fields: '{"id":"id","vector":"vector","knowledge_library_id":"knowledge_library_id","source_knowledge_id":"source_knowledge_id","content":"content","data":"data"}' })

async function load() {
  try {
    const [nextTypes, vectorIndexes, qualityProfiles] = await Promise.all([api.knowledgeTypes(), api.vectorIndexes(), api.qualityProfiles()])
    types.value = nextTypes; indexes.value = vectorIndexes.profiles; managedCollections.value = vectorIndexes.managed_collections || []; quality.value = qualityProfiles
    if (!typeForm.value.quality_profile_revision_id) typeForm.value.quality_profile_revision_id = qualityProfiles.flatMap(item => item.revisions).find(item => item.status === 'published')?.id || ''
    if (!typeForm.value.index_profile_ids.length) typeForm.value.index_profile_ids = vectorIndexes.profiles.filter(item => item.status === 'active').slice(0, 1).map(item => item.id)
  } catch (e) { error.value = e.message }
}
function parse(value, label) { try { return JSON.parse(value) } catch { throw new Error(`${label} 必须是合法 JSON`) } }
async function createType() { try { result.value = await api.createKnowledgeType({ ...typeForm.value, schema: parse(typeForm.value.schema, 'Schema'), identity_fields: typeForm.value.identity_fields.split(',').map(item => item.trim()).filter(Boolean) }); await load() } catch (e) { error.value = e.message } }
async function typeAction(type, action) { try { result.value = action === 'validate' ? await api.validateKnowledgeType(type.id) : await api.publishKnowledgeType(type.id); await load() } catch (e) { error.value = e.message } }
async function createIndex() { try { result.value = await api.createIndexProfile({ ...indexForm.value, fields: parse(indexForm.value.fields, '字段映射') }); await load() } catch (e) { error.value = e.message } }
async function indexAction(index, action) { try { result.value = action === 'validate' ? await api.validateIndexProfile(index.id) : await api.publishIndexProfile(index.id); await load() } catch (e) { error.value = e.message } }
onMounted(load)
</script>

<template>
  <section><div class="page-head"><div><h2>知识类型</h2><p>文本、问答、图谱是内置类型；扩展类型、Index Profile 和发布修订均受管理员治理。</p></div><span class="badge blue">受控发布</span></div>
    <div class="cards"><article v-for="type in types" :key="type.id"><span class="badge" :class="type.kind==='builtin'?'blue':'amber'">{{ type.icon }}</span><h3>{{ type.name }}</h3><p>{{ type.code }} · {{ type.kind }}</p><small>r{{ type.current_revision?.revision || '—' }} · {{ type.current_revision?.source_policy || 'draft' }}</small><div v-if="type.kind==='extension' && type.current_revision"><button @click="typeAction(type,'validate')">校验</button><button class="primary" @click="typeAction(type,'publish')">发布最新修订</button></div></article></div>
    <div class="governance-grid"><form class="panel stack" @submit.prevent="createType"><h3>新建扩展知识类型</h3><input v-model="typeForm.code" required placeholder="类型编码（管理员填写）"><input v-model="typeForm.name" required placeholder="名称"><input v-model="typeForm.icon" required placeholder="图标"><label>JSON Schema<textarea v-model="typeForm.schema" rows="5" /></label><input v-model="typeForm.canonical_field" required placeholder="canonical 字段"><input v-model="typeForm.identity_fields" required placeholder="identity 字段，以逗号分隔"><select v-model="typeForm.source_policy"><option value="single">单来源</option><option value="multiple">多来源</option></select><select v-model="typeForm.quality_profile_revision_id" required><option v-for="profile in quality.flatMap(item=>item.revisions.filter(rev=>rev.status==='published').map(rev=>({...rev,code:item.code})))" :key="profile.id" :value="profile.id">{{ profile.code }} · r{{ profile.revision }}</option></select><label>已发布 Index Profile<select v-model="typeForm.index_profile_ids" multiple required><option v-for="profile in indexes.filter(item=>item.status==='active')" :key="profile.id" :value="profile.id">{{ profile.code }} → {{ profile.collection_name }}</option></select></label><button class="primary">保存草稿</button></form>
      <form class="panel stack" @submit.prevent="createIndex"><h3>新建 Index Profile</h3><input v-model="indexForm.code" required placeholder="Profile 编码（管理员填写）"><select v-model="indexForm.knowledge_type"><option v-for="type in types" :key="type.id" :value="type.code">{{ type.name }}</option></select><input v-model="indexForm.collection_name" required placeholder="已有 Milvus Collection"><input v-model="indexForm.embedding_code" required placeholder="Embedding 编码"><input v-model="indexForm.embedding_model" required placeholder="Embedding 模型"><input v-model.number="indexForm.dimension" min="1" type="number" required><input v-model="indexForm.metric_type" required placeholder="度量类型"><input v-model="indexForm.endpoint_ref" placeholder="Embedding endpoint 引用"><label>字段映射（默认生产字段可编辑）<textarea v-model="indexForm.fields" rows="6" /></label><button class="primary">保存草稿</button></form></div>
    <section class="panel"><h3>Storage Contract 与 Collection</h3><p>只有结构哈希完全相同的 Profile 才能共用受管 Collection；外部 Collection 仍只校验、不接管。</p><table><thead><tr><th>结构契约</th><th>Collection</th><th>规格哈希</th><th>状态</th></tr></thead><tbody><tr v-for="item in managedCollections" :key="item.id"><td>{{ item.storage_contract.code }} · r{{ item.storage_contract.revision }}</td><td>{{ item.collection_name }}</td><td><code>{{ item.desired_spec_hash.slice(0,16) }}</code></td><td><span class="badge" :class="item.status==='ready'?'green':item.status==='failed'||item.status==='incompatible'?'red':'amber'">{{ item.status }}</span></td></tr></tbody></table></section>
    <section class="panel"><h3>Index Profile 发布</h3><p>受管 Profile 由 Provisioner 创建 Collection；外部 Profile 只校验既有 Collection。平台始终只删除本知识库的 <code>kl_&lt;library-id&gt;</code> Partition。</p><table><thead><tr><th>编码</th><th>Collection</th><th>策略</th><th>修订</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="index in indexes" :key="index.id"><td>{{ index.code }}</td><td>{{ index.collection_name }}</td><td>{{ index.revisions?.[0]?.collection_policy || 'external' }}</td><td>{{ index.revisions?.[0]?.revision || '—' }}</td><td>{{ index.status }}</td><td><button @click="indexAction(index,'validate')">校验</button><button class="primary" @click="indexAction(index,'publish')">发布</button></td></tr></tbody></table></section><pre v-if="result">{{ JSON.stringify(result,null,2) }}</pre><p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.governance-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:16px 0}.stack{display:grid;gap:9px}textarea{font-family:ui-monospace,monospace}@media(max-width:900px){.governance-grid{grid-template-columns:1fr}}
</style>
