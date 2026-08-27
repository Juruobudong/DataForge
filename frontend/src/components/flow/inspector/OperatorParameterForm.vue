<script setup>
import ServingSelector from './ServingSelector.vue'
import PromptRevisionSelector from './PromptRevisionSelector.vue'
import QualityProfileSelector from './QualityProfileSelector.vue'
import EntityTypeEditor from '../../graph/EntityTypeEditor.vue'

const props = defineProps({ schema: { type: Object, default: () => ({}) }, modelValue: { type: Object, default: () => ({}) }, entityTypes: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const widget = spec => spec?.['x-dataforge-ui']?.widget || ''
function patch(name, value) { emit('update:modelValue', { ...props.modelValue, [name]: value }) }
function scalar(name, spec, event) {
  const raw = event.target.type === 'checkbox' ? event.target.checked : event.target.value
  patch(name, ['integer', 'number'].includes(spec.type) ? Number(raw) : raw)
}
function tags(name, event) { patch(name, event.target.value.split(',').map(item => item.trim()).filter(Boolean)) }
function constraints(name) { return Array.isArray(props.modelValue[name]) ? props.modelValue[name] : [] }
function addConstraint(name) { patch(name, [...constraints(name), { relation_type: '', source_types: [], target_types: [] }]) }
function updateConstraint(name, index, field, raw) {
  const next = constraints(name).map(item => ({ ...item }))
  next[index][field] = field === 'relation_type' ? raw : raw.split(',').map(item => item.trim()).filter(Boolean)
  patch(name, next)
}
function removeConstraint(name, index) { patch(name, constraints(name).filter((_, itemIndex) => itemIndex !== index)) }
function subset(value, scope = 'subset') { emit('update:modelValue', { ...props.modelValue, entity_types: value, entity_type_scope: scope }) }
function allEntities() { return (props.modelValue.entity_type_scope || (props.modelValue.entity_types?.length ? 'subset' : 'all')) === 'all' }
</script>

<template>
  <div class="parameter-form">
    <template v-for="(spec,name) in schema.properties || {}" :key="name">
      <template v-if="widget(spec)==='hidden'"></template>
      <EntityTypeEditor v-else-if="widget(spec)==='entity-type-editor'" :model-value="modelValue[name] || []" :disabled="disabled" @update:model-value="patch(name,$event)" />
      <section v-else-if="widget(spec)==='entity-type-subset'" class="entity-subset">
        <b>{{ spec.title }}</b>
        <label><input type="checkbox" :checked="allEntities()" :disabled="disabled" @change="subset([], $event.target.checked ? 'all' : 'subset')"> 使用全部已定义类型</label>
        <select v-if="!allEntities() && entityTypes.length" multiple aria-label="实体类型子集" :disabled="disabled" :value="modelValue.entity_types || []" @change="subset([...$event.target.selectedOptions].map(option => option.value))"><option v-for="item in entityTypes" :key="item.code" :value="item.code">{{ item.label }}</option></select>
        <small v-if="!allEntities() && !(modelValue.entity_types || []).length">未选择类型，此节点不抽取实体。</small>
        <small>类型定义与医疗预设在流程的“图谱抽取配置”中维护。</small>
      </section>
      <ServingSelector v-else-if="widget(spec)==='llm-serving-selector'" :model-value="modelValue[name] || ''" :disabled="disabled" @update:model-value="patch(name,$event)" />
      <PromptRevisionSelector v-else-if="widget(spec)==='prompt-template-selector'" :model-value="modelValue[name] || ''" :knowledge-type="modelValue.knowledge_type || ''" :disabled="disabled" @update:model-value="patch(name,$event)" />
      <QualityProfileSelector v-else-if="widget(spec)==='quality-profile-selector'" :model-value="modelValue[name] ?? spec.default ?? ''" :knowledge-type="modelValue.knowledge_type || ''" :disabled="disabled" @update:model-value="patch(name,$event)" />
      <section v-else-if="widget(spec)==='relation-constraints'" class="constraints"><b>{{ spec.title || name }}</b><article v-for="(item,index) in constraints(name)" :key="index"><input :value="item.relation_type" placeholder="关系类型" :disabled="disabled" @input="updateConstraint(name,index,'relation_type',$event.target.value)"><input :value="(item.source_types || []).join(', ')" placeholder="来源实体类型，逗号分隔" :disabled="disabled" @input="updateConstraint(name,index,'source_types',$event.target.value)"><input :value="(item.target_types || []).join(', ')" placeholder="目标实体类型，逗号分隔" :disabled="disabled" @input="updateConstraint(name,index,'target_types',$event.target.value)"><button v-if="!disabled" type="button" @click="removeConstraint(name,index)">删除</button></article><button v-if="!disabled" type="button" @click="addConstraint(name)">＋ 添加关系约束</button></section>
      <label v-else>{{ spec.title || name }}
        <select v-if="spec.enum" :value="modelValue[name] ?? spec.default ?? ''" :disabled="disabled" @change="patch(name,$event.target.value)"><option v-for="choice in spec.enum" :key="choice" :value="choice">{{ choice }}</option></select>
        <input v-else-if="spec.type==='boolean'" type="checkbox" :checked="Boolean(modelValue[name] ?? spec.default)" :disabled="disabled" @change="scalar(name,spec,$event)">
        <input v-else-if="spec.type==='array'" :value="(modelValue[name] || []).join(', ')" placeholder="逗号分隔" :disabled="disabled" @input="tags(name,$event)">
        <input v-else :type="spec.type==='number' || spec.type==='integer' ? 'number' : 'text'" :min="spec.minimum" :max="spec.maximum" :step="spec.type==='number' ? '0.01' : '1'" :value="modelValue[name] ?? spec.default ?? ''" :disabled="disabled" @input="scalar(name,spec,$event)">
        <small v-if="spec.description">{{ spec.description }}</small>
      </label>
    </template>
  </div>
</template>

<style scoped>
.parameter-form{display:grid;gap:12px}.parameter-form>label,.constraints{display:grid;gap:6px;color:#617087;font-size:9px;font-weight:800}.parameter-form small{color:#8190a5;font-weight:500;line-height:1.5}.constraints{padding:10px;border:1px solid #e5eaf1;border-radius:9px}.constraints article{display:grid;gap:6px;padding:8px;border-radius:7px;background:#f7f9fc}.constraints button{justify-self:start}
</style>
