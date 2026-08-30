<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/platform'

const instance = ref(null), targets = ref([]), deployments = ref([])
const name = ref(''), uri = ref(''), token = ref(''), editing = ref(null)
const busy = ref(false), collectionCheckId = ref(''), collectionChecks = ref({}), error = ref(''), notice = ref('')
const central = computed(() => instance.value?.instance_mode === 'central')
const startupTargetIds = new Set(['milvus_dataforge_central_test', 'milvus_dataforge_central_production'])

function statusLabel(value) {
  return { verified: '连接通过', pending_verification: '正在验证', verification_failed: '连接失败' }[value] || '待验证'
}
function statusClass(value) {
  return value === 'verified' ? 'green' : value === 'verification_failed' ? 'red' : 'amber'
}
function healthLabel(value) {
  return { healthy: '当前可用', unavailable: '当前不可达', unknown: '尚未检查' }[value] || '尚未检查'
}
function healthClass(value) {
  return value === 'healthy' ? 'green' : value === 'unavailable' ? 'red' : 'amber'
}
function healthDetails(target) {
  if (!target.health_checked_at) {
    return startupTargetIds.has(target.id) ? '启动 30 秒后自动检查连接' : '尚未检查，可手动检查连接'
  }
  const checkedAt = new Date(target.health_checked_at).toLocaleString('zh-CN', { hour12: false })
  return `${checkedAt}${target.health_latency_ms == null ? '' : ` · ${target.health_latency_ms} ms`}`
}
function references(targetId) {
  const values = deployments.value.flatMap(deployment => Object.entries(deployment.stage_targets || {})
    .filter(([, target]) => target.id === targetId)
    .map(([stage]) => `${deployment.name} · ${stage === 'production' ? '生产' : '测试'}`))
  if (instance.value?.authoring_milvus_target?.id === targetId) values.unshift('默认知识写入目标')
  return values
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
    const value = await api.createMilvusTarget({ name: name.value.trim(), milvus_url: uri.value.trim(), token: token.value || null })
    name.value = ''; uri.value = ''; token.value = ''; await load()
    notice.value = value.verification_status === 'verified' ? 'Milvus 服务连接通过并已注册。' : '服务已保留，但连接失败，请修正地址后重试。'
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
function startEdit(target) {
  editing.value = { id: target.id, name: target.name, milvus_url: target.candidate_milvus_url || target.milvus_url, token: '', preserve_token: true }
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
      token: editing.value.token || undefined, preserve_token: editing.value.preserve_token,
    })
    editing.value = null; await load()
    notice.value = value.candidate_verification_status === 'verification_failed'
      ? '候选地址连接失败，当前已验证地址保持不变。' : 'Milvus 服务已更新。'
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function verifyTarget(target) {
  busy.value = true; error.value = ''; notice.value = ''
  try { await api.verifyMilvusTarget(target.id); await load(); notice.value = 'Milvus 服务连接验证通过。' }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function checkHealth(target) {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const value = await api.checkMilvusTargetHealth(target.id); await load()
    notice.value = value.health_status === 'healthy'
      ? `「${target.name}」当前连接可用。` : `「${target.name}」当前连接不可达，现有配置与绑定保持不变。`
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function checkCollections(target) {
  collectionCheckId.value = target.id; error.value = ''; notice.value = ''
  try {
    const value = await api.checkMilvusTargetCollections(target.id)
    collectionChecks.value = { ...collectionChecks.value, [target.id]: value }
    notice.value = value.status === 'available'
      ? `「${target.name}」Collection 检查完成。` : `「${target.name}」Collection 检查失败。`
  } catch (e) { error.value = e.message } finally { collectionCheckId.value = '' }
}
async function setAuthoring(target) {
  busy.value = true; error.value = ''; notice.value = ''
  try { await api.putAuthoringMilvusTarget(target.id); await load(); notice.value = `已将「${target.name}」设为默认知识写入目标。` }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <section class="milvus-registry-page">
    <div class="page-head"><div><h2>Milvus 服务</h2><p>API 启动 30 秒后只自动检查中心测试与生产服务的连接；Collection 名称和数量仅在管理员点击后读取。</p></div><span class="badge blue">中心控制面</span></div>
    <p v-if="!central && instance" class="notice">只有智能中心可以管理 Milvus 服务注册表；机构 Milvus 请在“本地初始化”中配置。</p>
    <template v-else>
      <form class="panel registry-form" @submit.prevent="createTarget"><div><h3>新增服务</h3><p>连接失败时保留配置，但不能设为默认知识写入目标或绑定中心环境。</p></div><label>服务名称<input v-model="name" required maxlength="255"></label><label>Milvus URI<input v-model="uri" required placeholder="http://milvus:19531"></label><label>Token（可选）<input v-model="token" type="password" autocomplete="new-password"></label><button class="primary" :disabled="busy">{{ busy ? '正在连接验证…' : '注册并验证' }}</button></form>
      <section class="panel"><div class="panel-head"><div><h3>服务注册表</h3><p>{{ targets.length }} 个服务；默认知识写入目标：{{ instance?.authoring_milvus_target?.name || '未配置' }}</p></div></div><table><thead><tr><th>服务</th><th>当前 URI</th><th>配置与健康</th><th>中心引用</th><th>操作</th></tr></thead><tbody><tr v-for="target in targets" :key="target.id"><td><b>{{ target.name }}</b><br><small><code>{{ target.id }}</code></small></td><td><code>{{ target.milvus_url }}</code><small>{{ target.token_configured ? ' · Token 已配置' : ' · 无 Token' }}</small><p v-if="target.candidate_milvus_url" class="candidate">候选：<code>{{ target.candidate_milvus_url }}</code></p></td><td><div class="status-stack"><div><small>配置</small><span class="badge" :class="statusClass(target.verification_status)">{{ statusLabel(target.verification_status) }}</span></div><div v-if="target.current_revision_id"><small>当前健康</small><span class="badge" :class="healthClass(target.health_status)">{{ healthLabel(target.health_status) }}</span><small>{{ healthDetails(target) }}</small><small v-if="target.health_error" class="error-text">{{ target.health_error }}</small></div><div v-if="target.candidate_revision_id"><small>候选配置</small><span class="badge" :class="statusClass(target.candidate_verification_status)">{{ statusLabel(target.candidate_verification_status) }}</span><small v-if="target.candidate_verification_error" class="error-text">{{ target.candidate_verification_error }}</small></div><div v-if="collectionChecks[target.id]" class="collection-result"><small>Collection</small><span class="badge" :class="collectionChecks[target.id].status==='available'?'green':'red'">{{ collectionChecks[target.id].status==='available' ? `共 ${collectionChecks[target.id].collection_count} 个` : '检查失败' }}</span><small v-if="collectionChecks[target.id].status==='available'">DataForge {{ collectionChecks[target.id].dataforge_collection_count }} 个<template v-if="collectionChecks[target.id].dataforge_collections?.length">：{{ collectionChecks[target.id].dataforge_collections.join('、') }}</template></small><small v-else class="error-text">{{ collectionChecks[target.id].error }}</small></div></div></td><td><span v-if="!references(target.id).length" class="muted">未绑定</span><div v-for="value in references(target.id)" :key="value">{{ value }}</div></td><td><div class="row-actions"><button @click="startEdit(target)">编辑</button><button v-if="target.current_revision_id" :disabled="busy" @click="checkHealth(target)">检查当前连接</button><button v-if="target.current_revision_id" :disabled="busy || collectionCheckId===target.id" @click="checkCollections(target)">{{ collectionCheckId===target.id ? '正在检查 Collection…' : '检查 Collection' }}</button><button v-if="target.candidate_revision_id" :disabled="busy" @click="verifyTarget(target)">验证候选</button><button v-if="target.verification_status==='verified'&&instance?.authoring_milvus_target?.id!==target.id" :disabled="busy" @click="setAuthoring(target)">设为默认写入</button></div></td></tr><tr v-if="!targets.length"><td colspan="5" class="muted">暂无 Milvus 服务。</td></tr></tbody></table></section>
    </template>
    <dialog :open="Boolean(editing)" class="dialog" @close="editing=null"><form v-if="editing" class="stack" @submit.prevent="saveEdit"><h3>编辑 Milvus 服务</h3><label>服务名称<input v-model="editing.name" required></label><label>Milvus URI<input v-model="editing.milvus_url" required></label><label>新 Token（留空则保留）<input v-model="editing.token" type="password" autocomplete="new-password"></label><label><input v-model="editing.preserve_token" type="checkbox"> 留空时保留已有 Token</label><p>URI 或 Token 变化会创建并验证候选连接；失败不会覆盖当前已验证连接。</p><div class="actions"><button type="button" @click="editing=null">取消</button><button class="primary" :disabled="busy">{{ busy ? '正在连接验证…' : '保存并验证' }}</button></div></form></dialog>
    <p v-if="notice" class="notice">{{ notice }}</p><p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.milvus-registry-page{display:grid;gap:18px}.registry-form{display:grid;grid-template-columns:minmax(240px,1fr) minmax(220px,1fr) minmax(320px,2fr) auto;align-items:end;gap:16px}.registry-form p{margin:4px 0 0;color:var(--muted)}label{display:grid;gap:6px}.candidate{margin:6px 0 0;color:var(--amber)}.status-stack{display:grid;gap:8px}.status-stack>div{display:grid;grid-template-columns:72px max-content;align-items:center;gap:4px 8px}.status-stack>div>small:last-child{grid-column:1/-1}.error-text{display:block;max-width:420px;margin-top:7px;color:var(--danger)}.dialog{width:min(620px,calc(100vw - 40px));border:0;border-radius:14px;box-shadow:0 22px 70px #0f172a33}.dialog::backdrop{background:#0f172a66}@media(max-width:1200px){.registry-form{grid-template-columns:1fr 1fr}.registry-form>div{grid-column:1/-1}}
</style>
