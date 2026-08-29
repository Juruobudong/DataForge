<script setup>
import { computed } from 'vue'
import { api } from '../../api/platform'
import DocxSourcePreview from './DocxSourcePreview.vue'
import PdfSourcePreview from './PdfSourcePreview.vue'
import StructuredSourcePreview from './StructuredSourcePreview.vue'

const props = defineProps({ source: Object, version: Object, documentIr: Object, selectedAnchor: Object })
const isPdf = computed(() => (props.source?.original_filename || '').toLowerCase().endsWith('.pdf'))
const isDocx = computed(() => (props.source?.original_filename || '').toLowerCase().endsWith('.docx'))
const previewUrl = computed(() => isPdf.value && props.source?.id && props.version?.id ? api.sourcePreviewUrl(props.source.id, props.version.id) : '')
</script>

<template>
  <section class="source-preview">
    <PdfSourcePreview v-if="isPdf && previewUrl" :url="previewUrl" :anchor="selectedAnchor || {}" />
    <DocxSourcePreview v-else-if="isDocx" :document-ir="documentIr || {}" :anchor="selectedAnchor || {}" />
    <StructuredSourcePreview v-else :document-ir="documentIr || {}" :anchor="selectedAnchor || {}" />
  </section>
</template>

<style scoped>
.source-preview{display:grid;height:100%;min-width:0;min-height:0;grid-template-rows:minmax(0,1fr);overflow:hidden;border:1px solid var(--border);border-radius:12px;background:#fff}pre{height:100%;min-height:0;overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable;margin:0;padding:18px;white-space:pre-wrap;line-height:1.75}
</style>
