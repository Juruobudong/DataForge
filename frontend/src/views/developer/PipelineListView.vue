<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const chunker = ref(null), samples = ref([]), sources = ref([])
const inputSource = ref('builtin_sample'), sampleCode = ref('preprocessing-document-v1'), sourceVersionId = ref('')
const form = ref({}), preview = ref(null), previousChunkCount = ref(null)
const loading = ref(false), saving = ref(false), error = ref('')
const availableSources = computed(() => sources.value.filter(item => item.version?.extraction_status === 'completed'))

function defaults() { return { chunk_size: 800, overlap_percent: 10, delimiters: ['\n\n', '\n', '。', '！', '？', '；'], min_chunk_size: 100, preserve_page_boundary: true, include_heading: true } }
function restoreDefaults() { form.value = defaults() }
async function load() {
  loading.value = true; error.value = ''
  try {
    const [chunkerData, sampleData, sourceData] = await Promise.all([
      api.sourcePreparationChunker(), api.developerSamples('preprocessing'), api.sources(),
    ])
    chunker.value = chunkerData; samples.value = sampleData; sources.value = sourceData
    form.value = { ...defaults(), ...(chunkerData.params || {}) }
    sampleCode.value = sampleData[0]?.code || sampleCode.value
    await runPreview()
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
async function runPreview() {
  if (inputSource.value === 'source_version' && !sourceVersionId.value) return
  loading.value = true; error.value = ''
  try {
    const result = await api.previewSourcePreparation({ input_source: inputSource.value, sample_code: sampleCode.value,
      source_version_id: inputSource.value === 'source_version' ? sourceVersionId.value : null, configuration: form.value })
    previousChunkCount.value = preview.value?.statistics?.chunk_count ?? null
    preview.value = result
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
async function save() {
  saving.value = true; error.value = ''
  try { chunker.value = await api.createSourcePreparationChunkerRevision({ base_revision: chunker.value.revision, params: form.value }) }
  catch (e) { error.value = e.message } finally { saving.value = false }
}
onMounted(load)
</script>

<template>
  <section class="preprocessing-page">
    <div class="page-head"><div><h2>文档预处理</h2><p>定义文档如何解析、清洗和分块，产出待人工审核的 SourceChunk；不直接生成正式知识。</p></div><span class="badge green">Source Preparation r{{ chunker?.revision || '—' }}</span></div>
    <section class="pipeline-grid">
      <article class="stage readonly"><span>1</span><div><b>文档解析</b><small>Parser · 平台按文件类型自动选择</small></div><em>只读</em></article>
      <article class="stage readonly"><span>2</span><div><b>内容清洗</b><small>Cleaner · 平台默认规则</small></div><em>只读</em></article>
      <article class="stage configurable"><span>3</span><div><b>文档分块</b><small>Chunker · 当前配置可预览后发布</small></div><em>可配置</em></article>
    </section>
    <section class="panel chunker-panel">
      <div class="panel-head"><div><h3>文档分块配置</h3><p>保存时创建新的 Source Preparation Revision；预览不会写入业务数据。</p></div><div><button @click="restoreDefaults">恢复默认</button><button class="primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存为新 Revision' }}</button></div></div>
      <div class="form-grid"><label>每块最大字符数<input v-model.number="form.chunk_size" type="number" min="100" max="4000"></label><label>重叠比例（%）<input v-model.number="form.overlap_percent" type="number" min="0" max="50"></label><label>最小块字符数<input v-model.number="form.min_chunk_size" type="number" min="1" :max="form.chunk_size"></label><label class="check"><input v-model="form.include_heading" type="checkbox"> 保留标题上下文</label><label class="check"><input v-model="form.preserve_page_boundary" type="checkbox"> 不跨页面</label></div>
    </section>
    <section class="panel preview-panel">
      <div class="panel-head"><div><h3>预览数据</h3><p>内置示例与业务文档都只执行无副作用 Preview。</p></div><button class="primary" :disabled="loading || (inputSource==='source_version' && !sourceVersionId)" @click="runPreview">{{ loading ? '预览中…' : '重新预览' }}</button></div>
      <div class="source-choice"><label><input v-model="inputSource" type="radio" value="builtin_sample"> 内置示例文档</label><select v-if="inputSource==='builtin_sample'" v-model="sampleCode"><option v-for="item in samples" :key="item.code" :value="item.code">{{ item.name }} · v{{ item.version }}</option></select><label><input v-model="inputSource" type="radio" value="source_version"> 选择业务文档</label><select v-if="inputSource==='source_version'" v-model="sourceVersionId"><option value="">请选择已完成解析的文档</option><option v-for="item in availableSources" :key="item.current_version_id || item.id" :value="item.current_version_id">{{ item.original_filename || item.name }}</option></select></div>
      <div v-if="preview" class="stats"><span v-if="previousChunkCount != null">分块前 <b>{{ previousChunkCount }}</b></span><span>分块后 <b>{{ preview.statistics.chunk_count }}</b></span><span>平均 <b>{{ preview.statistics.avg_chars }}</b> 字</span><span>范围 <b>{{ preview.statistics.min_chars }}–{{ preview.statistics.max_chars }}</b> 字</span></div>
      <div v-if="preview" class="preview-columns"><article><h4>原始内容</h4><pre>{{ preview.original_content }}</pre></article><article><h4>分块结果</h4><div class="chunks"><section v-for="(item,index) in preview.chunks" :key="item.chunk_key"><b>Chunk {{ index + 1 }}</b><small>{{ item.char_count }} 字</small><p>{{ item.content }}</p></section></div></article></div>
    </section>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.preprocessing-page{display:grid;gap:14px}.pipeline-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.stage{display:grid;grid-template-columns:34px 1fr auto;gap:10px;align-items:center;padding:15px;border:1px solid #dfe5ed;border-radius:12px;background:#fff}.stage>span{display:grid;width:32px;height:32px;place-items:center;border-radius:50%;background:#eaf1ff;color:#2f6fed;font-weight:900}.stage b,.stage small{display:block}.stage small{margin-top:4px;color:#7b8798}.stage em{font-size:10px;font-style:normal;color:#738197}.stage.configurable{border-color:#bcd0f4;background:#f7faff}.chunker-panel,.preview-panel{display:grid;gap:14px}.form-grid{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:12px}.form-grid label{display:grid;gap:5px;color:#59677a;font-weight:700}.form-grid .check{display:flex;align-items:center}.source-choice{display:flex;flex-wrap:wrap;gap:12px;align-items:center}.source-choice label{display:flex;gap:6px;align-items:center;font-weight:700}.source-choice select{min-width:280px}.stats{display:flex;gap:8px;flex-wrap:wrap}.stats span{padding:9px 12px;border-radius:9px;background:#eef4ff;color:#536985}.preview-columns{display:grid;grid-template-columns:1fr 1fr;gap:14px}.preview-columns article{min-width:0;border:1px solid #e0e6ef;border-radius:10px;overflow:hidden}.preview-columns h4{margin:0;padding:11px 13px;border-bottom:1px solid #e7ebf1;background:#f7f9fc}.preview-columns pre{max-height:520px;margin:0;padding:14px;overflow:auto;white-space:pre-wrap;line-height:1.7}.chunks{display:grid;gap:8px;max-height:520px;padding:10px;overflow:auto}.chunks section{padding:11px;border:1px solid #e3e8ef;border-radius:9px}.chunks small{margin-left:8px;color:#7c899a}.chunks p{margin:7px 0 0;line-height:1.6}@media(max-width:1100px){.pipeline-grid,.preview-columns{grid-template-columns:1fr}.form-grid{grid-template-columns:1fr 1fr}}
</style>
