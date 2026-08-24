<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/platform'
import { defaultCollectionName, managedCollectionCanRequestDelete } from './collectionLifecycle'

const types = ref([])
const indexes = ref([])
const managedCollections = ref([])
const deletionJobs = ref({})
const quality = ref([])
const embeddingServings = ref([])
const error = ref('')
const result = ref(null)

const standardFields = { id: 'id', vector: 'vector', knowledge_library_id: 'knowledge_library_id', source_knowledge_id: 'source_knowledge_id', content: 'content', data: 'data' }
const standardStorage = { fields: [
  { name: 'id', type: 'VARCHAR', max_length: 64, primary: true },
  { name: 'vector', type: 'FLOAT_VECTOR' },
  { name: 'knowledge_library_id', type: 'VARCHAR', max_length: 64 },
  { name: 'source_knowledge_id', type: 'VARCHAR', max_length: 128 },
  { name: 'content', type: 'VARCHAR', max_length: 65535 },
  { name: 'data', type: 'JSON' },
] }

const typeForm = ref({
  code: '', name: '', icon: '知', schema: '{"type":"object","required":["title"]}',
  canonical_field: 'title', identity_fields: 'title', source_policy: 'single',
  quality_profile_revision_id: '', index_profile_ids: [], managed_collection_name: '',
  reuse_managed_collection_id: '',
})
const indexForm = ref({
  code: '', knowledge_type: 'text', collection_mode: 'attach', collection_name: '',
  reuse_managed_collection_id: '', embedding_serving_id: 'bce_base_768', embedding_input: 'canonical_content',
  embedding_code: '', embedding_model: '', dimension: 0, metric_type: 'COSINE', endpoint_ref: null,
  fields: JSON.stringify(standardFields, null, 2),
  storage_schema: JSON.stringify(standardStorage, null, 2),
})

const manualProfiles = computed(() => indexes.value.filter(item => item.origin === 'manual'))
const managedProfiles = computed(() => indexes.value.filter(item => item.revisions?.[0]?.collection_policy === 'managed'))
const externalProfiles = computed(() => indexes.value.filter(item => item.revisions?.[0]?.collection_policy === 'external'))
const reusableCollections = computed(() => managedCollections.value.filter(item => item.status === 'ready'))
const selectedEmbeddingServing = computed(() => embeddingServings.value.find(item => item.serving_code === indexForm.value.embedding_serving_id))

watch(() => typeForm.value.code, (code, previous) => {
  const oldDefault = defaultCollectionName(previous)
  if (!typeForm.value.managed_collection_name || typeForm.value.managed_collection_name === oldDefault) {
    typeForm.value.managed_collection_name = defaultCollectionName(code)
  }
})

async function load() {
  error.value = ''
  try {
    const [nextTypes, vectorIndexes, qualityProfiles, servingRows] = await Promise.all([
      api.knowledgeTypes(), api.vectorIndexes(), api.qualityProfiles(), api.embeddingServings(),
    ])
    types.value = nextTypes
    indexes.value = vectorIndexes.profiles
    managedCollections.value = vectorIndexes.managed_collections || []
    const jobs = await Promise.all(managedCollections.value.map(async item => [
      item.id, await api.managedCollectionDeletionJobs(item.id),
    ]))
    deletionJobs.value = Object.fromEntries(jobs)
    quality.value = qualityProfiles
    embeddingServings.value = servingRows
    if (!embeddingServings.value.some(item => item.serving_code === indexForm.value.embedding_serving_id && item.is_enabled)) {
      indexForm.value.embedding_serving_id = embeddingServings.value.find(item => item.is_default && item.is_enabled)?.serving_code || ''
    }
    if (!typeForm.value.quality_profile_revision_id) {
      typeForm.value.quality_profile_revision_id = qualityProfiles.flatMap(item => item.revisions).find(item => item.status === 'published')?.id || ''
    }
  } catch (e) { error.value = e.message }
}

function parse(value, label) {
  try { return JSON.parse(value) } catch { throw new Error(`${label} 必须是合法 JSON`) }
}

async function createType() {
  error.value = ''
  try {
    const reuse = typeForm.value.reuse_managed_collection_id || null
    result.value = await api.createKnowledgeType({
      ...typeForm.value,
      schema: parse(typeForm.value.schema, 'Schema'),
      identity_fields: typeForm.value.identity_fields.split(',').map(item => item.trim()).filter(Boolean),
      reuse_managed_collection_id: reuse,
      managed_collection_name: reuse ? '' : typeForm.value.managed_collection_name,
    })
    await load()
  } catch (e) { error.value = e.message }
}

async function typeAction(type, action) {
  error.value = ''
  try {
    result.value = action === 'validate' ? await api.validateKnowledgeType(type.id) : await api.publishKnowledgeType(type.id)
    await load()
  } catch (e) { error.value = e.message }
}

async function createIndex() {
  error.value = ''
  try {
    const creating = indexForm.value.collection_mode === 'create'
    const reuse = creating ? (indexForm.value.reuse_managed_collection_id || null) : null
    result.value = await api.createIndexProfile({
      ...indexForm.value,
      collection_name: reuse ? '' : indexForm.value.collection_name,
      reuse_managed_collection_id: reuse,
      fields: parse(indexForm.value.fields, '字段映射'),
      storage_schema: creating ? parse(indexForm.value.storage_schema, 'Storage Schema') : null,
    })
    await load()
  } catch (e) { error.value = e.message }
}

async function indexAction(index, action) {
  error.value = ''
  try {
    if (action === 'archive') result.value = await api.archiveIndexProfile(index.id)
    else result.value = action === 'validate' ? await api.validateIndexProfile(index.id) : await api.publishIndexProfile(index.id)
    await load()
  } catch (e) { error.value = e.message }
}

async function reconcileCollection(item) {
  error.value = ''
  try { result.value = await api.reconcileManagedCollection(item.id); await load() }
  catch (e) { error.value = e.message }
}

async function requestCollectionDelete(item) {
  error.value = ''
  try {
    const check = await api.managedCollectionDeleteCheck(item.id)
    result.value = check
    if (!check.deletable) {
      error.value = check.blockers.map(blocker => blocker.message).join('；')
      return
    }
    if (!window.confirm(`${check.warning}\n\nCollection：${check.collection_name}`)) return
    result.value = await api.deleteManagedCollection(item.id)
    await load()
  } catch (e) { error.value = e.message }
}

async function retryCollectionDelete(job) {
  error.value = ''
  try { result.value = await api.retryManagedCollectionDeletion(job.id); await load() }
  catch (e) { error.value = e.message }
}
</script>

<template>
  <section>
    <div class="page-head">
      <div><h2>知识类型</h2><p>业务 JSON Schema 与物理 Storage Contract 分层治理；所有知识库通过独立 <code>kl_*</code> Partition 隔离。</p></div>
      <span class="badge blue">受控发布</span>
    </div>

    <div class="cards">
      <article v-for="type in types" :key="type.id">
        <span class="badge" :class="type.kind === 'builtin' ? 'blue' : 'amber'">{{ type.icon }}</span>
        <h3>{{ type.name }}</h3><p>{{ type.code }} · {{ type.kind }}</p>
        <small>r{{ type.current_revision?.revision || '—' }} · {{ type.status }}</small>
        <p v-for="profile in type.index_profiles" :key="profile.id"><code>{{ profile.code }}</code> → {{ profile.collection_name }}</p>
        <div v-if="type.kind === 'extension'">
          <button @click="typeAction(type, 'validate')">校验</button>
          <button class="primary" @click="typeAction(type, 'publish')">Provision 并发布</button>
        </div>
      </article>
    </div>

    <div class="governance-grid">
      <form class="panel stack" @submit.prevent="createType">
        <h3>新建扩展 Knowledge Type</h3>
        <input v-model="typeForm.code" required placeholder="类型编码">
        <input v-model="typeForm.name" required placeholder="名称">
        <input v-model="typeForm.icon" required placeholder="图标">
        <label>业务 JSON Schema<textarea v-model="typeForm.schema" rows="5" /></label>
        <input v-model="typeForm.canonical_field" required placeholder="canonical 字段">
        <input v-model="typeForm.identity_fields" required placeholder="identity 字段，以逗号分隔">
        <select v-model="typeForm.source_policy"><option value="single">单来源</option><option value="multiple">多来源</option></select>
        <select v-model="typeForm.quality_profile_revision_id" required>
          <option v-for="profile in quality.flatMap(item => item.revisions.filter(rev => rev.status === 'published').map(rev => ({ ...rev, code: item.code })))" :key="profile.id" :value="profile.id">{{ profile.code }} · r{{ profile.revision }}</option>
        </select>
        <label>新建受管 Collection 名<input v-model="typeForm.managed_collection_name" :disabled="!!typeForm.reuse_managed_collection_id" required></label>
        <label>或显式复用兼容受管 Collection
          <select v-model="typeForm.reuse_managed_collection_id"><option value="">不复用（默认独立）</option><option v-for="item in reusableCollections" :key="item.id" :value="item.id">{{ item.collection_name }}</option></select>
        </label>
        <label>附加已发布 Manual Profile
          <select v-model="typeForm.index_profile_ids" multiple><option v-for="profile in manualProfiles.filter(item => item.status === 'active')" :key="profile.id" :value="profile.id">{{ profile.code }} → {{ profile.collection_name }}</option></select>
        </label>
        <button class="primary">保存草稿</button>
      </form>

      <form class="panel stack" @submit.prevent="createIndex">
        <h3>新建 Manual Profile</h3>
        <label>Collection 来源<select v-model="indexForm.collection_mode"><option value="create">DataForge 创建受管 Collection</option><option value="attach">接入客户已有 external Collection</option></select></label>
        <input v-model="indexForm.code" required placeholder="Profile 编码">
        <select v-model="indexForm.knowledge_type"><option v-for="type in types" :key="type.id" :value="type.code">{{ type.name }}</option></select>
        <input v-model="indexForm.collection_name" :disabled="!!indexForm.reuse_managed_collection_id" required placeholder="Collection 名">
        <label v-if="indexForm.collection_mode === 'create'">或复用兼容受管 Collection
          <select v-model="indexForm.reuse_managed_collection_id"><option value="">不复用（默认独立）</option><option v-for="item in reusableCollections" :key="item.id" :value="item.id">{{ item.collection_name }}</option></select>
        </label>
        <label>Embedding 服务<select v-model="indexForm.embedding_serving_id" required><option v-for="item in embeddingServings.filter(row => row.is_enabled)" :key="item.id" :value="item.serving_code">{{ item.is_default ? '★ ' : '' }}{{ item.name }} · {{ item.dimension }} 维</option></select></label>
        <label>Model<input :value="selectedEmbeddingServing?.model_name || '—'" disabled></label>
        <label>Dimension<input :value="selectedEmbeddingServing?.dimension || '—'" disabled></label>
        <input v-model="indexForm.metric_type" required placeholder="度量类型">
        <label>Embedding Input<select v-model="indexForm.embedding_input"><option value="canonical_content">canonical_content</option><option value="question">question</option><option value="question_answer">question + answer</option></select></label>
        <label>字段映射<textarea v-model="indexForm.fields" rows="6" /></label>
        <label v-if="indexForm.collection_mode === 'create'">物理 Storage Schema<textarea v-model="indexForm.storage_schema" rows="8" /></label>
        <p v-else class="muted">external Collection 只校验和解绑，DataForge 不会创建或删除整个 Collection。</p>
        <button class="primary">保存草稿</button>
      </form>
    </div>

    <section class="panel">
      <h3>受管 Collection</h3>
      <p>DataForge-owned Collection 不自动删除；客户主动申请时必须先解除引用并通过 ownership 预检。</p>
      <table><thead><tr><th>Collection</th><th>所有权</th><th>Contract</th><th>状态</th><th>Partition / 引用</th><th>删除任务</th><th>操作</th></tr></thead>
        <tbody><tr v-for="item in managedCollections" :key="item.id">
          <td>{{ item.collection_name }}</td><td>DataForge · {{ item.ownership_verified ? '已验证' : '待验证' }}</td><td>{{ item.storage_contract.code }} · r{{ item.storage_contract.revision }}</td>
          <td><span class="badge" :class="item.status === 'ready' ? 'green' : item.status === 'failed' || item.status === 'incompatible' || item.status === 'delete_failed' ? 'red' : 'amber'">{{ item.status }}</span></td>
          <td>{{ item.partition_names?.join(', ') || '无 kl_* Partition' }}<br><small>{{ item.references?.profile_ids?.length || 0 }} Profile / {{ item.references?.knowledge_libraries?.length || 0 }} 知识库</small></td>
          <td><template v-if="deletionJobs[item.id]?.length"><span class="badge">{{ deletionJobs[item.id][0].status }}</span><button v-if="deletionJobs[item.id][0].status === 'failed'" @click="retryCollectionDelete(deletionJobs[item.id][0])">重试</button></template><span v-else>—</span></td>
          <td><button v-if="['planned','failed','incompatible'].includes(item.status)" @click="reconcileCollection(item)">Provision</button><button v-if="managedCollectionCanRequestDelete(item)" class="danger" @click="requestCollectionDelete(item)">申请删除</button></td>
        </tr></tbody>
      </table>
    </section>

    <section class="panel">
      <h3>Profile</h3>
      <table><thead><tr><th>编码</th><th>来源</th><th>Embedding 服务</th><th>向量契约</th><th>Collection</th><th>策略</th><th>状态</th><th>操作</th></tr></thead>
        <tbody><tr v-for="index in [...managedProfiles, ...externalProfiles]" :key="index.id">
          <td>{{ index.code }}</td><td>{{ index.origin }}</td><td>{{ index.embedding_serving?.name || index.embedding_serving_id || '待绑定' }}<br><small>{{ index.embedding_serving?.model_name }}</small></td><td><template v-if="index.embedding_serving"><div>Serving {{ index.vector_contract?.embedding_serving_dimension ?? index.embedding_serving.dimension }}</div><div>Profile {{ index.vector_contract?.index_profile_dimension ?? index.dimension }}</div><div>Storage {{ managedCollections.find(item => item.id === index.managed_collection_id)?.storage_contract?.dimension || '—' }}</div><div>Milvus {{ index.vector_contract?.milvus_collection_dimension ?? '待连接' }}</div><span :class="['badge', index.vector_contract?.compatible ? 'green' : 'red']">{{ index.vector_contract?.compatible ? '✓ 维度一致' : '× 维度不一致' }}</span></template><span v-else>—</span></td><td>{{ index.collection_name }}</td><td>{{ index.revisions?.[0]?.collection_policy }}</td><td>{{ index.status }}</td>
          <td><button @click="indexAction(index, 'validate')">校验</button><button v-if="index.status !== 'active' && index.status !== 'archived'" class="primary" @click="indexAction(index, 'publish')">发布</button><button v-if="index.origin === 'manual' && index.status !== 'archived'" @click="indexAction(index, 'archive')">归档/解绑</button></td>
        </tr></tbody>
      </table>
    </section>

    <pre v-if="result">{{ JSON.stringify(result, null, 2) }}</pre>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.stack label { display: grid; gap: 6px; }
.muted { color: var(--muted, #64748b); }
.danger { color: #b42318; }
table button + button { margin-left: 8px; }
</style>
