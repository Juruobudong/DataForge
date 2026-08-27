<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'
import OperatorInspector from '../../components/flow/inspector/OperatorInspector.vue'
import OperatorPluginManager from '../../components/flow/OperatorPluginManager.vue'

const EXPOSURE_LABELS = { public: '可直接使用', controlled: '受控使用', internal: '系统内部', disabled: '已禁用' }
const PROVIDERS = { dataforge: 'DataForge', dataflow: 'DataFlow', custom: 'Custom' }
const catalog = ref([]), facets = ref({ categories: [], knowledge_types: [], statuses: [] })
const query = ref(''), category = ref(''), knowledge = ref(''), exposure = ref(''), status = ref('')
const selected = ref(null), error = ref('')

const visible = computed(() => catalog.value.filter(item =>
  (!query.value || `${item.display_name_zh} ${item.code} ${item.summary}`.toLowerCase().includes(query.value.toLowerCase())) &&
  (!category.value || item.category === category.value) &&
  (!knowledge.value || item.knowledge_types?.includes('*') || item.knowledge_types?.includes(knowledge.value)) &&
  (!exposure.value || item.exposure === exposure.value) &&
  (!status.value || item.status === status.value)
))

async function load() {
  error.value = ''
  try { [catalog.value, facets.value] = await Promise.all([api.operatorCatalog({ include_internal: true }), api.operatorCatalogFacets()]) }
  catch (e) { error.value = e.message }
}
onMounted(load)
</script>

<template>
  <section class="catalog-page">
    <div class="page-head"><div><h2>算子组件</h2><p>查看精选算子契约、版本与运行依赖；自定义算子仅注册并验证已安装的审核包，不接受在线 Python 源码。</p></div><span class="badge blue">{{ visible.length }} / {{ catalog.length }}</span></div>
    <div class="catalog-filters">
      <input v-model="query" placeholder="搜索名称、编码或说明">
      <select v-model="category"><option value="">全部分类</option><option v-for="item in facets.categories" :key="item.name" :value="item.name">{{ item.name }} ({{ item.count }})</option></select>
      <select v-model="knowledge"><option value="">全部知识类型</option><option v-for="item in facets.knowledge_types" :key="item" :value="item">{{ item }}</option></select>
      <select v-model="exposure"><option value="">全部暴露级别</option><option v-for="(label, value) in EXPOSURE_LABELS" :key="value" :value="value">{{ label }}</option></select>
      <select v-model="status"><option value="">全部生命周期</option><option v-for="item in facets.statuses" :key="item" :value="item">{{ item }}</option></select>
    </div>
    <div class="catalog-layout">
      <div class="panel catalog-list">
        <button v-for="item in visible" :key="item.id" class="operator-row" :class="{ active: selected?.id === item.id }" @click="selected = item">
          <div><b>{{ item.display_name_zh || item.name }}</b><small>{{ item.code }} · v{{ item.version }} · {{ PROVIDERS[item.provider] }} · {{ item.category }}</small><p>{{ item.summary }}</p><small v-if="item.dependency_status?.status !== 'ready'">{{ item.dependency_status?.reason || '依赖状态未知' }}</small></div>
          <span class="badge" :class="item.exposure === 'public' ? 'green' : 'amber'">{{ EXPOSURE_LABELS[item.exposure] }}</span>
        </button>
        <p v-if="!visible.length" class="empty-catalog">没有匹配的算子。</p>
      </div>
      <OperatorInspector :operator="selected || visible[0]" />
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <OperatorPluginManager @published="load" />
  </section>
</template>

<style scoped>
.catalog-page{display:grid;gap:14px}
.catalog-filters{display:flex;flex-wrap:wrap;gap:8px}.catalog-filters input{flex:1;min-width:220px}.catalog-filters select{min-width:150px}
.catalog-layout{display:grid;grid-template-columns:minmax(600px,1fr) 380px;gap:12px;min-height:650px;align-items:start}
.catalog-list{overflow:auto}
.empty-catalog{margin:0;padding:16px;color:#8491a3}
.operator-row{display:flex;width:100%;justify-content:space-between;gap:16px;margin-top:6px;padding:12px;text-align:left}
.operator-row.active{border-color:#2f6fed;background:#f1f6ff}
.operator-row small{display:block;margin-top:3px;color:#7b8798}
.operator-row p{margin:6px 0 0;color:#5d6a7c}
@media(max-width:1100px){.catalog-layout{grid-template-columns:1fr}}
</style>
