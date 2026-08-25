<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../api/platform'
import DataForgeFlowCanvas from '../../components/flow/DataForgeFlowCanvas.vue'
import OperatorInspector from '../../components/flow/inspector/OperatorInspector.vue'
import { deserializeDefinition, serializeDefinition } from '../../components/flow/flowModel'

const route = useRoute(), router = useRouter(), detail = ref(null), catalog = ref([]), subflows = ref([]), nodes = ref([]), edges = ref([])
const selectedNode = ref(null), description = ref(''), inputContract = ref('{}'), outputContract = ref('{}'), error = ref(''), result = ref(null), canvas = ref(null), dirty = ref(false)
const editable = computed(() => detail.value?.revision_status === 'draft')
const selectedOperator = computed(() => catalog.value.find(item => item.code === selectedNode.value?.data?.meta?.code))
const trail = computed(() => { try { const value = JSON.parse(String(route.query.trail || '[]')); return Array.isArray(value) ? value : [] } catch { return [] } })
async function load() {
  try {
    const [value, operators, reusable] = await Promise.all([api.flowSubgraphRevision(route.params.subflowId, route.params.revision), api.operatorCatalog({ include_internal: true }), api.flowSubgraphs()])
    detail.value = value; catalog.value = operators; subflows.value = reusable; description.value = value.description || ''; inputContract.value = JSON.stringify(value.input_contract || {}, null, 2); outputContract.value = JSON.stringify(value.output_contract || {}, null, 2)
    const graph = deserializeDefinition(value.definition, operators, reusable); nodes.value = graph.nodes; edges.value = graph.edges; dirty.value = false
    await nextTick(); canvas.value?.fit()
  } catch (e) { error.value = e.message }
}
async function copyDraft() { try { const value = await api.copyFlowSubgraphDraft(detail.value.id, detail.value.revision); await router.push(`/developer/flow-templates/subgraphs/${detail.value.id}/revisions/${value.revision}`) } catch (e) { error.value = e.message } }
function payload() { return { definition: { ...serializeDefinition(nodes.value, edges.value), entry_node: detail.value.definition.entry_node, exit_node: detail.value.definition.exit_node }, description: description.value, input_contract: JSON.parse(inputContract.value || '{}'), output_contract: JSON.parse(outputContract.value || '{}') } }
async function action(kind) { try { result.value = kind === 'save' ? await api.updateFlowSubgraphDraft(detail.value.id, detail.value.revision, payload()) : kind === 'validate' ? await api.validateFlowSubgraphDraft(detail.value.id, detail.value.revision) : await api.publishFlowSubgraphDraft(detail.value.id, detail.value.revision); await load() } catch (e) { error.value = e.message } }
function openNested(node) { const target = subflows.value.find(item => item.code === node.data.definition.ref); if (target) router.push({ path: `/developer/flow-templates/subgraphs/${target.id}/revisions/${target.revision}`, query: { trail: JSON.stringify([...trail.value, { id: detail.value.id, revision: detail.value.revision, name: detail.value.name }]) } }) }
watch(() => `${route.params.subflowId}:${route.params.revision}`, () => load())
onMounted(load)
</script>

<template>
  <section>
    <nav class="breadcrumbs"><RouterLink to="/developer/flow-templates">知识流程</RouterLink><template v-for="item in trail" :key="`${item.id}-${item.revision}`"><span>›</span><RouterLink :to="`/developer/flow-templates/subgraphs/${item.id}/revisions/${item.revision}`">{{ item.name }} · r{{ item.revision }}</RouterLink></template><span>›</span><span>{{ detail?.name || '子图' }}</span><span>›</span><b>r{{ detail?.revision }}</b></nav>
    <div class="page-head"><div><h2>{{ detail?.name }}</h2><p>{{ description || '不可变子图 revision 的完整 DAG。双击嵌套 Subgraph 继续钻取。' }}</p></div><div class="page-actions"><span class="badge" :class="editable?'amber':'green'">{{ editable ? '草稿' : '只读 revision' }}</span><button v-if="!editable" class="primary" @click="copyDraft">复制为草稿</button><template v-else><button @click="action('validate')">校验</button><button @click="action('save')">保存草稿</button><button class="primary" @click="action('publish')">发布</button></template></div></div>
    <div v-if="detail" class="subgraph-workspace"><main><DataForgeFlowCanvas ref="canvas" v-model:nodes="nodes" v-model:edges="edges" :mode="editable?'edit':'readonly'" height="720" :canvas-id="`subgraph-${detail.id}-${detail.revision}`" @before-change="dirty=true" @select-node="selectedNode=$event" @open-subflow="openNested" /></main><aside><OperatorInspector :operator="selectedOperator" /><div v-if="editable" class="contracts"><label>描述<textarea v-model="description" rows="3"></textarea></label><label>输入契约<textarea v-model="inputContract" rows="7"></textarea></label><label>输出契约<textarea v-model="outputContract" rows="7"></textarea></label></div></aside></div>
    <p v-if="error" class="error">{{ error }}</p><pre v-if="result">{{ JSON.stringify(result, null, 2) }}</pre>
  </section>
</template>

<style scoped>
.breadcrumbs{display:flex;align-items:center;gap:8px;margin-bottom:10px;color:#748196}.breadcrumbs a{color:#2f6fed}.subgraph-workspace{display:grid;grid-template-columns:minmax(720px,1fr) 340px;gap:12px}.contracts{margin-top:12px;padding:12px;border:1px solid #dbe3ef;border-radius:12px;background:#fff}.contracts label{display:grid;gap:5px;margin-bottom:10px;font-size:11px;font-weight:700}.contracts textarea{font:11px monospace}@media(max-width:1050px){.subgraph-workspace{grid-template-columns:1fr}.subgraph-workspace aside{min-height:380px}}
</style>
