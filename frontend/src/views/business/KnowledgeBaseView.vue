<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api/platform'
import GraphBrowser from '../../components/GraphBrowser.vue'

const libraries = ref([]), types = ref([]), selected = ref(null), items = ref([]), changes = ref([]), vector = ref(null), sources = ref([]), deletion = ref(null), deletionJobs = ref([])
const name = ref(''), type = ref('text'), graphMode = ref(null), tab = ref('content'), error = ref('')
const typeCards = [
  { key: 'text', family: 'text', mode: null, icon: '文', name: '文本知识', detail: '文档片段与结构化文本' },
  { key: 'qa', family: 'qa', mode: null, icon: '问', name: '问答知识', detail: '问题与完整答案' },
  { key: 'graph:triple', family: 'graph', mode: 'triple', icon: '△', name: '三元组图谱', detail: '适用于明确 S / P / O 关系' },
  { key: 'graph:semantic', family: 'graph', mode: 'semantic', icon: '⬡', name: '语义图谱', detail: '实体、关系描述与 Evidence' },
]
async function load() { [libraries.value, types.value] = await Promise.all([api.knowledgeLibraries(), api.knowledgeTypes()]) }
async function choose(library) {
  selected.value = library; tab.value = library.knowledge_type === 'graph' ? 'graph' : 'content'; error.value = ''; deletion.value = null
  items.value = library.knowledge_type === 'qa' ? await api.qaPairs(library.id) : await api.knowledgeItems(library.id)
  ;[changes.value, vector.value, deletionJobs.value] = await Promise.all([api.changes(library.id), api.vectorStatus(library.id), api.deletionJobs(library.id)])
}
function selectType(card) { type.value = card.family; graphMode.value = card.mode }
async function create() { try { await api.createKnowledgeLibrary({ name: name.value, knowledge_type: type.value, graph_mode: graphMode.value }); name.value = ''; await load() } catch (e) { error.value = e.message } }
async function trace(item) { try { sources.value = await api.knowledgeItemSources(item.id); tab.value = 'sources' } catch (e) { error.value = e.message } }
async function checkDelete() { try { deletion.value = await api.knowledgeLibraryDeleteCheck(selected.value.id) } catch (e) { error.value = e.message } }
async function remove() { try { if (!deletion.value?.deletable) return; if (!window.confirm('将异步清理该知识库的 V7 Partition，确认继续？')) return; deletion.value = await api.deleteKnowledgeLibrary(selected.value.id); deletionJobs.value = await api.deletionJobs(selected.value.id); await load() } catch (e) { error.value = e.message } }
async function retryDeletion(job) { try { await api.retryDeletion(job.id); deletionJobs.value = await api.deletionJobs(selected.value.id) } catch (e) { error.value = e.message } }
onMounted(load)
</script>

<template>
  <section><div class="page-head"><div><h2>知识库</h2><p>集中管理正式知识、来源、变更记录、向量状态和安全删除。</p></div><div class="page-actions"><button class="primary" @click="$el.querySelector('form input')?.focus()">+ 新建知识库</button></div></div>
    <form class="library-create" @submit.prevent="create"><input v-model="name" required placeholder="知识库名称"><div class="type-cards"><button v-for="card in typeCards" :key="card.key" type="button" :class="{selected:type===card.family && graphMode===card.mode}" @click="selectType(card)"><span>{{ card.icon }}</span><b>{{ card.name }}</b><small>{{ card.detail }}</small></button></div><button class="primary">新建知识库</button></form>
    <p v-if="error" class="error">{{ error }}</p>
    <div class="cards"><button v-for="library in libraries" :key="library.id" type="button" :class="{active:selected?.id===library.id}" @click="choose(library)"><b>{{ library.graph_mode==='triple'?'△':library.graph_mode==='semantic'?'⬡':types.find(item=>item.code===library.knowledge_type)?.icon || '知' }} {{ library.name }}</b><small>{{ library.display_type || library.knowledge_type }} · {{ library.code }} · {{ library.status }}</small><small>Vector {{ library.vector_ready ? 'Ready' : '未就绪' }}</small></button></div>
    <section v-if="selected"><header class="detail-head"><h3>{{ selected.name }}</h3><div><button @click="checkDelete">删除影响检查</button><button class="danger" :disabled="!deletion?.deletable" @click="remove">安全删除</button></div></header>
      <nav class="tabs"><button :class="{active:tab==='content'}" @click="tab='content'">知识内容</button><button :class="{active:tab==='diff'}" @click="tab='diff'">Knowledge Diff</button><button :class="{active:tab==='vector'}" @click="tab='vector'">向量状态</button><button :class="{active:tab==='sources'}" @click="tab='sources'">来源追踪</button><button v-if="selected.knowledge_type==='graph'" :class="{active:tab==='graph'}" @click="tab='graph'">图谱浏览器</button></nav>
      <div v-if="tab==='content'"><table><thead><tr><th>内容</th><th>来源</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="item in items" :key="item.id"><td v-if="selected.knowledge_type==='qa'"><b>Q:</b> {{ item.data.question }}<br><b>A:</b> {{ item.data.answer }}</td><td v-else>{{ item.canonical_content }}</td><td>{{ item.source_count || item.source_version_ids.length }}</td><td>{{ item.status }}</td><td><button @click="trace(item)">查看来源</button></td></tr></tbody></table></div>
      <div v-else-if="tab==='diff'"><table><thead><tr><th>类型</th><th>前</th><th>后</th><th>时间</th></tr></thead><tbody><tr v-for="change in changes" :key="change.id"><td>{{ change.change_type }}</td><td>{{ change.before?.content || change.before_hash || '—' }}</td><td>{{ change.after?.content || change.after_hash || '—' }}</td><td>{{ change.created_at }}</td></tr></tbody></table></div>
      <div v-else-if="tab==='vector'"><p>Ready：{{ vector?.ready ? '是' : '否' }}</p><pre>{{ JSON.stringify(vector, null, 2) }}</pre></div>
      <div v-else-if="tab==='sources'"><p v-if="!sources.length">从“知识内容”选择一个知识项查看来源。</p><article v-for="source in sources" :key="source.id" class="source-card"><b>{{ source.source.original_filename }} · v{{ source.source_version.version_no }}</b><p>{{ source.anchor.label || source.anchor.file }}{{ source.anchor.chunk_index !== undefined ? ` / chunk-${source.anchor.chunk_index}` : '' }}</p><p>{{ source.evidence_text }}</p></article></div>
      <GraphBrowser v-else-if="tab==='graph'" :library-id="selected.id" />
      <details v-if="deletion" open><summary>删除检查</summary><pre>{{ JSON.stringify(deletion, null, 2) }}</pre></details><section v-if="deletionJobs.length" class="panel"><h3>Partition 清理任务</h3><table><thead><tr><th>任务</th><th>状态</th><th>同步数</th><th>操作</th></tr></thead><tbody><tr v-for="job in deletionJobs" :key="job.id"><td>{{ job.id }}</td><td><span class="badge" :class="job.status==='failed'?'red':job.status==='completed'?'green':'amber'">{{ job.status }}</span></td><td>{{ job.deleted_count ?? '—' }}</td><td><button v-if="job.status==='failed'" @click="retryDeletion(job)">重试</button></td></tr></tbody></table></section>
    </section>
  </section>
</template>

<style scoped>
.detail-head{display:flex;align-items:center;justify-content:space-between}.tabs{display:flex;gap:8px;border-bottom:1px solid #d8dee9;margin:12px 0}.tabs button{border:0;background:transparent;padding:8px}.tabs .active{border-bottom:2px solid #2563eb}.danger{color:#b91c1c}.source-card{border:1px solid #d8dee9;border-radius:8px;margin:8px 0;padding:10px}
.library-create{display:grid;gap:12px}.type-cards{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px}.type-cards button{display:grid;gap:4px;text-align:left;padding:14px;background:#fff;border:1px solid #dbe3ef;border-radius:12px}.type-cards button.selected{border-color:#2563eb;box-shadow:0 0 0 2px #dbeafe}.type-cards span{font-size:24px;color:#2563eb}.type-cards small{color:#64748b}@media(max-width:900px){.type-cards{grid-template-columns:1fr 1fr}}
</style>
