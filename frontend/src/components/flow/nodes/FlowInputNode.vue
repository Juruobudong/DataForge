<script setup>
import { computed, inject } from 'vue'
import TypedHandle from '../ports/TypedHandle.vue'
import { edgeNodeClasses } from '../edge/edgeInteraction.js'
const props = defineProps({ id: { type: String, required: true }, data: { type: Object, required: true }, selected: Boolean })
const edgeInteraction = inject('dataforge-edge-interaction', computed(() => ({ mode: 'idle', compatiblePorts: new Map() })))
const interactionClasses = computed(() => edgeNodeClasses(edgeInteraction.value, props.id))
</script>

<template>
  <article class="input-node" :class="[{ selected }, interactionClasses]">
    <header><span class="icon">→</span><div><em>INPUT</em><b>文档输入</b><small>运行时绑定 · ParsedDocument</small></div></header>
    <section><TypedHandle v-for="(spec, port) in data.meta.outputs" :key="port" :node-id="id" :port="port" :spec="spec" :definition="data.definition" :node-kind="data.meta.kind" direction="output" /></section>
    <footer>流程输入 · 系统节点</footer>
  </article>
</template>

<style scoped>
.input-node{width:270px;overflow:hidden;border:1px solid #bfd3f5;border-radius:12px;background:#fbfdff;box-shadow:0 7px 22px rgba(47,111,237,.08)}.input-node.selected{border-color:#2f6fed;box-shadow:0 0 0 2px rgba(47,111,237,.14),0 9px 25px rgba(47,111,237,.12)}header{display:grid;grid-template-columns:34px 1fr;gap:10px;align-items:center;padding:13px;border-bottom:1px solid #dce7f7}.icon{display:grid;width:34px;height:34px;place-items:center;border-radius:50%;color:#fff;background:#2f6fed;font-weight:900}em,b,small{display:block}em{color:#2f6fed;font-size:8px;font-style:normal;font-weight:900;letter-spacing:.08em}b{margin-top:3px;font-size:11px}small{margin-top:2px;color:#6d7f98;font-size:8px}section{min-height:52px;padding:6px 0}footer{padding:8px 13px;border-top:1px solid #dce7f7;color:#657c9c;background:#f1f6ff;font-size:8px;font-weight:750}
.input-node.edge-source-node{border-color:#2f6fed;box-shadow:0 0 0 3px rgba(47,111,237,.18)}.input-node.edge-compatible-node{border-color:#1d8c65;box-shadow:0 0 0 3px rgba(29,140,101,.15)}.input-node.edge-incompatible-node{opacity:.45}
</style>
