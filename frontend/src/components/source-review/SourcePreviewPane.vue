<script setup>
import { computed } from 'vue'
import { api } from '../../api/platform'

const props = defineProps({ source: Object, version: Object, documentIr: Object, selectedPage: Number })
const isPdf = computed(() => (props.source?.original_filename || '').toLowerCase().endsWith('.pdf'))
const previewUrl = computed(() => {
  if (!isPdf.value || !props.source?.id || !props.version?.id) return ''
  const page = Math.max(1, Number(props.selectedPage || 1))
  return `${api.sourcePreviewUrl(props.source.id, props.version.id)}#page=${page}`
})
</script>

<template>
  <section class="source-preview">
    <header><div><h3>原文</h3><span v-if="isPdf">PDF 第 {{ selectedPage || 1 }} 页</span></div><a :href="api.sourceDownloadUrl(source?.id, version?.id)">下载原文</a></header>
    <iframe v-if="isPdf" :key="previewUrl" :src="previewUrl" title="PDF 原文预览"></iframe>
    <pre v-else>{{ documentIr?.text || '解析完成后将在此显示结构化原文。' }}</pre>
  </section>
</template>

<style scoped>
.source-preview{min-width:0;overflow:auto;border:1px solid var(--border);border-radius:12px;background:#fff}.source-preview header{position:sticky;top:0;z-index:2;display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-bottom:1px solid var(--border);background:#fff}.source-preview h3{margin:0}.source-preview header span{color:var(--muted);font-size:var(--font-assist)}iframe{width:100%;min-height:720px;border:0}pre{min-height:660px;margin:0;padding:18px;white-space:pre-wrap;line-height:1.75}
</style>
