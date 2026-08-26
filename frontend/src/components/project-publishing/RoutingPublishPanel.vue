<script setup>
import { checkLabel } from '../../constants/checkLabels'
defineProps({ validation: { type: Object, required: true }, result: { type: Object, default: null }, preview: { type: Object, default: null }, institution: { type: Boolean, default: false }, ready: { type: Boolean, default: false }, problems: { type: Array, default: () => [] }, actionLabel: { type: String, required: true } })
defineEmits(['diff','validate','release'])
</script>

<template>
  <section class="panel stack">
    <div class="panel-head"><div><h3>发布配置</h3><p v-if="institution">中心只冻结不可变项目版本，现场验证和激活在机构本地执行。</p><p v-else>发布检查通过后，中心 Runtime 将立即使用新版本。</p></div></div>
    <p v-if="!ready" class="muted">发布前还需完成：{{ problems.join('；') }}</p>
    <div class="actions"><button @click="$emit('diff')">查看配置差异</button><button class="success" @click="$emit('validate')">执行发布检查</button><button class="primary" :disabled="!ready" @click="$emit('release')">{{ actionLabel }}</button></div>
    <template v-if="validation.available">
      <p v-if="validation.deferred" class="notice">机构 Milvus 实体验证将在本地 Prepare / Activation Preflight 执行。</p>
      <div class="table-wrap"><table><thead><tr><th>检查</th><th>状态</th><th>对象</th><th>Expected</th><th>Observed</th><th>说明</th></tr></thead><tbody><tr v-for="(check,index) in validation.checks" :key="`${check.code}-${index}`"><td>{{ checkLabel(check.code) }}<br><code>{{ check.code }}</code></td><td><span class="badge" :class="check.status==='passed'?'green':'red'">{{ check.status==='passed'?'通过':'阻断' }}</span></td><td><code>{{ check.subject?.collection_name || check.subject?.partition_name || check.subject?.knowledge_library_id || '—' }}</code></td><td><code>{{ JSON.stringify(check.expected) }}</code></td><td><code>{{ JSON.stringify(check.observed) }}</code></td><td>{{ check.message }}</td></tr></tbody></table></div>
      <details><summary>发布配置快照</summary><pre>{{ JSON.stringify(result?.snapshot,null,2) }}</pre></details>
    </template>
    <pre v-else-if="preview">{{ JSON.stringify(preview.snapshot,null,2) }}</pre>
    <pre v-else-if="result">{{ JSON.stringify(result,null,2) }}</pre>
  </section>
</template>
