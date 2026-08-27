<script setup>
import { computed, inject } from 'vue'
import TypedHandle from '../ports/TypedHandle.vue'
import { subflowSubtitle } from '../flowModel.js'
import { edgeNodeClasses } from '../edge/edgeInteraction.js'
const props = defineProps({ id: { type: String, required: true }, data: { type: Object, required: true }, selected: Boolean, showTechnicalCode: { type: Boolean, default: false } })
const edgeInteraction = inject('dataforge-edge-interaction', computed(() => ({ mode: 'idle', compatiblePorts: new Map() })))
const interactionClasses = computed(() => edgeNodeClasses(edgeInteraction.value, props.id))
const subtitle = computed(() => subflowSubtitle(props.data.meta, props.showTechnicalCode))
</script>
<template>
  <article class="subflow-node" :class="[{ selected }, interactionClasses]">
    <header><span class="icon">◈</span><div><em>可复用子流程</em><b>{{ data.meta.name }}</b><small>{{ subtitle }}</small><small v-if="!data.meta.versionLocked">版本未锁定</small></div></header>
    <section class="ports"><div><TypedHandle v-for="(spec, port) in data.meta.inputs" :key="port" :node-id="id" :port="port" :spec="spec" :definition="data.definition" :node-kind="data.meta.kind" direction="input" /></div><div><TypedHandle v-for="(spec, port) in data.meta.outputs" :key="port" :node-id="id" :port="port" :spec="spec" :definition="data.definition" :node-kind="data.meta.kind" direction="output" /></div></section>
    <footer><span>{{ data.meta.internalCount }} 节点 / {{ data.meta.internalEdgeCount }} 连线</span><span>双击查看内部 DAG</span></footer>
  </article>
</template>
<style scoped>
.subflow-node{width:270px;overflow:hidden;border:1px solid #c9dafa;border-radius:12px;background:#f8fbff;box-shadow:0 7px 22px rgba(47,111,237,.09)}.subflow-node.selected{border-color:#2f6fed;box-shadow:0 0 0 2px rgba(47,111,237,.16),0 9px 25px rgba(47,111,237,.13)}header{display:grid;grid-template-columns:32px 1fr;gap:10px;align-items:center;padding:12px 13px;border-bottom:1px solid #dce7fa}.icon{display:grid;width:32px;height:32px;place-items:center;border-radius:9px;color:#2f6fed;background:#dce8ff;font-size:17px}em,b,small{display:block}em{color:#2f6fed;font-size:8px;font-style:normal;font-weight:850;letter-spacing:.06em}b{margin-top:2px;font-size:11px}small{margin-top:2px;color:#71809a;font-size:8px}.ports{display:grid;min-height:58px;grid-template-columns:1fr 1fr;padding:6px 0}.ports>div+div{border-left:1px solid #e0e8f5}footer{display:flex;justify-content:space-between;padding:8px 13px;border-top:1px solid #dce7fa;color:#6f7f96;background:#f1f6ff;font-size:8px;font-weight:750}
.subflow-node.edge-source-node{border-color:#2f6fed;box-shadow:0 0 0 3px rgba(47,111,237,.18)}.subflow-node.edge-compatible-node{border-color:#1d8c65;box-shadow:0 0 0 3px rgba(29,140,101,.15)}.subflow-node.edge-incompatible-node{opacity:.45}
</style>
