<script setup>
import TypedHandle from '../ports/TypedHandle.vue'
defineProps({ data: { type: Object, required: true }, selected: Boolean })
</script>
<template>
  <article class="sink-node" :class="{ selected }">
    <header><span class="icon">✓</span><div><em>KNOWLEDGE SINK</em><b>{{ data.meta.code }}</b><small>正式知识输出</small></div></header>
    <section><TypedHandle v-for="(spec, port) in data.meta.inputs" :key="port" :port="port" :spec="spec" :definition="data.definition" :node-kind="data.meta.kind" direction="input" /></section>
    <footer>发布流程的受控终点</footer>
  </article>
</template>
<style scoped>
.sink-node{width:250px;overflow:hidden;border:1px solid #c8e7d9;border-radius:12px;background:#fbfffd;box-shadow:0 7px 22px rgba(29,140,101,.08)}.sink-node.selected{border-color:#1d8c65;box-shadow:0 0 0 2px rgba(29,140,101,.14),0 9px 25px rgba(29,140,101,.12)}header{display:grid;grid-template-columns:34px 1fr;gap:10px;align-items:center;padding:13px;border-bottom:1px solid #dceee6}.icon{display:grid;width:34px;height:34px;place-items:center;border-radius:50%;color:#fff;background:#1d8c65;font-weight:900}em,b,small{display:block}em{color:#1d8c65;font-size:8px;font-style:normal;font-weight:900;letter-spacing:.08em}b{margin-top:3px;font-size:11px;text-transform:capitalize}small{margin-top:2px;color:#6f857b;font-size:8px}section{min-height:52px;padding:6px 0}footer{padding:8px 13px;border-top:1px solid #dceee6;color:#658074;background:#f1faf6;font-size:8px;font-weight:750}
</style>
