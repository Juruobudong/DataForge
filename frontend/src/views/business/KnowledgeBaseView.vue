<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/platform'

const router = useRouter()
const libraries = ref([]), types = ref([]), keyword = ref(''), error = ref('')
const drawerOpen = ref(false), name = ref(''), type = ref('text'), graphMode = ref(null), creating = ref(false)
const builtinCards = [
  { key: 'text', family: 'text', mode: null, icon: '文', name: '文本知识', detail: '文档片段与结构化文本' },
  { key: 'qa', family: 'qa', mode: null, icon: '问', name: '问答知识', detail: '问题与完整答案' },
  { key: 'graph:triple', family: 'graph', mode: 'triple', icon: '△', name: '三元组图谱', detail: '适用于明确 S / P / O 关系' },
  { key: 'graph:semantic', family: 'graph', mode: 'semantic', icon: '⬡', name: '语义图谱', detail: '实体、关系描述与 Evidence' },
]

async function load() {
  try { [libraries.value, types.value] = await Promise.all([api.knowledgeLibraries(), api.knowledgeTypes()]); error.value = '' } catch (err) { error.value = err.message }
}

const additionalCards = computed(() => types.value
  .filter(item => !['text', 'qa', 'graph'].includes(item.code))
  .map(item => ({ key: item.code, family: item.code, mode: null, icon: item.icon || '知', name: item.name || item.code, detail: '扩展知识类型' })))
const typeCards = computed(() => [...builtinCards, ...additionalCards.value])
const visibleLibraries = computed(() => {
  const needle = keyword.value.trim().toLocaleLowerCase()
  if (!needle) return libraries.value
  return libraries.value.filter(item => [item.name, item.code, item.id, item.display_type, item.knowledge_type]
    .filter(Boolean).some(value => String(value).toLocaleLowerCase().includes(needle)))
})
const libraryGroups = computed(() => typeCards.value.map(card => {
  const values = visibleLibraries.value.filter(item => item.knowledge_type === card.family && (card.mode === null || item.graph_mode === card.mode))
  const allValues = libraries.value.filter(item => item.knowledge_type === card.family && (card.mode === null || item.graph_mode === card.mode))
  return { ...card, libraries: values, libraryCount: allValues.length, itemCount: allValues.reduce((sum, item) => sum + (item.knowledge_item_count || 0), 0) }
}).filter(group => group.libraryCount || builtinCards.some(card => card.key === group.key)))

function selectType(card) { type.value = card.family; graphMode.value = card.mode }
function openDrawer() { drawerOpen.value = true; error.value = '' }
function closeDrawer() { if (!creating.value) drawerOpen.value = false }
async function create() {
  creating.value = true
  try {
    const library = await api.createKnowledgeLibrary({ name: name.value, knowledge_type: type.value, graph_mode: graphMode.value })
    name.value = ''; drawerOpen.value = false; await load(); router.push(`/business/knowledge/${library.id}`)
  } catch (err) { error.value = err.message } finally { creating.value = false }
}
function openLibrary(library) { router.push(`/business/knowledge/${library.id}`) }
function typeName(library) { return library.display_type || types.value.find(item => item.code === library.knowledge_type)?.name || library.knowledge_type }
function shortId(value) { return value?.length > 18 ? `${value.slice(0, 18)}…` : value }
onMounted(load)
</script>

<template>
  <section class="knowledge-overview">
    <div class="page-head"><div><h2>知识库</h2><p>按知识类型查看成果库、活跃知识量和向量就绪状态。</p></div><div class="page-actions"><button class="primary" @click="openDrawer">+ 新建知识库</button></div></div>
    <div class="knowledge-search"><label class="sr-only" for="knowledge-search">搜索知识库</label><input id="knowledge-search" v-model="keyword" placeholder="搜索知识库名称或技术 ID"></div>
    <p v-if="error" class="error">{{ error }}</p>
    <section class="type-summary" aria-label="知识类型总览"><article v-for="group in libraryGroups" :key="group.key"><span class="type-icon">{{ group.icon }}</span><div><b>{{ group.name }}</b><small>{{ group.libraryCount }} 个知识库 · {{ group.itemCount.toLocaleString() }} 条知识</small></div></article></section>
    <section v-for="group in libraryGroups" :key="`list-${group.key}`" class="knowledge-group"><div class="section-heading"><div><h3>{{ group.name }}</h3><p>{{ group.detail }} · 共 {{ group.libraryCount }} 个</p></div></div><div v-if="group.libraries.length" class="library-grid"><button v-for="library in group.libraries" :key="library.id" class="library-card" type="button" @click="openLibrary(library)"><span class="library-type">{{ group.icon }} {{ typeName(library) }}</span><b>{{ library.name }}</b><span class="library-metrics"><span>{{ (library.knowledge_item_count || 0).toLocaleString() }} 条知识</span><span v-if="library.status === 'deleting'" class="badge amber">正在删除</span><span v-else :class="['badge', library.vector_ready ? 'green' : 'amber']">向量 {{ library.vector_ready ? '就绪' : '未就绪' }}</span></span><small :title="library.id">{{ shortId(library.code || library.id) }} · {{ library.status === 'deleting' ? '等待 Partition 清理完成' : `更新于 ${new Date(library.updated_at).toLocaleString()}` }}</small></button></div><p v-else-if="keyword" class="empty-group">没有匹配的知识库。</p><p v-else class="empty-group">暂未创建此类知识库。</p></section>
    <div v-if="drawerOpen" class="drawer-backdrop" @click.self="closeDrawer"><aside class="knowledge-drawer" aria-label="新建知识库"><header><div><h3>新建知识库</h3><p>选择受控类型后创建空知识库。</p></div><button aria-label="关闭" @click="closeDrawer">×</button></header><form class="stack" @submit.prevent="create"><label>知识库名称<input v-model="name" required autofocus placeholder="例如：骨科图谱知识库"></label><fieldset><legend>知识类型</legend><div class="create-type-grid"><button v-for="card in typeCards" :key="card.key" type="button" :class="{ active: type === card.family && graphMode === card.mode }" @click="selectType(card)"><span>{{ card.icon }}</span><b>{{ card.name }}</b><small>{{ card.detail }}</small></button></div></fieldset><div class="drawer-actions"><button type="button" @click="closeDrawer">取消</button><button class="primary" :disabled="creating">{{ creating ? '正在创建…' : '创建知识库' }}</button></div></form></aside></div>
  </section>
</template>

<style scoped>
.knowledge-search{max-width:640px;margin:0 0 20px}.knowledge-search input{width:100%}.type-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:30px}.type-summary article{display:flex;min-width:0;align-items:center;gap:12px;padding:18px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow)}.type-summary b,.type-summary small{display:block}.type-summary b{font-size:16px}.type-summary small{margin-top:5px;color:var(--muted);font-size:13px}.type-icon{display:grid;width:42px;height:42px;flex:0 0 auto;place-items:center;border-radius:12px;color:var(--blue);background:var(--blue-soft);font-size:20px;font-weight:800}.knowledge-group{margin-top:28px}.section-heading h3{margin:0;font-size:20px}.section-heading p{margin:6px 0 12px;color:var(--muted);font-size:13px}.library-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.library-card{display:grid;min-width:0;min-height:158px;align-content:start;gap:9px;padding:18px;text-align:left}.library-card:hover{border-color:#b9cff7;box-shadow:0 10px 28px rgba(47,111,237,.12)}.library-card>b{overflow:hidden;font-size:16px;text-overflow:ellipsis;white-space:nowrap}.library-type{color:var(--blue);font-size:13px;font-weight:800}.library-metrics{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:auto;color:#536177;font-size:14px}.library-card small{overflow:hidden;color:var(--muted);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.empty-group{margin:0;padding:24px;border:1px dashed var(--border);border-radius:var(--radius);color:var(--muted);background:var(--panel-muted)}.drawer-backdrop{position:fixed;inset:0;z-index:50;background:rgba(17,24,39,.35)}.knowledge-drawer{position:absolute;top:0;right:0;width:min(100%,520px);min-height:100%;padding:28px;overflow:auto;background:#fff;box-shadow:-12px 0 34px rgba(17,24,39,.16)}.knowledge-drawer header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:24px}.knowledge-drawer h3{margin:0;font-size:20px}.knowledge-drawer header p{margin:6px 0 0;color:var(--muted);font-size:13px}.knowledge-drawer header button{min-width:38px;padding:0;font-size:22px}.knowledge-drawer fieldset{width:100%;margin:0;padding:14px;border:1px solid var(--border);border-radius:12px}.knowledge-drawer legend{padding:0 6px;color:#566379;font-size:13px;font-weight:800}.create-type-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.create-type-grid button{min-height:108px;padding:13px;text-align:left}.create-type-grid button.active{border-color:var(--blue);background:var(--blue-soft)}.create-type-grid span,.create-type-grid b,.create-type-grid small{display:block}.create-type-grid span{font-size:20px}.create-type-grid b{margin-top:8px;font-size:15px}.create-type-grid small{margin-top:4px;color:var(--muted);font-size:12px;font-weight:500}.drawer-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:8px}@media(max-width:1440px){.type-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.library-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:1100px){.library-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:900px){.type-summary,.library-grid{grid-template-columns:1fr}.knowledge-drawer{width:100%;padding:22px}.create-type-grid{grid-template-columns:1fr}}
</style>
<style scoped>
@media (min-width: 901px) and (max-width: 1440px) {
  .library-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
</style>
