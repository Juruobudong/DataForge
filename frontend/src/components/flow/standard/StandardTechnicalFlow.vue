<script setup>
import { computed, ref } from 'vue'
import { runtimeArtifactLabel } from '../flowModel'
const props = defineProps({ value: { type: Object, required: true } })
const selected = ref(null)
const providers = { dataforge: 'DataForge', dataflow: 'DataFlow', custom: 'Custom' }
const nodes = computed(() => props.value.resolved_operators || [])
const links = computed(() => (props.value.edges || []).map(edge => Array.isArray(edge) ? { source: edge[0], target: edge[1] } : edge))
const roots = computed(() => nodes.value.filter(node => !links.value.some(edge => edge.target === node.node_id)))
const branches = computed(() => nodes.value.filter(node => node.kind === 'knowledge_sink').map(sink => {
  const ancestors = new Set(), pending = [sink.node_id]
  while (pending.length) { const id = pending.pop(); if (ancestors.has(id)) continue; ancestors.add(id); pending.push(...links.value.filter(edge => edge.target === id).map(edge => edge.source)) }
  return { sink, nodes: nodes.value.filter(node => ancestors.has(node.node_id) && !roots.value.includes(node)) }
}))
</script>

<template>
  <section class="technical-flow" aria-label="只读技术流程">
    <p>以下为当前配置物化的实际执行算子；结构由固定模板维护。参数请在业务流程中配置。</p>
    <p v-for="issue in value.issues || []" :key="issue.node_id" class="error">{{ issue.message }}</p>
    <button v-for="node in roots" :key="node.node_id" class="operator-step root-step" @click="selected = node"><b>{{ node.display_name_zh }}</b><small>{{ node.name }} · v{{ node.version }} · {{ providers[node.provider] }}</small></button>
    <div class="branches">
      <section v-for="branch in branches" :key="branch.sink.node_id" class="branch">
        <h4>{{ runtimeArtifactLabel(`candidate:${branch.sink.output_key}`) }}</h4>
        <template v-for="node in branch.nodes" :key="node.node_id">
          <span aria-hidden="true" class="chain-arrow">↓</span>
          <button class="operator-step" @click="selected = node"><small>{{ node.stage_label }}</small><b>{{ node.display_name_zh }}</b><small>{{ node.name }} · v{{ node.version }} · {{ providers[node.provider] }}</small></button>
        </template>
      </section>
    </div>
    <aside v-if="selected" class="operator-detail" aria-label="算子详情" @keydown.esc="selected = null">
      <button class="close" aria-label="关闭算子详情" @click="selected = null">关闭</button>
      <h3>{{ selected.display_name_zh }}</h3><p>{{ selected.name }} · {{ selected.code }} · v{{ selected.version }}</p>
      <p>{{ selected.description }}</p><p>{{ providers[selected.provider] }} · {{ selected.uses_llm ? '使用 LLM' : '不使用 LLM' }}</p>
      <h4>输入类型</h4><p v-for="(port, name) in selected.input_ports" :key="name">{{ name }}：{{ port.artifact_type }}</p>
      <h4>输出类型</h4><p v-for="(port, name) in selected.output_ports" :key="name">{{ name }}：{{ port.artifact_type }}</p>
      <h4>当前业务参数（只读）</h4><dl><template v-for="(value, key) in selected.parameters" :key="key"><dt>{{ key }}</dt><dd>{{ value ?? '系统默认' }}</dd></template></dl>
    </aside>
  </section>
</template>

<style scoped>
.technical-flow{position:relative;display:grid;gap:12px}.technical-flow>p{color:#64748b;line-height:1.6}.branches{display:flex;gap:16px;overflow:auto;align-items:start}.branch{min-width:220px;flex:1;padding:14px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px}.branch h4{margin:0;text-align:center}.operator-step{display:grid;width:100%;gap:6px;padding:12px;text-align:left;background:#fff;border:1px solid #dbe3ee;border-radius:9px}.operator-step small{color:#64748b;white-space:normal;overflow-wrap:anywhere}.operator-step b{font-size:15px}.chain-arrow{display:block;text-align:center;color:#94a3b8;margin:6px}.root-step{max-width:360px;justify-self:center}.operator-detail{position:fixed;z-index:80;right:16px;top:86px;bottom:16px;width:min(420px,45vw);padding:20px;background:#fff;border:1px solid #b6c9ee;border-radius:12px;box-shadow:0 8px 32px #1e293b1a;overflow:auto}.close{float:right}.operator-detail dl{display:grid;grid-template-columns:minmax(100px,1fr) 2fr;gap:8px;overflow-wrap:anywhere}.operator-detail dd{margin:0}.error{color:#b42318}
</style>
