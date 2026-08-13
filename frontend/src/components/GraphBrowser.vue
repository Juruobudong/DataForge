<script setup>
import { ref } from 'vue'
import { VueFlow } from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'
import { api } from '../api/platform'

const props = defineProps({ libraryId: { type: String, required: true } })
const query = ref(''), matches = ref([]), nodes = ref([]), edges = ref([]), selected = ref(null), evidence = ref([]), error = ref('')
function asFlow(graph) {
  nodes.value = graph.nodes.map((node, index) => ({ id: node.id, data: { label: `${node.name} (${node.type})` }, position: { x: 80 + (index % 4) * 190, y: 80 + Math.floor(index / 4) * 120 } }))
  edges.value = graph.edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, label: edge.predicate }))
}
async function search() { try { matches.value = await api.graphEntities(props.libraryId, query.value); error.value = '' } catch (e) { error.value = e.message } }
async function openEntity(entity, depth = 1) { try { selected.value = await api.graphEntity(props.libraryId, entity.id); evidence.value = []; asFlow(await api.graphNeighbors(props.libraryId, entity.id, depth)) } catch (e) { error.value = e.message } }
async function nodeClick({ node }) { try { selected.value = await api.graphEntity(props.libraryId, node.id); evidence.value = [] } catch (e) { error.value = e.message } }
async function edgeClick({ edge }) { try { const result = await api.graphEvidence(props.libraryId, edge.id); selected.value = result.relation; evidence.value = result.evidence } catch (e) { error.value = e.message } }
</script>

<template>
  <section class="graph-browser">
    <form @submit.prevent="search"><input v-model="query" placeholder="搜索实体，例如：高血压"><button>搜索</button></form>
    <div class="graph-layout">
      <aside><button v-for="entity in matches" :key="entity.id" @click="openEntity(entity)">{{ entity.name }} <small>{{ entity.type }}</small></button></aside>
      <div class="graph-canvas"><VueFlow :nodes="nodes" :edges="edges" fit-view-on-init @node-click="nodeClick" @edge-click="edgeClick" /></div>
      <aside v-if="selected"><h4>{{ selected.name || selected.predicate }}</h4><p v-if="selected.type">类型：{{ selected.type }}</p><p v-if="selected.relation_count !== undefined">关联：{{ selected.relation_count }}</p><button v-if="selected.id && selected.name" @click="openEntity(selected, 2)">扩展到 2 跳</button><details v-if="evidence.length" open><summary>Evidence（{{ evidence.length }}）</summary><p v-for="item in evidence" :key="item.id"><b>{{ item.source.original_filename }}</b><br>{{ item.anchor.label || item.anchor.file }}<br>{{ item.evidence_text }}</p></details></aside>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.graph-layout{display:grid;grid-template-columns:180px minmax(320px,1fr) 240px;gap:12px;margin-top:12px}.graph-layout aside{display:grid;align-content:start;gap:8px}.graph-canvas{height:420px;border:1px solid #d8dee9;border-radius:8px;background:#fafbfc}.graph-browser button{padding:6px 9px;text-align:left}.graph-browser small{display:block}@media(max-width:900px){.graph-layout{grid-template-columns:1fr}.graph-canvas{order:-1}}
</style>
