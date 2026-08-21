<script setup>
import { computed } from 'vue'

const props = defineProps({ graphConfig: { type: Object, default: () => ({}) } })

const entityLines = computed(() => (props.graphConfig.entity_types || []).map(e => `${e.label || e.code}${e.code ? `（${e.code}）` : ''}${e.description ? `：${e.description}` : ''}`))
const relationLines = computed(() => (props.graphConfig.relation_types || []).map(r => {
  const source = (r.source_types || []).join('、') || '任意'
  const target = (r.target_types || []).join('、') || '任意'
  return `${r.label || r.code}${r.code ? `（${r.code}）` : ''}：${source} → ${target}`
}))
const entityBlock = computed(() => entityLines.value.length
  ? `仅允许抽取以下实体类型：\n\n${entityLines.value.join('\n')}`
  : '未定义实体类型，请自由抽取当前分块中的全部实体，type 使用与原文相同语言的简洁、稳定类型名称，并使用原文语言给出 description。')
const relationBlock = computed(() => relationLines.value.length
  ? `仅允许以下关系：\n\n${relationLines.value.join('\n')}`
  : '未定义关系类型，请自由抽取已抽取实体之间的关系，type 和 label 使用与原文相同语言的简洁、稳定关系词，并使用原文语言给出 description。')
const prompt = computed(() => `${entityBlock.value}

实体名称、关系表述、描述、别名和关键词必须保持当前来源分块的原文语言，不得翻译；中文原文用中文，英文原文用英文。
已定义 Graph Schema 时 type 仍使用 Schema 的技术 code，但关系 label 使用原文语言；未定义类型时 type 和 label 均使用原文语言。

禁止将以下内容作为实体：
纯数字、数值范围、百分比、剂量、温度、时长、日期、页码、编号。

${relationBlock.value}

当前来源分块：
{{source_chunk}}`)
</script>

<template>
  <div class="prompt-preview">
    <p class="muted">由 Graph Schema 自动生成的抽取 Prompt；高级模式可覆盖，但输出仍经过 Schema Validator。</p>
    <pre>{{ prompt }}</pre>
  </div>
</template>

<style scoped>
.prompt-preview pre { white-space: pre-wrap; margin: 10px 0 0; max-height: 420px; overflow: auto; }
.muted { color: var(--muted); font-size: 13px; }
</style>
