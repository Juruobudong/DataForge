<script setup>
import { ref } from 'vue'
const props = defineProps({ current: Object, latest: Object, busy: Boolean })
const emit = defineEmits(['close', 'submit'])
const choice = ref('latest')
function summary(item) { const value = item?.params || {}; return `${value.chunk_size || '—'} 字符 / Overlap ${value.overlap_percent ?? '—'}%` }
</script>

<template>
  <div class="overlay" role="dialog" aria-modal="true" aria-label="重新分块"><section class="dialog"><h3>重新分块</h3><p>当前结果：Source Preparation r{{ current?.revision || '—' }} · {{ summary(current) }}</p><label><input v-model="choice" type="radio" value="latest"> 最新已发布方案 r{{ latest?.revision || '—' }} · {{ summary(latest) }}</label><label><input v-model="choice" type="radio" value="current"> 保持原方案 r{{ current?.revision || '—' }} · {{ summary(current) }}</label><p class="notice">重新分块不会立即删除当前正式结果；新 Candidate 需要重新审核。</p><footer><button @click="emit('close')">取消</button><button class="primary" :disabled="busy" @click="emit('submit', choice === 'latest' ? latest?.execution_snapshot_id : current?.execution_snapshot_id)">重新分块</button></footer></section></div>
</template>

<style scoped>
.overlay{position:fixed;inset:0;z-index:40;display:grid;place-items:center;background:#10182866}.dialog{width:min(520px,calc(100vw - 48px));padding:22px;border-radius:14px;background:#fff;box-shadow:var(--shadow)}label{display:block;margin:12px 0}.notice{padding:10px;border-radius:8px;background:var(--blue-soft);color:#405069}footer{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}
</style>
