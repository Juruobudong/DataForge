<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../api/platform'
import { defaultCollectionName, managedCollectionCanRequestDelete } from './collectionLifecycle'

const props = defineProps({ initialKnowledgeType: { type: String, default: '' } })
const types = ref([]), indexes = ref([]), managedCollections = ref([]), deletionJobs = ref({}), embeddingServings = ref([])
const loading = ref(true), error = ref(''), result = ref(null)
const bindingTypeId = ref(''), bindingProfileIds = ref([]), bindingMode = ref('preserve'), bindingCollectionName = ref(''), bindingReuseId = ref('')

const standardFields = { id: 'id', vector: 'vector', knowledge_library_id: 'knowledge_library_id', source_knowledge_id: 'source_knowledge_id', content: 'content', data: 'data' }
const standardStorage = { fields: [
  { name: 'id', type: 'VARCHAR', max_length: 64, primary: true },
  { name: 'vector', type: 'FLOAT_VECTOR' },
  { name: 'knowledge_library_id', type: 'VARCHAR', max_length: 64 },
  { name: 'source_knowledge_id', type: 'VARCHAR', max_length: 128 },
  { name: 'content', type: 'VARCHAR', max_length: 65535 },
  { name: 'data', type: 'JSON' },
] }
const indexForm = ref({
  code: '', knowledge_type: 'text', collection_mode: 'attach', collection_name: '', reuse_managed_collection_id: '',
  embedding_serving_id: 'bce_base_768', embedding_input: 'canonical_content', embedding_code: '', embedding_model: '',
  dimension: 0, metric_type: 'COSINE', endpoint_ref: null,
  fields: JSON.stringify(standardFields, null, 2), storage_schema: JSON.stringify(standardStorage, null, 2),
})

const extensionTypes = computed(() => types.value.filter(item => item.kind === 'extension'))
const selectedType = computed(() => extensionTypes.value.find(item => item.id === bindingTypeId.value) || null)
const manualProfiles = computed(() => indexes.value.filter(item => item.origin === 'manual'))
const managedProfiles = computed(() => indexes.value.filter(item => item.revisions?.[0]?.collection_policy === 'managed'))
const externalProfiles = computed(() => indexes.value.filter(item => item.revisions?.[0]?.collection_policy === 'external'))
const reusableCollections = computed(() => managedCollections.value.filter(item => item.status === 'ready'))
const compatibleManualProfiles = computed(() => manualProfiles.value.filter(item => item.status === 'active' && item.knowledge_type === selectedType.value?.code))
const selectedEmbeddingServing = computed(() => embeddingServings.value.find(item => item.serving_code === indexForm.value.embedding_serving_id))

function parse(value, label) { try { return JSON.parse(value) } catch (_) { throw new Error(`${label} 必须是合法 JSON`) } }

function selectInitialType() {
  const requested = extensionTypes.value.find(item => item.code === props.initialKnowledgeType)
  if (requested) bindingTypeId.value = requested.id
  else if (!extensionTypes.value.some(item => item.id === bindingTypeId.value)) bindingTypeId.value = extensionTypes.value[0]?.id || ''
  syncBindingSelection()
}

function syncBindingSelection() {
  bindingProfileIds.value = (selectedType.value?.latest_index_profiles || selectedType.value?.index_profiles || []).filter(item => item.origin === 'manual').map(item => item.id)
  bindingMode.value = 'preserve'; bindingCollectionName.value = ''; bindingReuseId.value = ''
}

async function load() {
  loading.value = true; error.value = ''
  try {
    const [nextTypes, vectorIndexes, servingRows] = await Promise.all([api.knowledgeTypes(), api.vectorIndexes(), api.embeddingServings()])
    types.value = nextTypes; indexes.value = vectorIndexes.profiles; managedCollections.value = vectorIndexes.managed_collections || []; embeddingServings.value = servingRows
    const jobs = await Promise.all(managedCollections.value.map(async item => [item.id, await api.managedCollectionDeletionJobs(item.id)]))
    deletionJobs.value = Object.fromEntries(jobs)
    if (!embeddingServings.value.some(item => item.serving_code === indexForm.value.embedding_serving_id && item.is_enabled)) {
      indexForm.value.embedding_serving_id = embeddingServings.value.find(item => item.is_default && item.is_enabled)?.serving_code || ''
    }
    selectInitialType()
  } catch (err) { error.value = err.message }
  finally { loading.value = false }
}

async function saveBindings() {
  if (!selectedType.value) return
  error.value = ''; result.value = null
  try {
    result.value = await api.reviseKnowledgeTypeStorageBindings(selectedType.value.id, {
      index_profile_ids: bindingProfileIds.value,
      managed_collection_name: bindingMode.value === 'new' ? bindingCollectionName.value.trim() : '',
      reuse_managed_collection_id: bindingMode.value === 'reuse' ? bindingReuseId.value : null,
    })
    await load()
  } catch (err) { error.value = err.message }
}

async function createIndex() {
  error.value = ''; result.value = null
  try {
    const creating = indexForm.value.collection_mode === 'create'
    const reuse = creating ? (indexForm.value.reuse_managed_collection_id || null) : null
    result.value = await api.createIndexProfile({
      ...indexForm.value, collection_name: reuse ? '' : indexForm.value.collection_name, reuse_managed_collection_id: reuse,
      fields: parse(indexForm.value.fields, '字段映射'), storage_schema: creating ? parse(indexForm.value.storage_schema, 'Storage Schema') : null,
    })
    await load()
  } catch (err) { error.value = err.message }
}

async function indexAction(index, action) {
  error.value = ''; result.value = null
  try {
    if (action === 'archive') result.value = await api.archiveIndexProfile(index.id)
    else result.value = action === 'validate' ? await api.validateIndexProfile(index.id) : await api.publishIndexProfile(index.id)
    await load()
  } catch (err) { error.value = err.message }
}

async function reconcileCollection(item) {
  error.value = ''; result.value = null
  try { result.value = await api.reconcileManagedCollection(item.id); await load() }
  catch (err) { error.value = err.message }
}

async function requestCollectionDelete(item) {
  error.value = ''; result.value = null
  try {
    const check = await api.managedCollectionDeleteCheck(item.id); result.value = check
    if (!check.deletable) { error.value = check.blockers.map(blocker => blocker.message).join('；'); return }
    if (!window.confirm(`${check.warning}\n\nCollection：${check.collection_name}`)) return
    result.value = await api.deleteManagedCollection(item.id); await load()
  } catch (err) { error.value = err.message }
}

async function retryCollectionDelete(job) {
  error.value = ''; result.value = null
  try { result.value = await api.retryManagedCollectionDeletion(job.id); await load() }
  catch (err) { error.value = err.message }
}

watch(bindingTypeId, syncBindingSelection)
watch(() => indexForm.value.knowledge_type, code => {
  if (indexForm.value.collection_mode === 'create' && !indexForm.value.collection_name) indexForm.value.collection_name = defaultCollectionName(code)
})
onMounted(load)
</script>

<template>
  <section class="storage-profile-governance">
    <div class="section-heading"><div><h3>Storage Profile 高级治理</h3><p>集中维护 Index Profile、Storage Contract、Managed Collection 与 Embedding；物理变更不会修改输出 Schema 或 Quality Revision。</p></div><span class="badge amber">高级配置</span></div>
    <p v-if="loading" class="loading">正在加载 Storage Profile…</p>
    <template v-else>
      <form class="panel binding-form" @submit.prevent="saveBindings">
        <div class="panel-head"><div><h3>输出类型绑定</h3><p>只为扩展类型创建新的物理绑定 Revision；语义契约和已冻结 Quality Revision 原样克隆。</p></div></div>
        <label>扩展输出类型<select v-model="bindingTypeId"><option value="">选择扩展类型</option><option v-for="type in extensionTypes" :key="type.id" :value="type.id">{{ type.name }} · {{ type.code }}</option></select></label>
        <template v-if="selectedType">
          <label>附加已发布 Manual Profile<select v-model="bindingProfileIds" multiple><option v-for="profile in compatibleManualProfiles" :key="profile.id" :value="profile.id">{{ profile.code }} → {{ profile.collection_name }}</option></select></label>
          <label>系统默认 Profile 的 Collection<select v-model="bindingMode"><option value="preserve">保持当前绑定</option><option value="new">创建新的受管 Collection 契约</option><option value="reuse">复用兼容的受管 Collection</option></select></label>
          <input v-if="bindingMode==='new'" v-model="bindingCollectionName" required :placeholder="defaultCollectionName(selectedType.code)">
          <select v-if="bindingMode==='reuse'" v-model="bindingReuseId" required><option value="">选择可复用 Collection</option><option v-for="item in reusableCollections" :key="item.id" :value="item.id">{{ item.collection_name }}</option></select>
          <div class="binding-summary"><span>当前 Quality Revision</span><code>{{ (selectedType.latest_revision || selectedType.current_revision)?.quality_profile_revision_id || '—' }}</code><span>当前 Profile</span><code>{{ (selectedType.latest_index_profiles || selectedType.index_profiles)?.map(item => item.code).join(', ') || '—' }}</code></div>
          <button class="primary">保存 Storage Binding Revision</button>
        </template>
        <p v-else class="muted">请先在“知识流程 → 输出类型配置”创建扩展类型。</p>
      </form>

      <form class="panel index-form" @submit.prevent="createIndex">
        <div class="panel-head"><div><h3>新建 Manual Index Profile</h3><p>用于接入客户已有 Collection，或创建额外的 DataForge 受管 Collection。</p></div></div>
        <div class="form-grid"><label>Collection 来源<select v-model="indexForm.collection_mode"><option value="create">DataForge 创建受管 Collection</option><option value="attach">接入 external Collection</option></select></label><label>Profile 编码<input v-model="indexForm.code" required></label><label>知识类型<select v-model="indexForm.knowledge_type"><option v-for="type in types" :key="type.id" :value="type.code">{{ type.name }}</option></select></label></div>
        <div class="form-grid"><label>Collection 名<input v-model="indexForm.collection_name" :disabled="!!indexForm.reuse_managed_collection_id" required></label><label v-if="indexForm.collection_mode==='create'">复用兼容 Collection<select v-model="indexForm.reuse_managed_collection_id"><option value="">不复用</option><option v-for="item in reusableCollections" :key="item.id" :value="item.id">{{ item.collection_name }}</option></select></label><label>Embedding 服务<select v-model="indexForm.embedding_serving_id" required><option v-for="item in embeddingServings.filter(row => row.is_enabled)" :key="item.id" :value="item.serving_code">{{ item.is_default ? '★ ' : '' }}{{ item.name }} · {{ item.dimension }} 维</option></select></label></div>
        <div class="form-grid"><label>Model<input :value="selectedEmbeddingServing?.model_name || '—'" disabled></label><label>Dimension<input :value="selectedEmbeddingServing?.dimension || '—'" disabled></label><label>Metric<input v-model="indexForm.metric_type" required></label><label>Embedding Input<select v-model="indexForm.embedding_input"><option value="canonical_content">canonical_content</option><option value="question">question</option><option value="question_answer">question + answer</option></select></label></div>
        <div class="schema-grid"><label>字段映射<textarea v-model="indexForm.fields" rows="8" /></label><label v-if="indexForm.collection_mode==='create'">Storage Schema<textarea v-model="indexForm.storage_schema" rows="8" /></label><p v-else class="muted">external Collection 只校验和解绑，DataForge 不创建或删除整个 Collection。</p></div>
        <button class="primary">保存 Profile 草稿</button>
      </form>

      <section class="panel table-panel"><h3>受管 Collection</h3><p>DataForge-owned Collection 只有在引用解除并通过 ownership 预检后才能申请删除。</p><div class="table-wrap"><table><thead><tr><th>Collection</th><th>Contract</th><th>状态</th><th>Partition / 引用</th><th>删除任务</th><th>操作</th></tr></thead><tbody><tr v-for="item in managedCollections" :key="item.id"><td><code>{{ item.collection_name }}</code><br><small>{{ item.ownership_verified ? 'Ownership 已验证' : 'Ownership 待验证' }}</small></td><td>{{ item.storage_contract.code }} · r{{ item.storage_contract.revision }}</td><td><span :class="['badge', item.status==='ready'?'green':['failed','incompatible','delete_failed'].includes(item.status)?'red':'amber']">{{ item.status }}</span></td><td>{{ item.partition_names?.join(', ') || '无 kl_* Partition' }}<br><small>{{ item.references?.profile_ids?.length || 0 }} Profile / {{ item.references?.knowledge_libraries?.length || 0 }} 知识库</small></td><td><template v-if="deletionJobs[item.id]?.length"><span class="badge">{{ deletionJobs[item.id][0].status }}</span><button v-if="deletionJobs[item.id][0].status==='failed'" @click="retryCollectionDelete(deletionJobs[item.id][0])">重试</button></template><span v-else>—</span></td><td><div class="actions"><button v-if="['planned','failed','incompatible'].includes(item.status)" @click="reconcileCollection(item)">Provision</button><button v-if="managedCollectionCanRequestDelete(item)" class="danger" @click="requestCollectionDelete(item)">申请删除</button></div></td></tr></tbody></table></div></section>

      <section class="panel table-panel"><h3>Index Profile</h3><div class="table-wrap"><table><thead><tr><th>编码</th><th>类型 / 来源</th><th>Embedding</th><th>向量契约</th><th>Collection</th><th>策略</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="index in [...managedProfiles,...externalProfiles]" :key="index.id"><td><code>{{ index.code }}</code></td><td>{{ index.knowledge_type }}<br><small>{{ index.origin }}</small></td><td>{{ index.embedding_serving?.name || index.embedding_serving_id || '待绑定' }}<br><small>{{ index.embedding_serving?.model_name }}</small></td><td><template v-if="index.embedding_serving"><div>Serving {{ index.vector_contract?.embedding_serving_dimension ?? index.embedding_serving.dimension }}</div><div>Profile {{ index.vector_contract?.index_profile_dimension ?? index.dimension }}</div><div>Storage {{ managedCollections.find(item => item.id===index.managed_collection_id)?.storage_contract?.dimension || '—' }}</div><div>Milvus {{ index.vector_contract?.milvus_collection_dimension ?? '待连接' }}</div><span :class="['badge',index.vector_contract?.compatible?'green':'red']">{{ index.vector_contract?.compatible ? '✓ 维度一致' : '× 维度不一致' }}</span></template><span v-else>—</span></td><td><code>{{ index.collection_name }}</code></td><td>{{ index.revisions?.[0]?.collection_policy }}</td><td>{{ index.status }}</td><td><div class="actions"><button @click="indexAction(index,'validate')">校验</button><button v-if="index.status!=='active'&&index.status!=='archived'" class="primary" @click="indexAction(index,'publish')">发布</button><button v-if="index.origin==='manual'&&index.status!=='archived'" @click="indexAction(index,'archive')">归档/解绑</button></div></td></tr></tbody></table></div></section>
    </template>
    <p v-if="error" class="error">{{ error }}</p><pre v-if="result" class="action-result">{{ JSON.stringify(result,null,2) }}</pre>
  </section>
</template>

<style scoped>
.storage-profile-governance{display:grid;gap:18px}.binding-form,.index-form{display:grid;gap:14px}.binding-form label,.index-form label,.schema-grid label{display:grid;gap:6px}.binding-form select[multiple]{min-height:120px}.binding-summary{display:grid;grid-template-columns:170px minmax(0,1fr);gap:8px 12px;padding:12px;border-radius:9px;background:var(--panel-muted)}.form-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.schema-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.table-panel{display:grid;gap:10px}.table-panel table{min-width:1060px}.table-panel small,.muted{color:var(--muted)}.danger{color:#b42318}
</style>
