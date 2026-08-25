<script setup>
import { computed } from 'vue'
import { api } from '../../api/platform'
import DocxSourcePreview from './DocxSourcePreview.vue'
import PdfSourcePreview from './PdfSourcePreview.vue'

const props = defineProps({ source: Object, version: Object, documentIr: Object, selectedAnchor: Object })
const isPdf = computed(() => (props.source?.original_filename || '').toLowerCase().endsWith('.pdf'))
const isDocx = computed(() => (props.source?.original_filename || '').toLowerCase().endsWith('.docx'))
const previewUrl = computed(() => isPdf.value && props.source?.id && props.version?.id ? api.sourcePreviewUrl(props.source.id, props.version.id) : '')
</script>

<template>
  <section class="source-preview">
    <header><div><h3>原文证据</h3><span>{{ isPdf ? 'PDF 精确定位' : isDocx ? 'DOCX 结构化定位' : 'DocumentIR' }}</span></div><a :href="api.sourceDownloadUrl(source?.id, version?.id)">下载原文</a></header>
    <PdfSourcePreview v-if="isPdf && previewUrl" :url="previewUrl" :anchor="selectedAnchor || {}" />
    <DocxSourcePreview v-else-if="isDocx" :document-ir="documentIr || {}" :anchor="selectedAnchor || {}" />
    <pre v-else>{{ documentIr?.text || '解析完成后将在此显示结构化原文。' }}</pre>
  </section>
</template>

<style scoped>
.source-preview{min-width:0;overflow:hidden;border:1px solid var(--border);border-radius:12px;background:#fff}.source-preview>header{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-bottom:1px solid var(--border);background:#fff}.source-preview h3{margin:0}.source-preview header span{color:var(--muted);font-size:var(--font-assist)}pre{height:720px;overflow:auto;margin:0;padding:18px;white-space:pre-wrap;line-height:1.75}
</style>
