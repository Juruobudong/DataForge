<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api/platform'
const pipelines = ref([]), error = ref('')
onMounted(async () => { try { pipelines.value = await api.standardPipelines() } catch (e) { error.value = e.message } })
const labels = { validation: ['文件检查', '格式 / 完整性'], parse: ['文件解析', 'PDF / DOCX / MD / TXT / CSV'], normalization: ['内容整理', '清洗 / 标准化'], structure_recovery: ['结构恢复', '保留结构与语义'], semantic_chunks: ['语义切片', '标准化 Chunk'] }
</script>
<template><section><div class="page-head"><div><h2>标准流水线</h2><p>统一维护所有知识生产都会经过的文档前置处理，不提供 DataFlow WebUI 或任意 DAG 编辑器。</p></div><div class="page-actions"><span class="badge green">当前默认</span></div></div><section v-for="pipeline in pipelines" :key="pipeline.code" class="panel"><div class="panel-head"><div><h3>公共前置处理</h3><p>{{ pipeline.code }}</p></div><span class="badge blue">受控线性步骤</span></div><div class="flow"><template v-for="(step,index) in pipeline.steps" :key="step"><div class="node"><b>{{ labels[step]?.[0] || step }}</b><small>{{ labels[step]?.[1] || '固定处理步骤' }}</small></div><span v-if="index < pipeline.steps.length-1" class="arrow">→</span></template><span class="arrow">→</span><div class="node"><b>知识流程模板</b><small>继续生成正式知识</small></div></div></section><p v-if="error" class="error">{{ error }}</p></section></template>
