<script setup>
const props = defineProps({ modelValue: { type: Array, default: () => [] }, evaluationNodes: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const fields = { text: '正文', question: '问题', answer: '答案', length: '正文长度', question_quality: '问题质量分', answer_alignment: '答案一致性分', answer_verifiability: '答案可核验性分', downstream_value: '下游价值分' }
const operations = { eq: '等于', ne: '不等于', gt: '大于', ge: '大于等于', lt: '小于', le: '小于等于', contains: '包含', in: '属于集合', is_empty: '为空', not_empty: '不为空' }
const scored = field => ['question_quality', 'answer_alignment', 'answer_verifiability', 'downstream_value'].includes(field)
const numeric = field => field === 'length' || scored(field)
function patch(index, change) { emit('update:modelValue', props.modelValue.map((rule, i) => i === index ? { ...rule, ...change } : rule)) }
function field(index, value) {
  const rule = { field: value, operator: numeric(value) ? 'ge' : 'contains', value: numeric(value) ? 0 : '' }
  if (scored(value)) { rule.value = 4; rule.evaluation_node = props.evaluationNodes[0]?.id || '' }
  emit('update:modelValue', props.modelValue.map((item, i) => i === index ? rule : item))
}
function value(index, rule, raw) {
  const convert = item => numeric(rule.field) ? Number(item) : item
  patch(index, { value: rule.operator === 'in' ? raw.split(',').map(item => convert(item.trim())) : convert(raw) })
}
</script>

<template>
  <section class="filter-rules" aria-label="保留条件"><b>保留条件 · 全部满足</b>
    <article v-for="(rule,index) in modelValue" :key="index">
      <select :aria-label="`条件${index+1}字段`" :value="rule.field" :disabled="disabled" @change="field(index,$event.target.value)"><option v-for="(label,key) in fields" :key="key" :value="key">{{ label }}</option></select>
      <select v-if="scored(rule.field)" :aria-label="`条件${index+1}评分节点`" :value="rule.evaluation_node" :disabled="disabled" @change="patch(index,{evaluation_node:$event.target.value})"><option value="">选择上游评估节点</option><option v-for="node in evaluationNodes" :key="node.id" :value="node.id">{{ node.label || node.id }}</option><option v-if="rule.evaluation_node && !evaluationNodes.some(node => node.id === rule.evaluation_node)" :value="rule.evaluation_node">{{ rule.evaluation_node }}（上游不可用）</option></select>
      <select :aria-label="`条件${index+1}比较方式`" :value="rule.operator" :disabled="disabled" @change="patch(index,{operator:$event.target.value})"><option v-for="(label,key) in operations" :key="key" :value="key">{{ label }}</option></select>
      <input v-if="!['is_empty','not_empty'].includes(rule.operator)" :aria-label="`条件${index+1}比较值`" :type="numeric(rule.field) && rule.operator !== 'in' ? 'number' : 'text'" :value="Array.isArray(rule.value) ? rule.value.join(', ') : rule.value" :placeholder="rule.operator === 'in' ? '逗号分隔的值' : '比较值'" :disabled="disabled" @input="value(index,rule,$event.target.value)">
      <button v-if="!disabled" type="button" @click="emit('update:modelValue',modelValue.filter((_,i)=>i!==index))">删除条件</button>
    </article>
    <button v-if="!disabled" type="button" @click="emit('update:modelValue',[...modelValue,{field:'length',operator:'ge',value:1}])">＋ 添加条件</button>
    <small>仅支持业务字段和上游评分，不接受代码。正文改变后需重新评分。</small>
  </section>
</template>

<style scoped>.filter-rules,.filter-rules article{display:grid;gap:8px}.filter-rules article{padding:10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px}.filter-rules button{justify-self:start}.filter-rules small{font-size:12px;line-height:1.6;color:#718096}</style>
