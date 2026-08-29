<script setup>
import { computed, ref, watch } from 'vue'
import { operatorPrimaryName, operatorSubtitle } from '../flowModel'
const props = defineProps({ operator: Object })
const tab = ref('overview')
watch(() => props.operator?.code, () => { tab.value = 'overview' })
const exposure = computed(() => ({ canvas: '可直接使用', controlled: '受控使用', internal: '系统内部', disabled: '已禁用' }[props.operator?.exposure] || props.operator?.exposure))
</script>

<template>
  <aside class="operator-inspector" v-if="operator">
    <header><div><small>{{ operator.category }} / {{ operator.subcategory || '通用' }}</small><h3>{{ operatorPrimaryName(operator) }}</h3><p class="operator-bilingual">{{ operatorSubtitle(operator, true) }} · v{{ operator.version }}</p></div><span class="badge blue">{{ exposure }}</span></header>
    <nav><button v-for="item in [['overview','概览'],['contract','业务契约'],['technical','技术契约']]" :key="item[0]" :class="{active:tab===item[0]}" @click="tab=item[0]">{{ item[1] }}</button></nav>
    <div v-if="tab==='overview'" class="inspector-body"><p>{{ operator.summary || operator.description }}</p><h4>适用场景</h4><ul><li v-for="item in operator.scenarios || []" :key="item">{{ item }}</li></ul><h4>适用知识类型</h4><div class="tag-row"><span v-for="item in operator.knowledge_types || []" :key="item" class="badge">{{ item }}</span></div></div>
    <div v-else-if="tab==='contract'" class="inspector-body"><h4>输入</h4><pre>{{ JSON.stringify(operator.input_example || {}, null, 2) }}</pre><h4>输出</h4><pre>{{ JSON.stringify(operator.output_example || {}, null, 2) }}</pre><h4>参数说明</h4><dl><template v-for="(item, key) in operator.parameter_docs || {}" :key="key"><dt>{{ key }}</dt><dd>{{ item }}</dd></template></dl></div>
    <div v-else class="inspector-body"><h4>执行身份</h4><pre>{{ JSON.stringify({ source: operator.source, catalog_group: operator.catalog_group, version: operator.version, executor: operator.executor }, null, 2) }}</pre><h4>Input Ports</h4><pre>{{ JSON.stringify(operator.input_ports || {}, null, 2) }}</pre><h4>Output Ports</h4><pre>{{ JSON.stringify(operator.output_ports || {}, null, 2) }}</pre><h4>Parameter Schema</h4><pre>{{ JSON.stringify(operator.parameter_schema || {}, null, 2) }}</pre></div>
  </aside>
</template>

<style scoped>
.operator-inspector{height:100%;overflow:auto;border:1px solid #dbe3ef;border-radius:12px;background:#fff}.operator-inspector header{display:flex;justify-content:space-between;gap:12px;padding:18px;border-bottom:1px solid #e7ebf1}.operator-inspector h3{margin:4px 0}.operator-inspector small{color:#6b778c}.operator-inspector nav{display:flex;gap:4px;padding:10px;border-bottom:1px solid #edf0f4}.operator-inspector nav button{border:0;background:transparent;color:#68758a}.operator-inspector nav button.active{color:#2f6fed;background:#edf4ff}.inspector-body{padding:16px}.inspector-body p{line-height:1.7;color:#526174}.inspector-body h4{margin:18px 0 8px}.inspector-body pre{max-height:260px;overflow:auto;padding:10px;border-radius:8px;background:#f6f8fb;font-size:11px;white-space:pre-wrap}.tag-row{display:flex;flex-wrap:wrap;gap:6px}.inspector-body dl{display:grid;grid-template-columns:minmax(80px,auto) 1fr;gap:8px}.inspector-body dt{font-weight:700}.inspector-body dd{margin:0;color:#65748a}
</style>

<style scoped>.operator-inspector header>div{min-width:0}.operator-bilingual{overflow-wrap:anywhere;white-space:normal}</style>
