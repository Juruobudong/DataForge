<script setup>
import { computed } from 'vue'
import { literalDatatypeLabels } from '../../constants/knowledgeLabels'
import EntityTypeEditor from './EntityTypeEditor.vue'

const props = defineProps({ modelValue: { type: Object, required: true } })
const emit = defineEmits(['update:modelValue'])

const literalOptions = Object.entries(literalDatatypeLabels).map(([key, label]) => ({ key, label }))

const entityTypes = computed(() => props.modelValue.entity_types || [])
const relationTypes = computed(() => props.modelValue.relation_types || [])
const entityCodes = computed(() => entityTypes.value.map(item => item.code).filter(Boolean))

function patch(partial) { emit('update:modelValue', { ...props.modelValue, ...partial }) }
function updateEntityTypes(value) { patch({ entity_types: value }) }
function addRelationType() {
  patch({ relation_types: [...relationTypes.value, { code: '', label: '', description: '', source_types: [], target_types: [] }] })
}
function removeRelationType(index) {
  const next = relationTypes.value.slice(); next.splice(index, 1)
  patch({ relation_types: next })
}
function updateRelationType(index, field, value) {
  const next = relationTypes.value.map((item, i) => i === index ? { ...item, [field]: value } : item)
  patch({ relation_types: next })
}
function toggleDatatype(key, checked) {
  const enabled = props.modelValue.literal_policy?.enabled_datatypes || []
  const next = checked ? [...enabled, key] : enabled.filter(item => item !== key)
  patch({ literal_policy: { enabled_datatypes: next } })
}
function datatypeEnabled(key) {
  const enabled = props.modelValue.literal_policy?.enabled_datatypes
  return !enabled || enabled.includes(key)
}
</script>

<template>
  <div class="schema-editor">
    <section class="schema-block">
      <EntityTypeEditor :model-value="entityTypes" @update:model-value="updateEntityTypes" />
    </section>

    <section class="schema-block">
      <header><h4>关系类型</h4><button type="button" @click="addRelationType">+ 新增关系类型</button></header>
      <div v-if="relationTypes.length" class="type-list">
        <div v-for="(item, index) in relationTypes" :key="index" class="relation-row">
          <div class="relation-line">
            <input v-model="item.label" placeholder="中文名称（如：使用药物）" @input="updateRelationType(index, 'label', $event.target.value)">
            <input v-model="item.code" placeholder="代码 code（如：uses_drug）" @input="updateRelationType(index, 'code', $event.target.value)">
            <button type="button" class="danger" @click="removeRelationType(index)">删除</button>
          </div>
          <div class="relation-constraints">
            <label>source 类型
              <select multiple :model-value="item.source_types || []" @change="updateRelationType(index, 'source_types', [...$event.target.selectedOptions].map(o => o.value))">
                <option v-for="code in entityCodes" :key="code" :value="code">{{ entityTypes.find(item => item.code === code)?.label || code }}</option>
              </select>
            </label>
            <label>target 类型
              <select multiple :model-value="item.target_types || []" @change="updateRelationType(index, 'target_types', [...$event.target.selectedOptions].map(o => o.value))">
                <option v-for="code in entityCodes" :key="code" :value="code">{{ entityTypes.find(item => item.code === code)?.label || code }}</option>
              </select>
            </label>
          </div>
        </div>
      </div>
      <p v-else class="muted">尚未定义关系类型。</p>
    </section>

    <section class="schema-block">
      <header><h4>Literal 规则</h4></header>
      <div class="literal-grid">
        <label v-for="opt in literalOptions" :key="opt.key" class="check"><input type="checkbox" :checked="datatypeEnabled(opt.key)" @change="toggleDatatype(opt.key, $event.target.checked)">{{ opt.label }}</label>
      </div>
    </section>

    <section class="schema-block">
      <header><h4>未识别实体处理</h4></header>
      <div class="policy-row">
        <label><input type="radio" name="unknown-entity" value="reject" :checked="modelValue.unknown_entity_policy !== 'other' && modelValue.unknown_entity_policy !== 'suggest'" @change="patch({ unknown_entity_policy: 'reject' })"> 拒绝</label>
        <label><input type="radio" name="unknown-entity" value="other" :checked="modelValue.unknown_entity_policy === 'other'" @change="patch({ unknown_entity_policy: 'other' })"> 归为「其他」</label>
        <label><input type="radio" name="unknown-entity" value="suggest" :checked="modelValue.unknown_entity_policy === 'suggest'" @change="patch({ unknown_entity_policy: 'suggest' })"> 允许模型建议新类型</label>
      </div>
    </section>
  </div>
</template>

<style scoped>
.schema-editor { display: grid; gap: 14px; }
.schema-block { padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); box-shadow: var(--shadow); }
.schema-block header { display: flex; align-items: center; justify-content: space-between; }
.schema-block h4 { margin: 0; font-size: 14px; }
.type-list { display: grid; gap: 8px; margin-top: 10px; }
.type-row { display: grid; grid-template-columns: 1fr 1fr 1.4fr auto; gap: 8px; align-items: center; }
.type-row input, .relation-line input { min-width: 0; }
.relation-row { display: grid; gap: 8px; padding: 8px; border: 1px solid #e3e8ef; border-radius: 8px; }
.relation-line { display: grid; grid-template-columns: 1fr 1fr auto; gap: 8px; align-items: center; }
.relation-constraints { display: flex; gap: 12px; }
.relation-constraints label { display: grid; gap: 4px; font-size: 12px; color: var(--muted); }
.relation-constraints select { min-height: 54px; }
.literal-grid { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; }
.literal-grid label, .policy-row label { display: flex; align-items: center; gap: 5px; font-size: 13px; color: #536177; }
.policy-row { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 8px; }
.muted { color: var(--muted); font-size: 13px; }
</style>
