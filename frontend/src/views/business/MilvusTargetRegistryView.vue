<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const instance = ref(null), targets = ref([]), deployments = ref([])
const name = ref(''), uri = ref(''), editing = ref(null)
const busy = ref(false), error = ref(''), notice = ref('')
const central = computed(() => instance.value?.instance_mode === 'central')

function statusLabel(value) {
  return { verified: '连接通过', pending_verification: '正在验证', verification_failed: '连接失败' }[value] || '待验证'
}
function statusClass(value) {
  return value === 'verified' ? 'green' : value === 'verification_failed' ? 'red' : 'amber'
}
function references(targetId) {
  return deployments.value.flatMap(deployment => Object.entries(deployment.stage_targets || {})
    .filter(([, target]) => target.id === targetId)
    .map(([stage]) => `${deployment.name} · ${stage === 'production' ? '生产' : '测试'}`))
}
async function load() {
  error.value = ''
  try {
    instance.value = await api.instance()
    if (!central.value) return
    ;[targets.value, deployments.value] = await Promise.all([api.milvusTargets(), api.sharedDeployments()])
  } catch (e) { error.value = e.message }
}
async function createTarget() {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const value = await api.createMilvusTarget({ name: name.value.trim(), milvus_url: uri.value.trim() })
    name.value = ''; uri.value = ''; await load()
    notice.value = value.verification_status === 'verified' ? 'Milvus 服务连接通过并已注册。' : '服务已保留，但连接失败，请修正地址后重试。'
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
function startEdit(target) {
  editing.value = { id: target.id, name: target.name, milvus_url: target.candidate_milvus_url || target.milvus_url }
}
async function saveEdit() {
  if (!editing.value) return
  const target = targets.value.find(item => item.id === editing.value.id)
  const changedUri = editing.value.milvus_url.trim() !== (target?.milvus_url || '')
  const needsVerification = changedUri || target?.verification_status !== 'verified'
  const refs = references(editing.value.id)
  if (changedUri && refs.length && !window.confirm(`新地址验证通过后将用于以下中心环境：\n${refs.join('\n')}\n\n连接失败不会覆盖当前地址。`)) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const value = await api.patchMilvusTarget(editing.value.id, {
      name: editing.value.name.trim(), milvus_url: needsVerification ? editing.value.milvus_url.trim() : undefined,
    })
    editing.value = null; await load()
    notice.value = value.candidate_verification_status === 'verification_failed'
      ? '候选地址连接失败，当前已验证地址保持不变。' : 'Milvus 服务已更新。'
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <section class="milvus-registry-page">
    <div class="page-head"><div><h2>Milvus 服务</h2><p>注册中心可访问的 Milvus；新增或改址会自动执行最小连接测试。</p></div><span class="badge blue">中心控制面</span></div>
    <p v-if="!central && instance" class="notice">只有智能中心可以管理 Milvus 服务注册表；机构 Milvus 请在“本地初始化”中配置。</p>
    <template v-else>
      <form class="panel registry-form" @submit.prevent="createTarget"><div><h3>新增服务</h3><p>连接失败时保留配置，但不能绑定到中心环境。</p></div><label>服务名称<input v-model="name" required maxlength="255"></label><label>Milvus URI<input v-model="uri" required placeholder="http://milvus:19531"></label><button class="primary" :disabled="busy">{{ busy ? '正在连接验证…' : '注册并验证' }}</button></form>
      <section class="panel"><div class="panel-head"><div><h3>服务注册表</h3><p>{{ targets.length }} 个服务</p></div></div><table><thead><tr><th>服务</th><th>当前 URI</th><th>状态</th><th>中心引用</th><th>操作</th></tr></thead><tbody><tr v-for="target in targets" :key="target.id"><td><b>{{ target.name }}</b><br><small><code>{{ target.id }}</code></small></td><td><code>{{ target.milvus_url }}</code><p v-if="target.candidate_milvus_url" class="candidate">候选：<code>{{ target.candidate_milvus_url }}</code></p></td><td><span class="badge" :class="statusClass(target.candidate_verification_status || target.verification_status)">{{ statusLabel(target.candidate_verification_status || target.verification_status) }}</span><small v-if="target.candidate_verification_error || target.verification_error" class="error-text">{{ target.candidate_verification_error || target.verification_error }}</small></td><td><span v-if="!references(target.id).length" class="muted">未绑定</span><div v-for="value in references(target.id)" :key="value">{{ value }}</div></td><td><button @click="startEdit(target)">编辑</button></td></tr><tr v-if="!targets.length"><td colspan="5" class="muted">暂无 Milvus 服务。</td></tr></tbody></table></section>
    </template>
    <dialog :open="Boolean(editing)" class="dialog" @close="editing=null"><form v-if="editing" class="stack" @submit.prevent="saveEdit"><h3>编辑 Milvus 服务</h3><label>服务名称<input v-model="editing.name" required></label><label>Milvus URI<input v-model="editing.milvus_url" required></label><p>改址会先验证候选地址；失败不会覆盖当前已验证地址。</p><div class="actions"><button type="button" @click="editing=null">取消</button><button class="primary" :disabled="busy">{{ busy ? '正在连接验证…' : '保存并验证' }}</button></div></form></dialog>
    <p v-if="notice" class="notice">{{ notice }}</p><p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.milvus-registry-page{display:grid;gap:18px}.registry-form{display:grid;grid-template-columns:minmax(240px,1fr) minmax(220px,1fr) minmax(320px,2fr) auto;align-items:end;gap:16px}.registry-form p{margin:4px 0 0;color:var(--muted)}label{display:grid;gap:6px}.candidate{margin:6px 0 0;color:var(--amber)}.error-text{display:block;max-width:420px;margin-top:7px;color:var(--danger)}.dialog{width:min(620px,calc(100vw - 40px));border:0;border-radius:14px;box-shadow:0 22px 70px #0f172a33}.dialog::backdrop{background:#0f172a66}@media(max-width:1200px){.registry-form{grid-template-columns:1fr 1fr}.registry-form>div{grid-column:1/-1}}
</style>
