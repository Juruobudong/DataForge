<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/platform'
const router = useRouter(); const libraries = ref([]); const name = ref(''); const keyword = ref(''); const status = ref(''); const error = ref(''); const selected = ref([])
async function load() { try { libraries.value = await api.documentLibraries(keyword.value, status.value) } catch (e) { error.value = e.message } }
async function createLibrary() { try { await api.createDocumentLibrary({ name: name.value }); name.value=''; await load() } catch (e) { error.value=e.message } }
function open(library) { router.push(`/business/documents/${library.id}`) }
async function destroyLibraries() {
  if (!selected.value.length) return
  try { const body = { document_library_ids: selected.value }; const check = await api.documentDeletionPreflight(body); if (!check.deletable) throw new Error('存在运行任务，整批不能彻底删除。'); if (!confirm(`将彻底删除 ${check.document_library_count} 个文档库及其中 ${check.source_count} 个文件。\n此操作不可撤销，确定继续吗？`)) return; await api.requestDocumentDeletion(body); selected.value=[]; await load() } catch (e) { error.value=e.message }
}
onMounted(load)
</script>
<template><section><div class="page-head"><div><h2>文档管理</h2><p>创建文档库后直接进入目录浏览、文件夹导入和知识处理。</p></div><div class="page-actions"><button class="danger" :disabled="!selected.length" @click="destroyLibraries">彻底删除文档库</button></div></div><form class="actions" @submit.prevent="load"><input v-model="keyword" placeholder="搜索文档库"><select v-model="status"><option value="">全部状态</option><option value="active">启用</option></select><button>搜索</button></form><form class="actions" @submit.prevent="createLibrary"><input v-model="name" required placeholder="文档库名称"><button class="primary">新建文档库</button></form><p v-if="error" class="error">{{ error }}</p><div class="cards"><article v-for="library in libraries" :key="library.id" class="card clickable" @click="open(library)"><label @click.stop><input v-model="selected" type="checkbox" :value="library.id"> 选择</label><b>{{ library.name }}</b><small>{{ library.code }} · {{ library.updated_at }}</small><span class="badge blue">打开文档库</span></article></div></section></template>
