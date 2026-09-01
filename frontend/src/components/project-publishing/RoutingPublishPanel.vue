<script setup>
import { checkLabel } from '../../constants/checkLabels'
const props = defineProps({ validation: { type: Object, required: true }, result: { type: Object, default: null }, diff: { type: Object, default: null }, institution: { type: Boolean, default: false }, ready: { type: Boolean, default: false }, busy: { type: Boolean, default: false }, problems: { type: Array, default: () => [] }, actionLabel: { type: String, required: true }, libraries: { type: Array, default: () => [] } })
defineEmits(['validate','release'])
function libraryName(id) { return props.libraries.find(item => item.id === id)?.name || id }
function routeSummary(route) {
  return {
    task: route?.task_code || '—', org: route?.org_code || '—',
    libraries: (route?.libraries || []).map(item => `${libraryName(item.knowledge_library_id)}${item.asset_version_no ? ` · Asset V${item.asset_version_no}` : ''}`),
    policy: `召回 ${route?.top_k ?? '—'} · 最终 ${route?.final_top_k ?? '—'} · ${route?.reranker_serving_code || '关闭重排'}`,
  }
}
</script>

<template>
  <section class="panel stack">
    <div class="panel-head"><div><h3>发布</h3><p v-if="institution">中心冻结不可变项目版本，现场验证和激活在机构本地执行。</p><p v-else>发布检查通过后，当前环境 Runtime 将使用新版本。</p></div></div>
    <p v-if="problems.length" class="muted">配置准备项：{{ problems.join('；') }}</p>
    <p v-if="!validation.available" class="notice">执行发布检查会同时运行 Preflight 并读取当前版本 Diff；上下文或配置变化后必须重新检查。</p>
    <div class="actions"><button class="success" :disabled="busy" @click="$emit('validate')">{{ busy?'检查中…':'执行发布检查' }}</button><button class="primary" :disabled="!ready||busy" @click="$emit('release')">{{ actionLabel }}</button></div>
    <template v-if="validation.available">
      <p v-if="validation.deferred" class="notice">机构 Milvus 实体验证将在本地 Prepare / Activation Preflight 执行。</p>
      <div class="table-wrap"><table><thead><tr><th>检查</th><th>状态</th><th>对象</th><th>Expected</th><th>Observed</th><th>说明</th></tr></thead><tbody><tr v-for="(check,index) in validation.checks" :key="`${check.code}-${index}`"><td>{{ checkLabel(check.code) }}<br><code>{{ check.code }}</code></td><td><span class="badge" :class="check.status==='passed'?'green':'red'">{{ check.status==='passed'?'通过':'阻断' }}</span></td><td><code>{{ check.subject?.collection_name || check.subject?.partition_name || check.subject?.knowledge_library_id || '—' }}</code></td><td><code>{{ JSON.stringify(check.expected) }}</code></td><td><code>{{ JSON.stringify(check.observed) }}</code></td><td>{{ check.message }}</td></tr></tbody></table></div>
      <details><summary>发布配置快照</summary><pre>{{ JSON.stringify(result?.snapshot,null,2) }}</pre></details>
    </template>
    <section v-if="diff" class="diff-section stack"><div class="panel-head"><div><h4>配置变化</h4><p>从 V{{ diff.from_version || 0 }} 开始：新增 {{ diff.summary?.added || 0 }}、移除 {{ diff.summary?.removed || 0 }}、修改 {{ diff.summary?.changed || 0 }}</p></div><span class="badge blue">{{ diff.summary?.total || 0 }} 项</span></div><article v-for="item in diff.added || []" :key="`add:${item.task_code}:${item.org_code}`" class="diff-card added"><b>新增 · {{ routeSummary(item).task }} / {{ routeSummary(item).org }}</b><p>{{ routeSummary(item).policy }}</p><p>知识库：{{ routeSummary(item).libraries.join('、') || '无' }}</p></article><article v-for="item in diff.removed || []" :key="`remove:${item.task_code}:${item.org_code}`" class="diff-card removed"><b>移除 · {{ routeSummary(item).task }} / {{ routeSummary(item).org }}</b><p>{{ routeSummary(item).policy }}</p><p>知识库：{{ routeSummary(item).libraries.join('、') || '无' }}</p></article><article v-for="(item,index) in diff.changed || []" :key="`change:${index}`" class="diff-card changed"><b>修改 · {{ routeSummary(item.after).task }} / {{ routeSummary(item.after).org }}</b><div class="diff-columns"><div><small>当前已发布</small><p>{{ routeSummary(item.before).policy }}</p><p>{{ routeSummary(item.before).libraries.join('、') || '无知识库' }}</p></div><div><small>即将发布</small><p>{{ routeSummary(item.after).policy }}</p><p>{{ routeSummary(item.after).libraries.join('、') || '无知识库' }}</p></div></div></article><p v-if="!diff.summary?.total" class="muted">当前配置与已发布版本一致。</p><details><summary>原始 Diff</summary><pre>{{ JSON.stringify(diff,null,2) }}</pre></details></section>
  </section>
</template>

<style scoped>
.actions{display:flex;gap:8px}.diff-section{margin-top:8px;padding-top:16px;border-top:1px solid var(--border)}.diff-card{padding:14px;border:1px solid var(--border);border-left-width:4px;border-radius:9px}.diff-card.added{border-left-color:var(--green)}.diff-card.removed{border-left-color:var(--red)}.diff-card.changed{border-left-color:var(--amber)}.diff-columns{display:grid;grid-template-columns:1fr 1fr;gap:12px}.diff-columns>div{padding:10px;background:#f7f9fc;border-radius:8px}@media(max-width:900px){.diff-columns{grid-template-columns:1fr}}
</style>
