<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { normalizedPositions } from './sourceAnchorModel'
import { scrollTargetWithin } from './sourcePreviewScroll'

const props = defineProps({ documentIr: Object, anchor: Object })
const root = ref(null)
const selectedIds = computed(() => new Set(normalizedPositions(props.anchor || {}).map(item => item.block_id).filter(Boolean)))

function blockLabel(block) {
  if (block.sheet) return `${block.sheet} · 第${block.row}行`
  if (block.json_pointer) return `JSON ${block.json_pointer}`
  if (block.line_number || block.line_start) return `第${block.line_start || block.line_number}${block.line_end && block.line_end !== block.line_start ? `–${block.line_end}` : ''}行`
  return `字符 ${block.char_start ?? 0}–${block.char_end ?? 0}`
}

watch(() => props.anchor, async () => {
  await nextTick()
  const target = root.value?.querySelector('[data-selected="true"]')
  if (root.value && target) scrollTargetWithin(root.value, target)
}, { deep: true })
</script>

<template>
  <div ref="root" class="structured-preview">
    <article v-for="block in documentIr?.blocks || []" :key="block.block_id"
      :data-selected="selectedIds.has(block.block_id)" :class="{ selected: selectedIds.has(block.block_id) }">
      <small>{{ blockLabel(block) }}</small>
      <pre>{{ block.text }}</pre>
    </article>
    <pre v-if="!documentIr?.blocks?.length">{{ documentIr?.text || '解析完成后将在此显示结构化原文。' }}</pre>
  </div>
</template>

<style scoped>
.structured-preview{height:100%;min-height:0;overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding:12px}.structured-preview article{padding:8px 10px;border-left:3px solid transparent}.structured-preview article.selected{border-left-color:var(--blue);background:var(--blue-soft)}small{color:var(--muted)}pre{margin:4px 0 0;white-space:pre-wrap;font:inherit;line-height:1.65}
</style>
