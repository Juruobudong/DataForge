<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { api } from '../../api/platform'

const tabs = [{ key: 'templates', name: '模板' }, { key: 'catalog', name: '算子库' }, { key: 'subflows', name: '可复用子图' }]
const activeTab = ref('templates'), templates = ref([]), catalog = ref([]), subflows = ref([]), types = ref([])
const selected = ref(null), selectedNode = ref(null), result = ref(null), error = ref('')
const code = ref(''), name = ref(''), outputTypes = ref(['text']), nodes = ref([]), edges = ref([])
const history = ref([]), future = ref([]), sampleId = ref('guideline-md')
const parameterText = ref('{}')
const { addEdges, fitView } = useVueFlow()

const typeOptions = computed(() => {
  const normal = types.value.filter(item => item.status === 'active' && item.current_revision && item.code !== 'graph').map(item => ({ code: item.code, name: item.name }))
  return [...normal, { code: 'graph:triple', name: '三元组图谱' }, { code: 'graph:semantic', name: '语义图谱' }]
})
const exposedCatalog = computed(() => catalog.value.filter(item => item.exposure === 'canvas' && item.enabled !== false))

function snapshot() { return JSON.stringify({ nodes: nodes.value, edges: edges.value }) }
function remember() { history.value.push(snapshot()); if (history.value.length > 30) history.value.shift(); future.value = [] }
function restore(raw) { const value = JSON.parse(raw); nodes.value = value.nodes; edges.value = value.edges }
function undo() { if (!history.value.length) return; future.value.push(snapshot()); restore(history.value.pop()) }
function redo() { if (!future.value.length) return; history.value.push(snapshot()); restore(future.value.pop()) }
function outputFamily(value) { return value.startsWith('graph:') ? 'graph' : value }
function definition() {
  return {
    schema_version: 3,
    nodes: nodes.value.map(node => ({ ...node.data.definition, id: node.id })),
    edges: edges.value.map(edge => ({ source: edge.source, source_port: edge.sourceHandle || 'output', target: edge.target, target_port: edge.targetHandle || 'input' })),
    ui: { positions: Object.fromEntries(nodes.value.map(node => [node.id, node.position])) },
  }
}
function canvasNode(definition, position = { x: 80, y: 80 }) {
  const label = definition.kind === 'knowledge_sink' ? `Writer · ${definition.output_key}` : definition.kind === 'subflow' ? `子图 · ${definition.ref}` : definition.ref
  return { id: definition.id, position, data: { label, definition } }
}
function loadDefinition(value) {
  const positions = value?.ui?.positions || {}
  nodes.value = (value?.nodes || []).map((node, index) => canvasNode(node, positions[node.id] || { x: 80 + (index % 4) * 220, y: 60 + Math.floor(index / 4) * 110 }))
  edges.value = (value?.edges || []).map((edge, index) => ({ id: `edge-${index}`, source: Array.isArray(edge) ? edge[0] : edge.source, target: Array.isArray(edge) ? edge[1] : edge.target, sourceHandle: edge.source_port || 'output', targetHandle: edge.target_port || 'input' }))
  history.value = []; future.value = []
  nextTick(() => fitView({ padding: 0.15 }))
}
function edit(item) { selected.value = item; code.value = item.code; name.value = item.name; outputTypes.value = [...item.output_types].map(value => value === 'graph' ? 'graph:triple' : value); result.value = null; loadDefinition(item.definition) }
function reset() { selected.value = null; selectedNode.value = null; code.value = ''; name.value = ''; outputTypes.value = ['text']; nodes.value = []; edges.value = []; history.value = []; future.value = [] }
function dragStart(event, item, kind = 'operator') { event.dataTransfer.setData('application/dataforge-operator', JSON.stringify({ kind, ref: item.code, params: {} })); event.dataTransfer.effectAllowed = 'move' }
function drop(event) { event.preventDefault(); const raw = event.dataTransfer.getData('application/dataforge-operator'); if (!raw) return; remember(); const rect = event.currentTarget.getBoundingClientRect(); const def = JSON.parse(raw); def.id = `${def.ref}-${Date.now().toString(36)}`; nodes.value.push(canvasNode(def, { x: event.clientX - rect.left - 70, y: event.clientY - rect.top - 30 })) }
function addSink(outputKey) { remember(); const family = outputFamily(outputKey); const mode = outputKey.includes(':') ? outputKey.split(':')[1] : null; const id = `sink-${outputKey.replace(':','-')}-${Date.now().toString(36)}`; nodes.value.push(canvasNode({ id, kind: 'knowledge_sink', knowledge_type: family, graph_mode: mode, output_key: outputKey }, { x: 650, y: 100 + nodes.value.length * 20 })) }
function artifactType(node, direction) {
  if (!node) return ''
  const definition = node.data.definition
  if (definition.kind === 'knowledge_sink') return direction === 'input' ? `candidate:${definition.output_key}` : ''
  const item = catalog.value.find(value => value.code === definition.ref)
  const value = direction === 'input' ? item?.input_ports?.input : item?.output_ports?.output
  let type = value?.artifact_type || value || ''
  if (type === 'candidate:*' && definition.params?.knowledge_type) type = `candidate:${definition.params.knowledge_type}${definition.params.graph_mode ? `:${definition.params.graph_mode}` : ''}`
  return type
}
function compatible(actual, expected) { return actual === expected || expected.endsWith(':*') && actual.startsWith(expected.slice(0, -1)) }
function connect(connection) {
  const source = nodes.value.find(node => node.id === connection.source), target = nodes.value.find(node => node.id === connection.target)
  const actual = artifactType(source, 'output'), expected = artifactType(target, 'input')
  if (actual && expected && !compatible(actual, expected) && !(expected.startsWith('candidate:graph:') && actual === 'candidate:graph')) {
    error.value = `类型不兼容：${actual} → ${expected}`; return
  }
  const targetItem = catalog.value.find(value => value.code === target?.data.definition.ref)
  if ((targetItem?.input_ports?.input?.cardinality || 'one') === 'one' && edges.value.some(edge => edge.target === connection.target)) {
    error.value = '目标端口基数为 one，不能重复连线'; return
  }
  error.value = ''; remember(); addEdges([{ ...connection, id: `edge-${Date.now().toString(36)}`, sourceHandle: connection.sourceHandle || 'output', targetHandle: connection.targetHandle || 'input' }])
}
function selectNode(node) { selectedNode.value = node; parameterText.value = JSON.stringify(node.data.definition.params || {}, null, 2) }
function applyParameters() { try { const value = JSON.parse(parameterText.value || '{}'); if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(); remember(); selectedNode.value.data.definition.params = value; error.value = '' } catch { error.value = '节点参数必须是 JSON 对象' } }
function removeSelected() { if (!selectedNode.value) return; remember(); const id = selectedNode.value.id; nodes.value = nodes.value.filter(node => node.id !== id); edges.value = edges.value.filter(edge => edge.source !== id && edge.target !== id); selectedNode.value = null }
async function load() { try { [templates.value, catalog.value, subflows.value, types.value] = await Promise.all([api.flowTemplates(), api.operatorCatalog(), api.flowSubgraphs(), api.knowledgeTypes()]) } catch (e) { error.value = e.message } }
async function save() { try { error.value = ''; const body = { name: name.value, output_types: outputTypes.value, definition: definition() }; result.value = selected.value ? await api.updateFlowTemplate(selected.value.id, body) : await api.createFlowTemplate({ ...body, code: code.value }); await load(); if (!selected.value) reset() } catch (e) { error.value = e.message } }
async function action(kind) { if (!selected.value) return; try { result.value = kind === 'validate' ? await api.validateFlowTemplate(selected.value.id) : kind === 'publish' ? await api.publishFlowTemplate(selected.value.id) : kind === 'default' ? await api.defaultFlowTemplate(selected.value.id) : kind === 'sample' ? await api.sampleFlowTemplate(selected.value.id, sampleId.value) : await api.archiveFlowTemplate(selected.value.id); await load() } catch (e) { error.value = e.message } }
onMounted(load)
</script>

<template>
  <section><div class="page-head"><div><h2>知识流程模板</h2><p>白名单算子、强类型端口和不可变快照组成受控可拖拽 DAG。</p></div><span class="badge blue">Flow DSL v3</span></div>
    <div class="tabs"><button v-for="tab in tabs" :key="tab.key" :class="{active:activeTab===tab.key}" @click="activeTab=tab.key">{{ tab.name }}</button></div>
    <template v-if="activeTab==='templates'"><div class="template-head"><aside><button class="primary" @click="reset">新建模板</button><button v-for="item in templates" :key="item.id" class="template-row" :class="{active:selected?.id===item.id}" @click="edit(item)"><b>{{ item.name }}</b><small>{{ item.code }} · r{{ item.revision || '-' }}</small></button></aside><form class="stack" @submit.prevent="save"><input v-model="code" :disabled="!!selected" required placeholder="模板编码"><input v-model="name" required placeholder="模板名称"><div class="outputs"><label v-for="item in typeOptions" :key="item.code"><input v-model="outputTypes" type="checkbox" :value="item.code">{{ item.name }}</label></div><div class="actions"><button type="button" :disabled="!history.length" @click="undo">撤销</button><button type="button" :disabled="!future.length" @click="redo">重做</button><button type="button" :disabled="!selectedNode" @click="removeSelected">删除节点</button><button class="primary">保存草稿</button></div></form><aside v-if="selected"><button @click="action('validate')">编译校验</button><button class="primary" @click="action('publish')">发布快照</button><button @click="action('default')">设为默认</button><select v-model="sampleId"><option value="guideline-md">指南 Markdown</option><option value="faq-csv">FAQ CSV</option></select><button @click="action('sample')">样例运行</button></aside></div>
      <div class="editor"><aside class="palette"><h3>算子库</h3><button v-for="item in exposedCatalog" :key="item.code" draggable="true" @dragstart="dragStart($event,item)"><b>{{ item.name }}</b><small>{{ item.input_ports?.input?.artifact_type || item.input_ports?.input }} → {{ item.output_ports?.output?.artifact_type || item.output_ports?.output }}</small></button><h3>可复用子图</h3><button v-for="item in subflows" :key="item.id" draggable="true" @dragstart="dragStart($event,item,'subflow')"><b>{{ item.name }}</b><small>{{ item.code }} · r{{ item.revision }}</small></button><h3>输出 Writer</h3><button v-for="item in outputTypes" :key="item" @click="addSink(item)">+ {{ item }}</button></aside><div class="canvas" @dragover.prevent @drop="drop"><VueFlow v-model:nodes="nodes" v-model:edges="edges" fit-view-on-init @connect="connect" @node-click="selectNode($event.node)" @node-drag-start="remember"><Background/><Controls/></VueFlow></div><aside class="properties"><h3>节点参数</h3><template v-if="selectedNode"><b>{{ selectedNode.data.label }}</b><textarea v-model="parameterText" :disabled="selectedNode.data.definition.kind !== 'operator'" rows="8"/><button v-if="selectedNode.data.definition.kind === 'operator'" type="button" @click="applyParameters">应用参数</button></template><p v-else>选择节点编辑参数。保存与发布时服务端仍会按已发布 Operator Schema 编译校验。</p></aside></div>
    </template>
    <section v-else-if="activeTab==='catalog'" class="panel"><h3>Operator Catalog</h3><table><thead><tr><th>算子</th><th>类别</th><th>暴露</th><th>端口</th></tr></thead><tbody><tr v-for="item in catalog" :key="item.id"><td>{{ item.name }}<small>{{ item.code }}</small></td><td>{{ item.category }}</td><td>{{ item.exposure }}</td><td>{{ JSON.stringify(item.input_ports) }} → {{ JSON.stringify(item.output_ports) }}</td></tr></tbody></table></section>
    <section v-else class="panel"><h3>可复用子图</h3><article v-for="item in subflows" :key="item.id"><b>{{ item.name }}</b><small>{{ item.code }} · r{{ item.revision }}</small></article></section>
    <pre v-if="result">{{ JSON.stringify(result,null,2) }}</pre><p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.tabs,.actions,.outputs{display:flex;gap:8px;flex-wrap:wrap}.tabs button.active{background:#1d4ed8;color:#fff}.template-head{display:grid;grid-template-columns:220px 1fr 180px;gap:14px}.template-row,.palette button{display:grid;width:100%;text-align:left;margin:5px 0}.stack{display:grid;gap:10px}.editor{display:grid;grid-template-columns:220px minmax(500px,1fr) 240px;gap:12px;margin-top:14px}.palette,.properties,.canvas{background:#fff;border:1px solid #dbe3ef;border-radius:12px;padding:12px}.canvas{height:620px;padding:0;overflow:hidden}.canvas :deep(.vue-flow){height:100%}.palette small,.template-row small{color:#64748b}.properties pre{white-space:pre-wrap}.active{outline:2px solid #2563eb}@media(max-width:1000px){.template-head,.editor{grid-template-columns:1fr}.canvas{height:500px}}
</style>
