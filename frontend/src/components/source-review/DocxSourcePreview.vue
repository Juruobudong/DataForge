<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { anchorNotice, docxBlockIds } from './sourceAnchorModel'

const props = defineProps({ documentIr: { type: Object, default: () => ({}) }, anchor: { type: Object, default: () => ({}) } })
const viewport = ref(null)
const blocks = computed(() => props.documentIr?.blocks || props.documentIr?.anchor?.blocks || [])
const activeIds = computed(() => new Set(docxBlockIds(props.anchor)))
const notice = computed(() => anchorNotice(props.anchor))

function headingTag(block) { return `h${Math.max(2, Math.min(6, Number(block.heading_level || 3)))}` }
async function locate() {
  await nextTick()
  const first = docxBlockIds(props.anchor)[0]
  if (first) viewport.value?.querySelector(`[data-source-block="${CSS.escape(first)}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}
watch(() => props.anchor, locate, { deep: true, immediate: true })
</script>

<template>
  <section ref="viewport" class="docx-preview">
    <p v-if="notice" class="anchor-notice">{{ notice }}</p>
    <article v-if="blocks.length" class="docx-page">
      <template v-for="block in blocks" :key="block.block_id">
        <component :is="headingTag(block)" v-if="block.block_type === 'heading'" :data-source-block="block.block_id" :class="{ highlighted: activeIds.has(block.block_id) }">{{ block.text }}</component>
        <table v-else-if="block.block_type === 'table_row'" :data-source-block="block.block_id" :class="{ highlighted: activeIds.has(block.block_id) }"><tbody><tr><td v-for="(cell, index) in block.cells || [block.text]" :key="index">{{ cell }}</td></tr></tbody></table>
        <p v-else :data-source-block="block.block_id" :class="{ highlighted: activeIds.has(block.block_id) }">{{ block.text }}</p>
      </template>
    </article>
    <p v-else class="empty">DOCX 重新解析后将在此显示结构化标题、段落和表格。</p>
  </section>
</template>

<style scoped>
.docx-preview{height:720px;overflow:auto;padding:20px;background:#edf1f6}.docx-page{max-width:820px;min-height:660px;margin:auto;padding:48px 56px;background:#fff;box-shadow:0 4px 18px rgba(48,61,78,.13)}.docx-page h2,.docx-page h3,.docx-page h4,.docx-page h5,.docx-page h6,.docx-page p,.docx-page table{scroll-margin-block:160px;transition:background .18s,box-shadow .18s}.docx-page p{line-height:1.8}.docx-page table{width:100%;margin:10px 0;border-collapse:collapse}.docx-page td{padding:8px 10px;border:1px solid #d8dee8}.highlighted{border-radius:4px;background:rgba(255,190,46,.28);box-shadow:0 0 0 3px rgba(47,111,237,.35)}.anchor-notice{position:sticky;top:-20px;z-index:4;margin:-20px -20px 18px;padding:9px 12px;border-bottom:1px solid #ead39a;color:#805b0a;background:#fff7dd;font-size:12px}.empty{padding:60px 20px;color:var(--muted);text-align:center}
</style>
