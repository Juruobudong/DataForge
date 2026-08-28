<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../../api/platform'
import FieldHelp from '../common/FieldHelp.vue'
import { displayedEntityTypes, entityLabel, medicalCoverage } from './entityTypeModel'

const props = defineProps({ modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const catalog = ref(null), error = ref(''), busy = ref(false), adding = ref(false), name = ref('')
const editing = ref(null), editName = ref(''), editDescription = ref(''), editInput = ref(null), notice = ref('')
let editBaseline = ''
watch(() => JSON.stringify(props.modelValue), () => { editing.value = null; notice.value = '' })
let active = true
onBeforeUnmount(() => { active = false })
const types = computed(() => displayedEntityTypes(props.modelValue, catalog.value))
const coverage = computed(() => medicalCoverage(props.modelValue, catalog.value))
const complete = computed(() => coverage.value.total > 0 && coverage.value.count === coverage.value.total)
const medicalButton = computed(() => complete.value ? '－ 医疗' : coverage.value.count ? `＋ 补全医疗 ${coverage.value.count}/${coverage.value.total}` : '＋ 医疗')
const ownsMedical = computed(() => types.value.some(item => item.source === 'preset' && item.preset === 'medical'))
async function loadCatalog() {
  try { catalog.value = await api.graphEntityTypes(); error.value = '' }
  catch (e) { error.value = e.message }
}
async function resolve(action) {
  if (busy.value || props.disabled) return
  error.value = ''
  if (action === 'add_custom' || action === 'update') {
    const label = entityLabel(action === 'update' ? editName.value : name.value)
    if (!label) { error.value = '实体类型名称不能为空'; return }
    if (types.value.some(item => entityLabel(item.label) === label && (action !== 'update' || item.code !== editing.value?.code))) { error.value = `实体类型已存在：${label}`; return }
    if (action === 'update' && (!editing.value || editBaseline !== JSON.stringify(props.modelValue))) return
  }
  const before = JSON.stringify(props.modelValue)
  busy.value = true
  try {
    const original = editing.value
    const result = await api.resolveGraphEntityTypes({ entity_types: props.modelValue, action,
      ...(action === 'add_custom' ? { label: name.value } : {}),
      ...(action === 'update' ? { code: original.code, label: editName.value, description: editDescription.value } : {}) })
    if (!active || before !== JSON.stringify(props.modelValue) || props.disabled) return
    if (before !== JSON.stringify(result.entity_types)) emit('update:modelValue', result.entity_types)
    if (action === 'add_custom') { adding.value = false; name.value = '' }
    if (action === 'update') {
      editing.value = null
      await nextTick()
      if (active && original.source !== 'custom' && result.entity_types.find(item => item.code === original.code)?.source === 'custom') notice.value = '已自定义，不随医疗预设移除。'
    }
  } catch (e) { if (active && before === JSON.stringify(props.modelValue)) error.value = e.message }
  finally { busy.value = false }
}
function remove(index) { emit('update:modelValue', props.modelValue.filter((_, i) => i !== index)) }
async function edit(item) {
  if (props.disabled || busy.value) return
  adding.value = false; error.value = ''; notice.value = ''
  editing.value = { ...item }; editName.value = item.label; editDescription.value = item.description || ''
  editBaseline = JSON.stringify(props.modelValue)
  await nextTick(); editInput.value?.focus()
}
onMounted(loadCatalog)
</script>

<template>
  <section class="entity-type-editor" aria-label="实体类型">
    <header><b>实体类型</b><FieldHelp text="实体类型用于约束模型识别哪些对象，未配置的领域实体可通过“添加实体类型”补充。医疗预设只增强当前 Schema，不改变流程模板。" /></header>
    <div class="entity-chips">
      <span v-for="(item,index) in types" :key="item.code || item.label" class="entity-chip" :title="item.description || item.label">
        <button type="button" class="edit-type" :aria-label="`编辑${item.label}`" :disabled="disabled || busy" @click="edit(item)">{{ item.label }}</button><button type="button" :aria-label="`删除${item.label}`" :disabled="disabled || busy" @click="remove(index)">×</button>
      </span>
    </div>
    <p v-if="!types.length" class="muted">未配置实体类型，当前不约束模型识别的类型。</p>
    <div class="entity-actions">
      <button type="button" class="medical-preset" :disabled="disabled || busy || !catalog || (complete && !ownsMedical)" :title="complete ? '只移除医疗预设新增的类型，保留自定义类型' : '补充缺少的医疗实体类型'" @click="resolve(complete ? 'remove_medical' : 'add_medical')">{{ medicalButton }}</button>
      <button v-if="!complete && ownsMedical" type="button" :disabled="disabled || busy" title="移除剩余的医疗预设类型，保留自定义类型" @click="resolve('remove_medical')">－ 医疗</button>
      <button type="button" :disabled="disabled || busy" @click="adding = true; editing = null">＋ 添加实体类型</button>
      <button v-if="!catalog && error" type="button" @click="loadCatalog">重新加载预设</button>
    </div>
    <div v-if="adding" class="custom-entity">
      <input v-model="name" aria-label="实体类型名称" placeholder="例如：医疗设备" :disabled="disabled || busy" @keydown.enter.prevent="resolve('add_custom')" @keydown.esc.prevent="adding = false">
      <button type="button" :disabled="disabled || busy" @click="resolve('add_custom')">添加</button>
      <button type="button" :disabled="busy" @click="adding = false; name = ''; error = ''">取消</button>
    </div>
    <p v-if="error" role="alert" class="entity-error">{{ error }}</p>
    <section v-if="editing" class="entity-edit" @keydown.esc.prevent="!busy && (editing = null)">
      <label>实体名称<input ref="editInput" v-model="editName" aria-label="编辑实体名称" :disabled="disabled || busy"></label>
      <label>抽取说明<textarea v-model="editDescription" aria-label="实体抽取说明" rows="3" :disabled="disabled || busy"></textarea></label>
      <small>内部标识保持不变。修改基础/医疗预设项后转为自定义，不随医疗预设移除。</small>
      <div><button type="button" :disabled="disabled || busy" @click="resolve('update')">应用</button><button type="button" :disabled="busy" @click="editing = null; error = ''">取消</button></div>
    </section>
    <p v-if="notice" role="status" class="muted">{{ notice }}</p>
    <small>点击类型名称可编辑名称与说明；× 删除单项，「－ 医疗」仅移除预设包新增项。</small>
  </section>
</template>

<style scoped>
.entity-type-editor :deep(.field-help-trigger){min-height:18px;height:18px;width:18px}
.entity-type-editor{display:grid;gap:12px;color:#34445b;font-size:14px}.entity-type-editor header{display:flex;align-items:center;gap:7px}.entity-chips,.entity-actions{display:flex;flex-wrap:wrap;gap:8px}.entity-chip{display:inline-flex;align-items:center;gap:8px;border:1px solid #d7e4f8;border-radius:6px;padding:5px 8px;background:#eff5ff;color:#285ca4}.entity-chip button{border:0;padding:0 3px;min-height:0;background:none;color:inherit;font-size:18px;line-height:1.2}.entity-actions button{font-size:13px}.custom-entity{display:flex;gap:8px}.custom-entity input{min-width:0;flex:1}.entity-error{margin:0;color:#b93838}.muted,.entity-type-editor small{margin:0;color:#718095;font-size:12px}
</style>
<style scoped>
.entity-chip button.edit-type{font-size:14px;line-height:1.5;padding:0;text-align:left}.entity-edit{display:grid;gap:10px;padding:12px;border:1px solid #d7e4f8;border-radius:8px;background:#f8fbff}.entity-edit label{display:grid;gap:6px}.entity-edit input,.entity-edit textarea{width:100%;font-size:14px}.entity-edit>div{display:flex;gap:8px}
</style>
