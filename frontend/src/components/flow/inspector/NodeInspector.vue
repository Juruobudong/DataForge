<script setup>
import { computed, ref, watch } from 'vue'
import { hasEditableParameters, operatorNodeSubtitle, subflowRevisions, operatorAvailable, operatorPrimaryName } from '../flowModel.js'
import OperatorParameterForm from './OperatorParameterForm.vue'
const props = defineProps({ node: Object, issue: Object, sampleResult: Object, purpose: { type: String, default: 'knowledge' }, outputTypes: { type: Array, default: () => [] }, entityTypes: { type: Array, default: () => [] }, evaluationNodes: { type: Array, default: () => [] }, subflows: { type: Array, default: () => [] }, catalog: { type: Array, default: () => [] } })
const emit = defineEmits(['apply-parameters', 'open-subflow', 'change-subflow-revision', 'change-operator-version', 'replace-operator', 'open-graph-config'])
const replacing = ref(false), replacementCode = ref('')
const replacements = computed(() => props.catalog.filter(item => item.node_role !== 'flow_input' && item.code !== 'reviewed-source-chunk-input' && operatorAvailable(item, props.purpose, props.outputTypes)))
function replace() { const item = replacements.value.find(item => item.code === replacementCode.value); if (item) emit('replace-operator', item); replacing.value = false; replacementCode.value = '' }
const operatorVersions = computed(() => props.catalog.find(item => item.code === props.node?.data.definition.ref)?.versions || [])
const availableRevisions = computed(() => subflowRevisions(props.subflows).filter(item => item.code === props.node?.data.definition.ref && item.revision_status === 'published'))
function changeRevision(event) { emit('change-subflow-revision', availableRevisions.value.find(item => (item.revision_id || item.latest_revision_id) === event.target.value)) }
const tab = ref('parameters'), params = ref({})
const advancedOpen = ref(false)
watch(() => props.node, node => { params.value = { ...(node?.data.definition.params || {}) }; advancedOpen.value = false }, { immediate: true })
const documentParser = computed(() => props.node?.data.meta.code === 'document-parser')
const graphExtractor = computed(() => ['entity-extractor', 'relation-extractor'].includes(props.node?.data.definition.ref))
const editable = computed(() => hasEditableParameters(props.node))
const nodeRun = computed(() => props.node ? props.sampleResult?.node_runs?.[props.node.id] || null : null)
const nodeSubtitle = computed(() => {
  const meta = props.node?.data.meta
  if (!meta) return ''
  if (meta.kind !== 'operator') return meta.code
  return operatorNodeSubtitle(meta, true)
})
const parameterSchema = computed(() => props.node?.data.meta.parameterSchema || {})
const editableKeys = computed(() => new Set(Object.keys(parameterSchema.value.properties || {})))
const systemParameters = computed(() => Object.fromEntries(Object.entries(params.value).filter(([key]) => !editableKeys.value.has(key))))
const format = value => JSON.stringify(value, null, 2)
function updateParameters(value) { params.value = value; emit('apply-parameters', { ...value }) }
</script>
<template>
  <aside class="node-inspector">
    <div class="inspector-title"><div><h3>节点设置</h3><small v-if="node">{{ node.data.meta.kind === 'subflow' ? 'SUBFLOW' : node.data.meta.kind === 'knowledge_sink' ? 'SINK' : 'OPERATOR' }}</small></div><span v-if="node" class="status">{{ node.data.meta.status || 'idle' }}</span></div>
    <template v-if="node">
      <div class="node-summary"><span class="summary-icon">{{ node.data.meta.kind === 'subflow' ? '◈' : node.data.meta.kind === 'knowledge_sink' ? '✓' : '◇' }}</span><div><b>{{ node.data.meta.name }}</b><small>{{ nodeSubtitle }}</small></div></div>
      <section v-if="node.data.meta.kind === 'subflow'" class="subflow-settings"><label>引用版本<select aria-label="子流程引用版本" :value="node.data.definition.subflow_revision_id || ''" @change="changeRevision"><option v-if="!node.data.definition.subflow_revision_id" value="">版本未锁定</option><option v-for="item in availableRevisions" :key="item.revision_id" :value="item.revision_id || item.latest_revision_id">r{{ item.revision }}</option></select></label><button :disabled="!node.data.definition.subflow_revision_id || !node.data.meta.known" @click="emit('open-subflow')">查看内部 DAG</button></section>
      <nav><button :class="{ active: tab==='parameters' }" @click="tab='parameters'">配置</button><button :class="{ active: tab==='ports' }" @click="tab='ports'">输入输出</button><button :class="{ active: tab==='schema' }" @click="tab='schema'">Schema</button><button :class="{ active: tab==='result' }" @click="tab='result'">运行结果</button></nav>
      <div class="inspector-scroll" tabindex="0" aria-label="节点设置内容">
      <section v-if="node.data.definition.kind === 'operator' && node.data.meta.nodeRole === 'operator'" class="inspector-body">
        <button @click="replacing=!replacing">替换算子</button>
        <template v-if="replacing"><select v-model="replacementCode" aria-label="替换目标算子"><option value="">选择算子</option><option v-for="item in replacements" :key="item.code" :value="item.code">{{ operatorPrimaryName(item) }} · {{ item.code }} · v{{ item.version }}</option></select><button :disabled="!replacementCode" @click="replace">确认替换</button><small>保留兼容参数与连线，不兼容连线将删除；可一次撤销。</small></template>
      </section>
      <section v-if="node.data.meta.kind === 'operator'" class="inspector-body"><p>来源 {{ node.data.meta.source || 'dataforge' }} · 分组 {{ node.data.meta.catalogGroup || 'dataforge' }} · v{{ node.data.definition.operator_version || node.data.meta.version }}</p><p>Executor {{ node.data.meta.executor || 'dataforge-native' }}</p><p v-if="node.data.meta.dependencyStatus && node.data.meta.dependencyStatus.status !== 'ready'" class="error">{{ node.data.meta.dependencyStatus.reason || '算子依赖不可用' }}</p><label v-if="operatorVersions.length > 1">执行版本<select aria-label="算子执行版本" :value="node.data.definition.operator_version || node.data.meta.version" @change="emit('change-operator-version', Number($event.target.value))"><option v-for="item in operatorVersions" :key="item.version" :value="item.version">v{{ item.version }} · {{ item.source }}</option></select></label></section>
      <section v-if="tab==='parameters'" class="inspector-body">
        <div v-if="graphExtractor" class="graph-links"><button type="button" @click="emit('open-graph-config', { part: node.data.definition.ref === 'entity-extractor' ? 'entities' : 'relations' })">查看全流程图谱规则</button><button type="button" @click="emit('open-graph-config', { part: 'prompt', nodeId: node.id })">完整提示词预览</button></div>
        <div v-if="documentParser" class="fixed-parser"><h4>PDF 自动解析</h4><dl><div><dt>Backend</dt><dd>pipeline</dd></div><div><dt>Parse method</dt><dd>auto</dd></div></dl><p class="muted">扫描件判断与 OCR 由 MinerU 内部处理，当前不开放节点参数。</p></div>
        <template v-else>
          <OperatorParameterForm v-if="editable" :schema="parameterSchema" :model-value="params" :entity-types="entityTypes" :evaluation-nodes="evaluationNodes" @update:model-value="updateParameters" />
          <div v-else class="system-config"><h4>系统配置</h4><dl><div v-for="(value,key) in systemParameters" :key="key"><dt>{{ key }}</dt><dd>{{ value === null ? '—' : Array.isArray(value) || typeof value === 'object' ? JSON.stringify(value) : value }}</dd></div></dl><p class="muted">该节点的运行契约由 DataForge 维护，只读。</p></div>
          <details class="advanced" :open="advancedOpen" @toggle="advancedOpen = $event.target.open"><summary>高级信息 · Effective Params（只读）</summary><pre>{{ format(params) }}</pre></details>
        </template>
      </section>
      <section v-else-if="tab==='ports'" class="inspector-body io-body">
        <h4>INPUT</h4>
        <div v-for="(spec, name) in node.data.meta.inputs" :key="name" class="port-block"><article><b>{{ name }}</b><small>{{ spec.artifact_type }} · {{ spec.cardinality }}</small></article><h5>典型示例</h5><pre>{{ format(node.data.meta.inputExample?.[name] || []) }}</pre><h5>本次样例预览</h5><template v-if="nodeRun?.inputs?.[name]"><pre>{{ format(nodeRun.inputs[name].items) }}</pre><small class="preview-meta">共 {{ nodeRun.inputs[name].total }} 条<span v-if="nodeRun.inputs[name].truncated"> · 已截断</span></small></template><p v-else class="muted">尚未运行样例，暂无节点输入预览。</p></div>
        <h4>OUTPUT</h4>
        <div v-for="(spec, name) in node.data.meta.outputs" :key="name" class="port-block"><article><b>{{ name }}</b><small>{{ spec.artifact_type }} · {{ spec.cardinality }}</small></article><h5>典型示例</h5><pre>{{ format(node.data.meta.outputExample?.[name] || []) }}</pre><h5>本次样例预览</h5><template v-if="nodeRun?.outputs?.[name]"><pre>{{ format(nodeRun.outputs[name].items) }}</pre><small class="preview-meta">共 {{ nodeRun.outputs[name].total }} 条<span v-if="nodeRun.outputs[name].truncated"> · 已截断</span></small></template><p v-else class="muted">{{ nodeRun ? '本次预览没有输出数据。' : '尚未运行样例，暂无节点输出预览。' }}</p></div>
        <p v-if="!Object.keys(node.data.meta.outputs).length" class="muted">终点节点没有输出端口。</p>
        <details v-if="nodeRun?.internal_trace"><summary>查看子图内部轨迹（{{ Object.keys(nodeRun.internal_trace).length }} 个节点）</summary><pre>{{ format(nodeRun.internal_trace) }}</pre></details>
      </section>
      <section v-else-if="tab==='schema'" class="inspector-body"><pre>{{ JSON.stringify(node.data.meta.parameterSchema || {}, null, 2) }}</pre></section>
      <section v-else class="inspector-body result-body"><template v-if="nodeRun"><p class="run-status" :class="nodeRun.status">{{ nodeRun.status }}</p><p v-if="nodeRun.error" class="inline-error">{{ nodeRun.error }}</p><h4>OUTPUT</h4><pre>{{ format(nodeRun.outputs || {}) }}</pre><details v-if="nodeRun.internal_trace"><summary>子图内部轨迹</summary><pre>{{ format(nodeRun.internal_trace) }}</pre></details><p class="muted">这是受控内存预览，不代表生产 Adapter 或外部模型的执行结果。</p></template><p v-else class="muted">运行模板样例后，这里只显示当前所选节点的预览状态、输出和错误。</p></section>
      <p v-if="issue" class="issue">{{ issue.message }}</p>
      </div>
    </template>
    <div v-else class="empty"><span>◇</span><b>选择一个节点</b><p>查看端口契约、编辑参数或检查运行结果。</p></div>
  </aside>
</template>
<style scoped>
.node-inspector{display:flex;width:300px;height:auto;max-height:var(--flow-canvas-height,720px);min-height:0;align-self:start;flex:0 0 300px;flex-direction:column;overflow:hidden;border:1px solid var(--border);border-radius:12px;background:#fff;box-shadow:var(--shadow)}.inspector-title{display:flex;align-items:center;justify-content:space-between;padding:14px;border-bottom:1px solid #edf0f4}.inspector-title h3{margin:0;font-size:12px}.inspector-title small{color:#8090a5;font-size:7px}.status{padding:4px 7px;border-radius:999px;color:#2f6fed;background:#eaf1ff;font-size:7px;font-weight:850}.node-summary{display:grid;grid-template-columns:36px 1fr;gap:9px;align-items:center;padding:13px}.summary-icon{display:grid;width:35px;height:35px;place-items:center;border-radius:9px;color:#2f6fed;background:#eaf1ff;font-size:16px}.node-summary b,.node-summary small{display:block}.node-summary b{font-size:11px}.node-summary small{margin-top:3px;color:#7d899a;font-size:8px}.node-inspector nav{display:flex;overflow-x:auto;padding:0 10px;border-bottom:1px solid #edf0f4}.node-inspector nav button{min-height:32px;padding:0 7px;border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;font-size:8px}.node-inspector nav button.active{border-bottom-color:#2f6fed;color:#2f6fed}.inspector-body{flex:none;padding:13px}.inspector-body label{display:grid;gap:7px;color:#617087;font-size:9px;font-weight:800}.inspector-body textarea{width:100%;resize:vertical;font:9px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}.apply{width:100%;margin-top:10px}.muted{color:#8190a5;font-size:8px;line-height:1.6}.inline-error{color:#c94a4a;font-size:8px}.inspector-body h4{margin:12px 0 6px;color:#8390a3;font-size:8px;letter-spacing:.08em}.inspector-body article{padding:9px;border:1px solid #edf0f4;border-radius:8px;background:#fbfcfe}.inspector-body article+article{margin-top:6px}.inspector-body article b,.inspector-body article small{display:block}.inspector-body article b{font-size:9px}.inspector-body article small{margin-top:3px;color:#7d8ba0;font-size:8px}.inspector-body pre{margin:0;font-size:8px}.issue{margin:10px;padding:9px;border:1px solid #efcccc;border-radius:8px;color:#c94a4a;background:#fff0f0;font-size:8px}.empty{display:grid;flex:1;place-content:center;padding:24px;color:#8793a4;text-align:center}.empty span{font-size:28px}.empty b{margin-top:8px;color:#506078;font-size:10px}.empty p{font-size:8px;line-height:1.6}
</style>
<style scoped>.graph-links{display:grid;gap:8px;margin-bottom:12px}.graph-links button{font-size:13px}</style>
<style scoped>.subflow-settings{padding:12px;display:grid;gap:10px}.subflow-settings label{display:grid;gap:6px;font-size:14px}</style>
<style scoped>
.node-inspector>.inspector-title,.node-inspector>.node-summary,.node-inspector>.subflow-settings,.node-inspector>nav{flex:none}
.inspector-scroll{flex:0 1 auto;min-height:0;overflow:auto;overscroll-behavior:contain}
.inspector-body+.inspector-body{padding-top:0}
.inspector-body>p{margin:0 0 10px}
</style>
<style scoped>
.inspector-body h5{margin:9px 0 5px;color:#69778b;font-size:8px}.inspector-body pre{max-height:180px;overflow:auto;padding:8px;border:1px solid #edf0f4;border-radius:7px;background:#f8fafc;white-space:pre-wrap;word-break:break-word}.port-block+.port-block{margin-top:12px}.preview-meta{display:block;margin-top:4px;color:#7d8ba0;font-size:7px}.io-body details,.result-body details{margin-top:12px}.io-body summary,.result-body summary{cursor:pointer;color:#2f6fed;font-size:8px;font-weight:800}.io-body details pre,.result-body details pre{margin-top:6px}.run-status{display:inline-block;margin:0;padding:4px 7px;border-radius:999px;background:#eaf7f1;color:#1d8c65;font-size:8px;font-weight:850}.run-status.failed,.run-status.skipped{background:#fff0f0;color:#c94a4a}
.fixed-parser{padding:12px;border:1px solid #d9e5ff;border-radius:9px;background:#f6f9ff}.fixed-parser h4{margin:0 0 10px;color:#2f6fed}.fixed-parser dl{margin:0}.fixed-parser dl div{display:flex;align-items:center;justify-content:space-between;padding:7px 0;border-top:1px solid #e5edfb}.fixed-parser dt{color:#69778b;font-size:8px}.fixed-parser dd{margin:0;color:#24364f;font:9px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:800}
.advanced{margin-top:12px}.advanced summary{cursor:pointer;color:#2f6fed;font-size:8px;font-weight:800}.advanced label{margin-top:8px}
.system-config{padding:12px;border:1px solid #dfe6ef;border-radius:9px;background:#f8fafc}.system-config h4{margin:0 0 8px}.system-config dl{margin:0}.system-config dl div{display:grid;grid-template-columns:1fr 1.2fr;gap:8px;padding:7px 0;border-top:1px solid #e7ebf1}.system-config dt{color:#748198;font-size:8px}.system-config dd{margin:0;overflow-wrap:anywhere;color:#2d3d54;font-size:8px;font-weight:700}
</style>

<style scoped>.node-summary b,.node-summary small{white-space:normal;overflow-wrap:anywhere}.node-summary>div{min-width:0}</style>
