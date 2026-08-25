<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'
import SourcePreviewPane from '../../components/source-review/SourcePreviewPane.vue'
import ChunkCard from '../../components/source-review/ChunkCard.vue'
import RechunkDialog from '../../components/source-review/RechunkDialog.vue'
import SplitChunkDialog from '../../components/source-review/SplitChunkDialog.vue'
import { shouldPollPreparation } from './documentReviewModel'

const route = useRoute(), router = useRouter()
const libraryId = route.params.libraryId, sourceId = route.params.sourceId, versionId = route.params.versionId
const detail = ref(null), review = ref(null), latestChunker = ref(null), error = ref(''), busy = ref(false)
const keyword = ref(''), filter = ref('all'), selectedIds = ref([]), selectedPage = ref(1), focusedChunkId = ref('')
const editingId = ref(''), editContent = ref(''), showRechunk = ref(false), showSplit = ref(false), splitTarget = ref(null)
let pollTimer = null

const chunks = computed(() => (review.value?.chunks || []).filter(item => {
  const matchesStatus = filter.value === 'all' || item.review_status === filter.value
  return matchesStatus && (!keyword.value.trim() || item.content.toLowerCase().includes(keyword.value.trim().toLowerCase()))
}))
const selectedChunks = computed(() => (review.value?.chunks || []).filter(item => selectedIds.value.includes(item.id)))
const reviewableChunks = computed(() => chunks.value.filter(item => item.review_status !== 'approved'))
const allSelected = computed(() => reviewableChunks.value.length > 0 && reviewableChunks.value.every(item => selectedIds.value.includes(item.id)))
const someSelected = computed(() => !allSelected.value && reviewableChunks.value.some(item => selectedIds.value.includes(item.id)))
const canComplete = computed(() => Number(review.value?.counts?.total || 0) > 0 && Number(review.value?.counts?.approved || 0) === Number(review.value?.counts?.total || 0))
const prep = computed(() => detail.value?.preparation || {})

function schedulePolling() {
  clearInterval(pollTimer); pollTimer = null
  if (shouldPollPreparation(prep.value.status)) pollTimer = setInterval(load, 2000)
}
async function load() {
  try {
    const nextDetail = await api.sourceDetail(sourceId, versionId)
    detail.value = nextDetail
    review.value = await api.sourceReview(versionId, nextDetail.chunk_sets?.review_target_id || '')
    schedulePolling()
  } catch (e) { error.value = e.message; clearInterval(pollTimer); pollTimer = null }
}
async function run(action) { busy.value = true; error.value = ''; try { await action(); await load() } catch (e) { error.value = e.message } finally { busy.value = false } }
function focusChunk(chunk) { focusedChunkId.value = chunk.id; if (chunk.anchor?.page) selectedPage.value = Number(chunk.anchor.page) }
function selectChunk(chunk, checked) { selectedIds.value = checked ? [...new Set([...selectedIds.value, chunk.id])] : selectedIds.value.filter(id => id !== chunk.id) }
function startEdit(chunk) { editingId.value = chunk.id; editContent.value = chunk.content; focusChunk(chunk) }
async function saveChunk(chunk) { await run(() => api.updateSourceChunk(chunk.id, { content: editContent.value, expected_revision_no: chunk.revision_no })); editingId.value = '' }
function openSplit(chunk) { splitTarget.value = chunk; showSplit.value = true }
async function confirmSplit(parts) { const chunk = splitTarget.value; showSplit.value = false; splitTarget.value = null; await run(() => api.splitSourceChunk(chunk.id, { parts, expected_revision_no: chunk.revision_no })) }
async function removeChunk(chunk) { if (confirm('删除该文档块？审核修订仍会保留。')) await run(() => api.deleteSourceChunk(chunk.id, chunk.revision_no)) }
async function reviewChunk(chunk, status) { await run(() => api.reviewSourceChunk(chunk.id, { status, expected_revision_no: chunk.revision_no })) }
async function reopenChunk(chunk) { await run(() => api.reopenSourceChunk(chunk.id)) }
async function mergeSelected() { const values = selectedChunks.value; await run(() => api.mergeSourceChunks({ chunk_ids: values.map(item => item.id), expected_revisions: Object.fromEntries(values.map(item => [item.id, item.revision_no])) })); selectedIds.value = [] }
async function batch(action) { const values = selectedChunks.value; await run(() => api.batchReviewSourceChunks(versionId, { chunk_ids: values.map(item => item.id), action, expected_revisions: Object.fromEntries(values.map(item => [item.id, item.revision_no])) })); selectedIds.value = [] }
function toggleSelectAll(checked) { selectedIds.value = checked ? reviewableChunks.value.map(item => item.id) : [] }
async function approveAll() { const values = reviewableChunks.value; if (!values.length) return; await run(() => api.batchReviewSourceChunks(versionId, { chunk_ids: values.map(item => item.id), action: 'approve', expected_revisions: Object.fromEntries(values.map(item => [item.id, item.revision_no])) })) }
async function completeReview() { await run(() => api.approveSourceReview(versionId)) }
async function retryPreparation() { await run(() => api.retrySourcePreparation(versionId)) }
async function openRechunk() { try { latestChunker.value = await api.sourcePreparationChunker(); showRechunk.value = true } catch (e) { error.value = e.message } }
async function rechunk(executionSnapshotId) { showRechunk.value = false; await run(() => api.rechunkSourceVersion(versionId, executionSnapshotId)) }

onMounted(load)
onBeforeUnmount(() => clearInterval(pollTimer))
</script>

<template>
  <section>
    <div class="page-head"><div><button @click="router.push(`/business/documents/${libraryId}`)">← 返回文档库</button><p>业务工作区 / 文档管理 / 文档审核</p><h2>{{ detail?.source?.original_filename || '文档审核' }}</h2><p>{{ review?.review_status || prep.status || '加载中' }} · {{ review?.counts?.total || 0 }} Chunk</p></div><div class="page-actions"><button @click="openRechunk">重新分块</button><a v-if="detail" :href="api.sourceDownloadUrl(sourceId, versionId)">下载原文</a></div></div>
    <section class="panel summary"><div><b>Source Preparation r{{ detail?.chunker?.revision || '—' }}</b><span>{{ detail?.chunker?.params?.chunk_size || '—' }} 字符 · Overlap {{ detail?.chunker?.params?.overlap_percent ?? '—' }}% · {{ detail?.chunker?.params?.preserve_page_boundary ? '不跨页' : '允许跨页' }}</span></div><div v-if="detail?.chunk_sets?.active"><b>当前生效</b><span>{{ detail.chunk_sets.active.chunk_count }} Chunk · {{ detail.chunk_sets.active.status }}</span></div><div v-if="detail?.chunk_sets?.candidate"><b>候选</b><span>{{ detail.chunk_sets.candidate.chunk_count }} Chunk · {{ detail.chunk_sets.candidate.status }}</span></div><button v-if="prep.status === 'failed'" class="primary" @click="retryPreparation">重试解析与分块</button></section>
    <p v-if="prep.status === 'queued' || prep.status === 'running'" class="notice">{{ prep.current_node ? `正在执行 ${prep.current_node}` : 'Source Preparation 正在运行' }} · {{ prep.completed_nodes || 0 }}/{{ prep.total_nodes || 0 }}</p>
    <div class="workbench">
      <SourcePreviewPane :source="detail?.source" :version="detail?.source?.version" :document-ir="detail?.document_ir" :selected-page="selectedPage" />
      <section class="chunk-pane"><header><div><h3>Chunk 审核</h3><span>待审 {{ review?.counts?.pending_review || 0 }} · 通过 {{ review?.counts?.approved || 0 }} · 拒绝 {{ review?.counts?.rejected || 0 }}</span></div><div class="filters"><input v-model="keyword" placeholder="搜索 Chunk"><select v-model="filter"><option value="all">全部</option><option value="pending_review">待审</option><option value="approved">通过</option><option value="rejected">拒绝</option></select></div></header><div class="bulk"><label class="select-all"><input type="checkbox" :checked="allSelected" :indeterminate.prop="someSelected" @change="toggleSelectAll($event.target.checked)"> 全选</label><span>已选 {{ selectedIds.length }}</span><button :disabled="selectedIds.length < 1 || busy" @click="batch('approve')">批量通过</button><button :disabled="selectedIds.length < 1 || busy" @click="batch('reject')">批量拒绝</button><button :disabled="selectedIds.length < 2 || busy" @click="mergeSelected">合并连续 Chunk</button><button class="primary" :disabled="busy || reviewableChunks.length < 1" @click="approveAll">一键通过全部</button></div><ChunkCard v-for="chunk in chunks" :key="chunk.id" :chunk="chunk" :selected="selectedIds.includes(chunk.id) || focusedChunkId === chunk.id" :editing="editingId === chunk.id" :edit-content="editContent" :busy="busy" @select="selectChunk" @focus="focusChunk" @edit="startEdit" @update:edit-content="editContent=$event" @save="saveChunk" @cancel="editingId=''" @split="openSplit" @remove="removeChunk" @review="reviewChunk" @reopen="reopenChunk" /><p v-if="!chunks.length" class="empty">{{ prep.status === 'failed' ? '解析失败，尚无候选 Chunk。' : '当前筛选没有 Chunk。' }}</p><footer class="complete"><button class="primary" :disabled="busy || !canComplete" @click="completeReview">完成审核</button></footer></section>
    </div>
    <RechunkDialog v-if="showRechunk" :current="detail?.chunker" :latest="latestChunker" :busy="busy" @close="showRechunk=false" @submit="rechunk" />
    <SplitChunkDialog v-if="showSplit" :chunk="splitTarget" :busy="busy" @close="showSplit=false; splitTarget=null" @submit="confirmSplit" />
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.summary{display:flex;align-items:center;gap:24px;flex-wrap:wrap}.summary div{display:grid;gap:3px}.summary span,.chunk-pane header span{color:var(--muted);font-size:var(--font-assist)}.notice{padding:10px 12px;border:1px solid #bad0f5;border-radius:8px;background:var(--blue-soft);color:#405069}.workbench{display:grid;grid-template-columns:minmax(0,1fr) minmax(440px,1fr);gap:16px;min-height:720px;margin-top:16px}.chunk-pane{min-width:0;overflow:auto;border:1px solid var(--border);border-radius:12px;background:#f7f9fc}.chunk-pane>header{position:sticky;top:0;z-index:3;display:flex;justify-content:space-between;gap:12px;padding:12px 14px;border-bottom:1px solid var(--border);background:#fff}.chunk-pane h3{margin:0}.filters,.bulk{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.bulk{padding:10px 12px;border-bottom:1px solid var(--border);background:#fff}.select-all{display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none}.empty{padding:40px;text-align:center;color:var(--muted)}.complete{position:sticky;bottom:0;display:flex;justify-content:flex-end;padding:12px;border-top:1px solid var(--border);background:#fff}
</style>
