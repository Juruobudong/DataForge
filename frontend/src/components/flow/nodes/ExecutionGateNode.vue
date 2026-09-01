<script setup>
import { computed, inject } from 'vue'
import TypedHandle from '../ports/TypedHandle.vue'
import { edgeNodeClasses } from '../edge/edgeInteraction.js'

const props = defineProps({ id: { type: String, required: true }, data: { type: Object, required: true }, selected: Boolean })
const edgeInteraction = inject('dataforge-edge-interaction', computed(() => ({ mode: 'idle', compatiblePorts: new Map() })))
const interactionClasses = computed(() => edgeNodeClasses(edgeInteraction.value, props.id))
</script>

<template>
  <article class="gate-node" :class="[{ selected }, interactionClasses]">
    <div class="diamond"><span>◇</span></div>
    <div class="gate-copy"><em>EXECUTION GATE</em><b>自动冻结输入快照</b><small>校验已批准 ParsedDocument 后继续</small></div>
    <TypedHandle v-for="(spec, port) in data.meta.inputs" :key="`in-${port}`" :node-id="id" :port="port" :spec="spec" :definition="data.definition" :node-kind="data.meta.kind" direction="input" />
    <TypedHandle v-for="(spec, port) in data.meta.outputs" :key="`out-${port}`" :node-id="id" :port="port" :spec="spec" :definition="data.definition" :node-kind="data.meta.kind" direction="output" />
  </article>
</template>

<style scoped>
.gate-node{position:relative;display:grid;width:270px;min-height:116px;grid-template-columns:62px 1fr;align-items:center;gap:8px;padding:18px;border:1px solid #e2c36e;border-radius:14px;background:#fffdf5;box-shadow:0 7px 22px rgba(139,104,10,.1)}.gate-node.selected{border-color:#c99000;box-shadow:0 0 0 2px rgba(201,144,0,.18)}.diamond{display:grid;width:50px;height:50px;place-items:center;transform:rotate(45deg);border:2px solid #c99000;border-radius:7px;background:#fff6d6}.diamond span{transform:rotate(-45deg);color:#946b00;font-size:24px;font-weight:900}.gate-copy em,.gate-copy b,.gate-copy small{display:block}.gate-copy em{color:#946b00;font-size:8px;font-style:normal;font-weight:900;letter-spacing:.08em}.gate-copy b{margin-top:5px;font-size:12px}.gate-copy small{margin-top:4px;color:#7d6c3e;font-size:8px;line-height:1.4}.gate-node.edge-source-node,.gate-node.edge-compatible-node{border-color:#1d8c65}.gate-node.edge-incompatible-node{opacity:.45}
</style>
