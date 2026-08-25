<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const pipelines = ref([]), chunker = ref(null), form = ref(null), error = ref(''), saving = ref(false), drawer = ref(false)
const labels = { validation: ['文件检查', '格式 / 完整性'], parse: ['文件解析', 'PDF / DOCX / MD / TXT / CSV'], normalization: ['内容整理', '清洗 / 标准化'], structure_recovery: ['结构恢复', '保留结构与语义'], semantic_chunks: ['结构化分块器', '标准化 Chunk'] }
async function load() { try { [pipelines.value, chunker.value] = await Promise.all([api.standardPipelines(), api.sourcePreparationChunker()]) } catch (e) { error.value = e.message } }
function editChunker() { form.value = { ...chunker.value.params, delimiters: [...(chunker.value.params.delimiters || [])] }; drawer.value = true }
function restoreDefaults() { form.value = { chunk_size: 800, overlap_percent: 10, delimiters: ['\n\n', '\n', '。', '！', '？', '；'], min_chunk_size: 100, preserve_page_boundary: true, include_heading: true } }
async function save() { saving.value = true; error.value = ''; try { chunker.value = await api.createSourcePreparationChunkerRevision({ base_revision: chunker.value.revision, params: form.value }); drawer.value = false } catch (e) { error.value = e.message } finally { saving.value = false } }
onMounted(load)
</script>

<template>
  <section><div class="page-head"><div><h2>标准流程</h2><p>统一维护所有知识生产都会经过的文档前置处理，不提供任意 DAG 编辑器。</p></div><div class="page-actions"><span class="badge green">当前默认</span></div></div><section v-for="pipeline in pipelines" :key="pipeline.code" class="panel"><div class="panel-head"><div><h3>Source Preparation · r{{ chunker?.revision || '—' }}</h3><p>{{ pipeline.code }}</p></div><span class="badge blue">受控线性步骤</span></div><div class="flow"><div class="node"><b>文档解析器</b><small>Document Parser</small></div><span class="arrow">→</span><div class="node"><b>内容清洗</b><small>保留段落与页结构</small></div><span class="arrow">→</span><button class="node configurable" @click="editChunker"><b>结构化分块器</b><small>{{ chunker?.params?.chunk_size || '—' }} 字符 · Overlap {{ chunker?.params?.overlap_percent ?? '—' }}%</small></button><span class="arrow">→</span><div class="node"><b>SourceChunk Builder</b><small>Candidate ChunkSet</small></div></div></section><p v-if="error" class="error">{{ error }}</p>
    <aside v-if="drawer" class="drawer" aria-label="结构化分块器配置"><div class="panel-head"><div><h3>结构化分块器</h3><p>当前 r{{ chunker.revision }}；保存将创建新 Revision。</p></div><button @click="drawer=false">关闭</button></div><label>目标块大小（字符）<input v-model.number="form.chunk_size" type="number" min="100" max="4000"></label><label>Overlap（%）<input v-model.number="form.overlap_percent" type="number" min="0" max="50"></label><label>最小块大小<input v-model.number="form.min_chunk_size" type="number" min="1" :max="form.chunk_size"></label><fieldset><legend>自然边界</legend><label v-for="(_, index) in form.delimiters" :key="index"><input v-model="form.delimiters[index]"></label><button @click="form.delimiters.push('')">增加边界</button></fieldset><label class="check"><input v-model="form.preserve_page_boundary" type="checkbox"> 不跨页面</label><label class="check"><input v-model="form.include_heading" type="checkbox"> 保留明确标题上下文</label><footer><button @click="restoreDefaults">恢复默认</button><button class="primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存为新 Revision' }}</button></footer></aside>
  </section>
</template>

<style scoped>
.configurable{color:inherit;text-align:left}.configurable:hover{border-color:var(--blue);background:var(--blue-soft)}.drawer{position:fixed;z-index:30;top:0;right:0;width:min(440px,100vw);height:100vh;overflow:auto;padding:22px;border-left:1px solid var(--border);background:#fff;box-shadow:var(--shadow)}.drawer>label,.drawer fieldset{display:grid;gap:6px;margin:14px 0}.drawer input[type=number],.drawer fieldset input{width:100%}.drawer .check{display:flex;grid-template-columns:auto 1fr;align-items:center}.drawer footer{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}
</style>
