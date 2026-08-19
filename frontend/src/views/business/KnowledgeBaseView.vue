<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/platform'

const router = useRouter()
const libraries = ref([]), types = ref([]), keyword = ref(''), error = ref('')
const deletingId = ref('')
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

function openLibrary(library) { router.push(`/business/knowledge/${library.id}`) }
async function requestDelete(library) {
  if (deletingId.value) return
  deletingId.value = library.id; error.value = ''
  try {
    const check = await api.knowledgeLibraryDeleteCheck(library.id)
    if (!check.deletable) {
      const routes = check.references || []
      const jobs = check.active_job_references || []
      if (routes.length) window.alert('该知识库仍被项目路由引用。请先从项目路由移除并重新发布，再删除知识库。')
      else if (jobs.length) window.alert(`该知识库仍有 ${jobs.length} 个排队或运行中的处理任务。请等待任务结束或先停止任务，再删除知识库。`)
      else window.alert('该知识库仍被引用，暂不能删除。')
      return
    }
    const bindings = check.template_binding_references || []
    let message = '将异步清理该知识库的知识内容和 V7 Partition，确认继续？'
    if (bindings.length) {
      const sources = [...new Set(bindings.map(item => `${item.document_library_name} / ${item.template_name || item.template_code}`))]
      message += `\n\n该知识库关联：${sources.join('、')}。删除不会解除模板绑定；清理完成后，下次主动处理文档库将创建新知识库并全量重跑该模板。若模板还有其他输出，它们也会按正常 Diff 刷新。`
    }
    if (!window.confirm(message)) return
    await api.deleteKnowledgeLibrary(library.id)
    await load()
  } catch (err) { error.value = err.message } finally { deletingId.value = '' }
}
function typeName(library) { return library.display_type || types.value.find(item => item.code === library.knowledge_type)?.name || library.knowledge_type }
function shortId(value) { return value?.length > 18 ? `${value.slice(0, 18)}…` : value }
onMounted(load)
</script>

<template>
  <section class="knowledge-overview">
    <div class="page-head"><div><h2>知识库</h2><p>按知识类型查看由文档处理或迁移产生的成果库、活跃知识量和向量就绪状态。</p></div></div>
    <div class="knowledge-search"><label class="sr-only" for="knowledge-search">搜索知识库</label><input id="knowledge-search" v-model="keyword" placeholder="搜索知识库名称或技术 ID"></div>
    <p v-if="error" class="error">{{ error }}</p>
    <section class="type-summary" aria-label="知识类型总览"><article v-for="group in libraryGroups" :key="group.key"><span class="type-icon">{{ group.icon }}</span><div><b>{{ group.name }}</b><small>{{ group.libraryCount }} 个知识库 · {{ group.itemCount.toLocaleString() }} 条知识</small></div></article></section>
    <section v-if="!libraries.length" class="empty-guidance"><b>尚无知识库</b><p>请前往文档管理，为文档库绑定已发布的知识流程模板；处理文档后会自动生成结果知识库。也可以通过知识库迁移导入已有成果。</p><button class="primary" type="button" @click="router.push('/business/documents')">前往文档管理</button></section>
    <template v-else><section v-for="group in libraryGroups" :key="`list-${group.key}`" class="knowledge-group"><div class="section-heading"><div><h3>{{ group.name }}</h3><p>{{ group.detail }} · 共 {{ group.libraryCount }} 个</p></div></div><div v-if="group.libraries.length" class="library-grid"><div v-for="library in group.libraries" :key="library.id" class="library-card"><button class="library-card-main" type="button" @click="openLibrary(library)"><span class="library-type">{{ group.icon }} {{ typeName(library) }}</span><b>{{ library.name }}</b><span><span class="badge" :class="library.origin_state==='forked'?'amber':library.origin_type==='central_import'?'blue':'green'">{{ library.origin_state==='forked'?'已本地修改':library.origin_type==='central_import'?'中心迁入':'本地创建' }}</span></span><span class="library-metrics"><span>{{ (library.knowledge_item_count || 0).toLocaleString() }} 条知识</span><span v-if="library.status === 'deleting'" class="badge amber">正在删除</span><span v-else :class="['badge', library.vector_ready ? 'green' : 'amber']">向量 {{ library.vector_ready ? '就绪' : '未就绪' }}</span></span><small :title="library.id">{{ shortId(library.code || library.id) }} · {{ library.status === 'deleting' ? '等待 Partition 清理完成' : `更新于 ${new Date(library.updated_at).toLocaleString()}` }}</small></button><div class="library-card-actions"><button v-if="library.status !== 'deleting'" class="danger" :disabled="deletingId === library.id" @click="requestDelete(library)">{{ deletingId === library.id ? '检查中…' : '删除' }}</button></div></div></div><p v-else-if="keyword" class="empty-group">没有匹配的知识库。</p><p v-else class="empty-group">暂无此类知识库。</p></section></template>
  </section>
</template>

<style scoped>
  .knowledge-search{max-width:640px;margin:0 0 20px}.knowledge-search input{width:100%}.type-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:30px}.type-summary article{display:flex;min-width:0;align-items:center;gap:12px;padding:18px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow)}.type-summary b,.type-summary small{display:block}.type-summary b{font-size:16px}.type-summary small{margin-top:5px;color:var(--muted);font-size:13px}.type-icon{display:grid;width:42px;height:42px;flex:0 0 auto;place-items:center;border-radius:12px;color:var(--blue);background:var(--blue-soft);font-size:20px;font-weight:800}.knowledge-group{margin-top:28px}.section-heading h3{margin:0;font-size:20px}.section-heading p{margin:6px 0 12px;color:var(--muted);font-size:13px}.library-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.library-card{display:flex;flex-direction:column;min-width:0;min-height:158px;gap:9px;padding:18px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow);text-align:left}.library-card:hover{border-color:#b9cff7;box-shadow:0 10px 28px rgba(47,111,237,.12)}.library-card-main{display:grid;flex:1 1 auto;min-width:0;align-content:start;gap:9px;padding:0;border:0;background:transparent;text-align:left}.library-card-main>b{overflow:hidden;font-size:16px;text-overflow:ellipsis;white-space:nowrap}.library-card-actions{display:flex;justify-content:flex-end;margin-top:2px}.library-type{color:var(--blue);font-size:13px;font-weight:800}.library-metrics{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:auto;color:#536177;font-size:14px}.library-card small{overflow:hidden;color:var(--muted);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.empty-group,.empty-guidance{margin:0;padding:24px;border:1px dashed var(--border);border-radius:var(--radius);color:var(--muted);background:var(--panel-muted)}.empty-guidance{display:grid;justify-items:start;gap:10px}.empty-guidance b{color:var(--text);font-size:17px}.empty-guidance p{max-width:720px;margin:0}.empty-guidance button{margin-top:4px}@media(max-width:1440px){.type-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.library-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:1100px){.library-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:900px){.type-summary,.library-grid{grid-template-columns:1fr}}
</style>
<style scoped>
@media (min-width: 901px) and (max-width: 1440px) {
  .library-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
</style>
