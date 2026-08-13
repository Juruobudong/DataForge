<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'
const data = ref({ profiles: [], capacity: [] }), error = ref('')
const capacityByCollection = computed(() => Object.fromEntries(data.value.capacity.map(item => [item.collection_name, item])))
async function load() { try { data.value = await api.vectorIndexes() } catch (e) { error.value = e.message } }
onMounted(load)
</script>
<template><section><div class="page-head"><div><h2>向量索引</h2><p>正式知识按已发布 Index Profile 写入管理员指定的已有 Collection，并以知识库 ID 管理独立 Partition。</p></div><div class="page-actions"><span class="badge blue">动态 Profile</span></div></div><div class="cards"><article v-for="profile in data.profiles" :key="profile.id"><div class="panel-head"><div><h3>{{ profile.code }}</h3><p>{{ profile.collection_name }}</p></div><span class="badge" :class="profile.status==='active'?'green':'amber'">{{ profile.status }}</span></div><div><small>知识类型</small><b>{{ profile.knowledge_type }}</b></div><div><small>Vector Input</small><b>{{ Object.values(profile.fields || {}).join(' + ') || '受控字段' }}</b></div><div><small>容量</small><b>{{ capacityByCollection[profile.collection_name]?.entity_count ?? '—' }}</b></div></article></div><div class="grid2"><section class="panel"><h3>Embedding Profile</h3><p>模型、维度和相似度由已发布 Profile 配置控制。</p></section><section class="panel"><h3>Partition 管理</h3><p>平台只管理 <code>kl_&lt;knowledge_library_id&gt;</code>，业务项目只消费 RoutingSnapshot。</p></section></div><p v-if="error" class="error">{{ error }}</p></section></template>
