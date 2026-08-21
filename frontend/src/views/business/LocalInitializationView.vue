<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const instance = ref(null), health = ref(null), configs = ref([]), error = ref(''), notice = ref('')
const uri = ref(''), username = ref(''), secret = ref(''), tls = ref(false)
const candidate = computed(() => configs.value.find(item => item.slot === 'candidate_target'))
const current = computed(() => configs.value.find(item => item.slot === 'current_target'))
const steps = computed(() => [
  ['管理员认证', true], ['导入 deployment_seed', instance.value?.initialized],
  ['机构身份锁定', instance.value?.initialized], ['MySQL / MinIO 检查', health.value?.components?.mysql?.status === 'ok'],
  ['Milvus / Embedding / LLM', candidate.value?.status === 'verified' || current.value?.status === 'verified'],
  ['Worker / Runner / 解析与磁盘', health.value?.components?.disk?.status === 'ok' &&
    health.value?.components?.worker?.status === 'ok' && health.value?.components?.runner?.status === 'ok'],
  ['向量导入与验证', instance.value?.initialized], ['进入项目候选激活', instance.value?.initialized],
])
async function load() {
  try {
    ;[instance.value, health.value, configs.value] = await Promise.all([api.instance(), api.health(), api.localMilvusConfigurations()])
    const value = candidate.value || current.value
    if (value) { uri.value = value.uri; username.value = value.username || ''; tls.value = value.tls_enabled }
  } catch (e) { error.value = e.message }
}
async function saveCandidate() {
  try {
    await api.putLocalMilvusConfiguration('candidate_target', { uri: uri.value, username: username.value || null, secret: secret.value || null, tls_enabled: tls.value, database_name: 'default', preserve_secret: !secret.value })
    secret.value = ''; notice.value = '候选配置已保存；地址或凭据变化会自动清除旧验证状态。'; await load()
  } catch (e) { error.value = e.message }
}
async function verifyCandidate() { try { await api.verifyLocalMilvusConfiguration('candidate_target'); notice.value = '候选 Milvus 验证通过。'; await load() } catch (e) { error.value = e.message } }
async function promote() { try { await api.promoteLocalMilvusCandidate(); notice.value = '候选目标已切换为 current；原运行路由在切换失败时不会改变。'; await load() } catch (e) { error.value = e.message } }
onMounted(load)
</script>
<template><section><div class="page-head"><div><h2>机构本地初始化</h2><p>部署负责 MySQL、MinIO 与管理员预配；向导只做身份、组件、模型、向量与候选路由检查。</p></div><span class="badge blue">{{ instance?.display_name }}</span></div><div class="grid2"><section class="panel"><h3>初始化步骤</h3><ol class="timeline"><li v-for="([label,done],index) in steps" :key="label" :class="{done}"><b>{{ index+1 }}. {{ label }}</b><span class="badge" :class="done?'green':'amber'">{{ done?'通过':'待完成' }}</span></li></ol></section><section class="panel stack"><div class="panel-head"><div><h3>Milvus 候选目标</h3><p>发布包预设只用于创建 candidate，不覆盖 current。</p></div><span class="badge" :class="candidate?.status==='verified'?'green':'amber'">{{ candidate?.status||'未配置' }}</span></div><p>Current：<code>{{ current?.uri||'未配置' }}</code></p><label>Candidate URI<input v-model="uri" placeholder="http://milvus.internal:19531"></label><label>用户名<input v-model="username"></label><label>密码 / Token<input v-model="secret" type="password" autocomplete="new-password" placeholder="留空保持原密文"></label><label><input v-model="tls" type="checkbox"> 启用 TLS</label><div class="actions"><button @click="saveCandidate">保存候选</button><button class="success" :disabled="!candidate" @click="verifyCandidate">验证</button><button class="primary" :disabled="candidate?.status!=='verified'" @click="promote">切换为 Current</button></div></section></div><section class="panel"><h3>组件健康</h3><div class="card-grid"><div v-for="(value,key) in health?.components" :key="key" class="stat-card"><small>{{ key }}</small><b>{{ value.status }}</b><span v-if="key==='disk'">可用 {{ (value.free_bytes/1073741824).toFixed(1) }} GB</span></div></div></section><p v-if="notice" class="notice">{{ notice }}</p><p v-if="error" class="error">{{ error }}</p></section></template>
