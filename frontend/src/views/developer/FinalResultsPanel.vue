<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../../api/platform'
import { finalResultCells, finalResultColumns, finalResultOutputs, RESULT_PAGE_SIZE } from './finalResults'
import { literalDatatypeLabel, objectKindLabel } from '../../constants/knowledgeLabels'

const props = defineProps({ run: Object })
const emit = defineEmits(['inspect-node'])
const outputs = computed(() => finalResultOutputs(props.run))
const selectedId = ref(''), pages = ref({}), page = ref(null), loading = ref(false), error = ref('')
const expanded = ref(null)
const selected = computed(() => outputs.value.find(item => item.id === selectedId.value))
const columns = computed(() => finalResultColumns(selected.value?.key))
let requestVersion = 0
let outputChosen = false, firstPreviewSelected = false

watch(() => props.run?.id, () => {
  requestVersion++; pages.value = {}; selectedId.value = ''; page.value = null; expanded.value = null
  outputChosen = false; firstPreviewSelected = false
}, { immediate: true, flush: 'sync' })
watch(outputs, values => {
  if (!values.some(item => item.id === selectedId.value)) selectedId.value = (values.find(item => item.preview) || values[0])?.id || ''
  const ready = values.find(item => item.preview)
  if (ready && !firstPreviewSelected) {
    firstPreviewSelected = true
    if (!outputChosen) selectedId.value = ready.id
  }
}, { immediate: true })
watch([() => props.run?.id, () => selectedId.value, () => selected.value?.preview?.id,
  () => selected.value?.preview?.preview_checksum, () => pages.value[selectedId.value] || 0],
  () => { loadPage() }, { immediate: true })
onBeforeUnmount(() => { requestVersion++ })

async function loadPage() {
  const version = ++requestVersion, output = selected.value, runId = props.run?.id
  page.value = null; expanded.value = null; error.value = ''; loading.value = false
  if (!runId || !output?.preview) return
  loading.value = true
  try {
    const result = await api.sinkPreviewCandidates(runId, output.preview.id, pages.value[output.id] || 0, RESULT_PAGE_SIZE)
    if (version === requestVersion && runId === props.run?.id && output.id === selectedId.value) page.value = result
  } catch (e) {
    if (version === requestVersion) error.value = e.message || String(e)
  } finally {
    if (version === requestVersion) loading.value = false
  }
}
function changePage(delta) { pages.value = { ...pages.value, [selectedId.value]: Math.max(0, (page.value?.offset || 0) + delta * RESULT_PAGE_SIZE) } }
function selectOutput(id) { outputChosen = true; selectedId.value = id }
function sourceLabel(item) { return item.source_anchor || item.anchor_json?.label || item.source_chunk_id || '来源未记录' }
function pretty(value) { return JSON.stringify(value ?? {}, null, 2) }
function semanticDetails(data) {
  return [
    ['来源实体类型', data.source_entity?.type_label || data.source_entity?.type], ['来源实体描述', data.source_entity?.description],
    ['来源实体别名', data.source_entity?.aliases?.join('、')],
    ['目标实体类型', data.target_entity?.type_label || data.target_entity?.type], ['目标实体描述', data.target_entity?.description],
    ['目标实体别名', data.target_entity?.aliases?.join('、')], ['关系描述', data.relation?.description],
    ['关系关键词', data.relation?.keywords?.join('、')], ['关系权重', data.relation?.weight],
  ]
}
</script>

<template>
  <section class="final-results" aria-label="最终结果">
    <header><h3>最终结果</h3><p>本次运行的知识输出候选，已经过上游处理；调试不写入正式知识库。</p></header>
    <nav class="output-tabs" aria-label="结果输出类型">
      <button v-for="output in outputs" :key="output.id" :class="{ active: selectedId === output.id }"
        :aria-pressed="selectedId === output.id" @click="selectOutput(output.id)">
        {{ output.label }}<span v-if="output.count != null"> · {{ output.count }} 条</span><small>{{ output.status }}</small>
      </button>
    </nav>
    <p v-if="!outputs.length" class="empty-result">本次冻结 DAG 没有知识输出节点。</p>
    <template v-if="selected">
      <div class="result-summary" :class="selected.state" role="status"><b>{{ selected.status }}</b>
        <span v-if="selected.preview">最终候选 {{ page?.total ?? selected.count ?? '—' }} 条</span>
        <small v-if="selected.key?.startsWith('graph:')">候选条数不等于实体节点数。</small>
      </div>
      <div v-if="selected.preview" class="result-diff" aria-label="结果变更预览">
        <span v-for="(label, code) in { ADD: '新增', UPDATE: '更新', INACTIVE: '删除', UNCHANGED: '不变' }" :key="code">{{ label }} {{ selected.preview.diff?.[code] || 0 }}</span>
      </div>
      <details v-if="selected.diagnostics.length" :key="selected.id" :open="selected.hasWarning" class="processing" aria-label="处理统计与异常">
        <summary>处理统计与异常 · {{ selected.diagnostics.length }} 个节点</summary>
        <p>以下为各节点本次处理统计，不跨节点累加。</p>
        <article v-for="node in selected.diagnostics" :key="node.nodeId">
          <b>{{ node.name }} · {{ node.nodeId }}</b>
          <p v-for="stat in node.processing" :key="stat.output_key">处理 {{ stat.attempted_chunks }} 块 · 成功 {{ stat.successful_chunks }} 块 · 失败 {{ stat.failed_chunks }} 块</p>
          <p v-if="!node.processing.length">存在处理异常，失败数量未记录。</p>
          <p v-for="reason in node.reasons" :key="reason" class="error-text">{{ reason }}</p>
          <p v-if="node.explanation" class="muted">{{ node.explanation }}</p>
          <p v-if="node.processing.some(stat => stat.failed_chunks > 0) && !node.reasons.length" class="error-text">已记录分块失败，但没有可提取的明确原因；请查看节点日志。</p>
          <button @click="emit('inspect-node', node.nodeId)">查看节点诊断</button>
        </article>
      </details>
      <p v-if="selected.preview && !selected.diagnostics.some(node => node.processing.length)" class="muted">本次输出未记录可靠的块级统计；不能据此确认全部分块成功。</p>
      <p v-if="loading" class="empty-result" role="status">正在加载最终结果…</p>
      <div v-else-if="error" class="result-error" role="alert"><p>{{ error }}</p><button @click="loadPage">重试加载</button></div>
      <template v-else-if="page">
        <p v-if="!page.items.length" class="empty-result">{{ selected.hasWarning ? '本页没有最终候选，请查看处理异常。' : page.total === 0 ? '本次最终输出为 0 条；这不等同于模型无匹配，也可能经过下游过滤。' : '本页没有结果。' }}</p>
        <div v-else class="result-table-wrap">
          <table><thead><tr><th>#</th><th v-for="column in columns" :key="column">{{ column }}</th><th>来源 / 详情</th></tr></thead>
            <tbody><template v-for="(item, index) in page.items" :key="`${page.offset}-${index}`">
              <tr><td>{{ page.offset + index + 1 }}</td>
                <td v-for="(value, cell) in finalResultCells(selected.key, item)" :key="cell" class="result-content">{{ value ?? '—' }}</td>
                <td class="source-cell">{{ sourceLabel(item) }}<button :aria-expanded="expanded === index" @click="expanded = expanded === index ? null : index">{{ expanded === index ? '收起详情' : '查看详情' }}</button></td>
              </tr>
              <tr v-if="expanded === index"><td :colspan="columns.length + 2" class="result-detail">
                <h4>完整内容</h4><p>{{ item.canonical_content || '—' }}</p>
                <dl v-if="selected.key === 'graph:triple'">
                  <dt>主体类型</dt><dd>{{ item.data_json?.subject_type_label || item.data_json?.subject_type || '—' }}</dd>
                  <dt>客体类型</dt><dd>{{ item.data_json?.object_type || objectKindLabel(item.data_json?.data?.object_kind) || '—' }}</dd>
                  <dt>字面值类型</dt><dd>{{ literalDatatypeLabel(item.data_json?.data?.literal_datatype) || '—' }}</dd>
                  <dt>原始值 / 规范值 / 单位</dt><dd>{{ item.data_json?.data?.literal_raw_value ?? '—' }} / {{ item.data_json?.data?.literal_normalized_value ?? '—' }} / {{ item.data_json?.data?.literal_unit || '—' }}</dd>
                </dl>
                <dl v-else-if="selected.key === 'graph:semantic'"><template v-for="[label, value] in semanticDetails(item.data_json || {})" :key="label"><dt>{{ label }}</dt><dd>{{ value ?? '—' }}</dd></template></dl>
                <div v-else-if="!['text', 'qa'].includes(selected.key)"><h4>结构化字段</h4><pre>{{ pretty(item.data_json) }}</pre></div>
                <h4>原文证据</h4><p>{{ item.evidence_text || '原文证据未记录' }}</p>
                <h4>来源锚点</h4><pre>{{ pretty(item.anchor_json) }}</pre>
                <details><summary>技术标识与原始 JSON</summary><pre>{{ pretty(item) }}</pre></details>
              </td></tr>
            </template></tbody>
          </table>
        </div>
        <footer class="pagination"><button :disabled="page.offset === 0" @click="changePage(-1)">上一页</button><span>第 {{ Math.floor(page.offset / RESULT_PAGE_SIZE) + 1 }} 页 · 共 {{ page.total }} 条</span><button :disabled="!page.has_more" @click="changePage(1)">下一页</button></footer>
      </template>
      <p v-else-if="!selected.preview" class="empty-result">该输出尚无已暂存的最终结果。节点中间输出仍可在执行 DAG 中查看。</p>
    </template>
  </section>
</template>

<style scoped>
.final-results{height:590px;overflow:auto;padding:18px;box-sizing:border-box;background:var(--bg,#f6f8fb);font-size:14px;overscroll-behavior:contain}
h3{margin:0;font-size:20px}header p,.muted{color:#65748a;line-height:1.6}.output-tabs{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}.output-tabs button{background:#fff;border:1px solid #dbe3ef;border-radius:8px;text-align:left;padding:10px 14px}.output-tabs button.active{border-color:#2f6fed;background:#edf4ff;color:#2458b8}.output-tabs small{display:block;margin-top:4px}
.result-summary,.result-diff{display:flex;flex-wrap:wrap;align-items:center;gap:14px;padding:12px;background:#fff;border:1px solid #dbe3ef;border-radius:8px}.result-summary.warning{background:#fff8e8;border-color:#e8c879;color:#855e17}.result-diff{margin:8px 0;color:#65748a}.result-summary small{color:#65748a}
.processing{margin:12px 0}.processing>summary{cursor:pointer;padding:10px 0;color:#536177}.processing>p{color:#65748a}.processing article{padding:12px;margin:8px 0;border:1px solid #dbe3ef;border-radius:8px;background:#fff;overflow-wrap:anywhere}.processing p{margin:7px 0}.processing button,.source-cell button{font-size:13px}.error-text,.result-error{color:#a63636;white-space:pre-wrap}.empty-result{padding:24px 12px;color:#65748a;line-height:1.7}
.result-table-wrap{overflow-x:auto;border:1px solid #dbe3ef;border-radius:8px;background:#fff}table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{text-align:left;padding:12px;border-bottom:1px solid #e5eaf1;vertical-align:top;overflow-wrap:anywhere}th{background:#eef2f7;font-size:13px}th:first-child{width:32px}th:last-child{width:125px}.result-content{white-space:pre-wrap;line-height:1.7}.source-cell{font-size:13px;color:#65748a}.source-cell button{display:block;margin-top:10px}.result-detail{background:#f8faff}.result-detail p{white-space:pre-wrap;line-height:1.7}.result-detail pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 monospace}.result-detail dl{display:grid;grid-template-columns:150px minmax(0,1fr);gap:8px}.result-detail dd{margin:0;white-space:pre-wrap}.result-detail dt{color:#65748a}.result-detail summary{cursor:pointer}h4{margin:12px 0 6px}.pagination{display:flex;align-items:center;justify-content:center;gap:16px;margin-top:16px}
</style>
