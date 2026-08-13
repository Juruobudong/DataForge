<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const documents = ref([]), sources = ref([]), jobs = ref([]), libraries = ref([]), projects = ref([]), error = ref('')
const ready = computed(() => libraries.value.filter(item => item.vector_ready).length)
const activeJobs = computed(() => jobs.value.filter(item => ['queued', 'running'].includes(item.status)).length)
const byType = type => computed(() => libraries.value.filter(item => item.knowledge_type === type).length)
async function load() { try { [documents.value, sources.value, jobs.value, libraries.value, projects.value] = await Promise.all([api.documentLibraries(), api.sources(), api.knowledgeJobs(), api.knowledgeLibraries(), api.projects()]) } catch (e) { error.value = e.message } }
onMounted(load)
</script>

<template>
  <section>
    <div class="page-head"><div><h2>工作台</h2><p>统一查看文档、任务、正式知识、向量索引和项目知识授权状态。</p></div><div class="page-actions"><span class="badge green">V7 运行中</span></div></div>
    <div class="metrics"><div class="metric"><b>{{ sources.length }}</b><span>文档 · {{ documents.length }} 个文档库</span></div><div class="metric"><b>{{ byType('text').value }}</b><span>文本知识库</span></div><div class="metric"><b>{{ byType('qa').value }}</b><span>问答知识库</span></div><div class="metric"><b>{{ byType('graph').value }}</b><span>知识图谱</span></div><div class="metric"><b>{{ ready }}</b><span>Vector Ready</span></div></div>
    <div class="grid2"><section class="panel"><div class="panel-head"><div><h3>知识生产</h3><p>文档经受控 Parse / Clean / Chunk / Production / Publish 后生成当前有效知识。</p></div><span class="badge blue">{{ activeJobs }} 个进行中</span></div><div class="flow"><div class="node"><b>文档管理</b><small>原始资料</small></div><span class="arrow">→</span><div class="node"><b>受控流程</b><small>解析 / 切片 / 质量</small></div><span class="arrow">→</span><div class="node"><b>Knowledge Sink</b><small>文 / 问 / 图</small></div><span class="arrow">→</span><div class="node"><b>正式知识库</b><small>当前有效状态</small></div></div></section><section class="panel"><div class="panel-head"><div><h3>索引与授权</h3><p>正式知识完成向量同步后，才能由项目 RoutingSnapshot 使用。</p></div><span class="badge amber">{{ projects.length }} 个项目</span></div><div class="flow"><div class="node"><b>Index Profile</b><small>已发布类型配置</small></div><span class="arrow">→</span><div class="node"><b>Embedding</b><small>V7 受控配置</small></div><span class="arrow">→</span><div class="node"><b>Collection + Partition</b><small>动态 Collection / kl_ 分区</small></div><span class="arrow">→</span><div class="node"><b>项目知识授权</b><small>RoutingSnapshot</small></div></div></section></div>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>
