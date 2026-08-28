<script setup>
import { computed, ref, watch } from 'vue'
import OperatorParameterForm from '../inspector/OperatorParameterForm.vue'
import StandardTechnicalFlow from './StandardTechnicalFlow.vue'
import { runtimeArtifactLabel, operatorPrimaryName, operatorSubtitle } from '../flowModel'
import { api } from '../../../api/platform'

const props = defineProps({ template: { type: Object, default: null }, managedTemplateCode: { type: String, default: '' }, definition: { type: Object, default: null }, managedTemplates: { type: Array, default: () => [] }, outputTypes: { type: Array, default: () => [] } })
const emit = defineEmits(['update:definition'])
const managedCode = computed(() => props.managedTemplateCode || props.template?.managed_template_code || '')
const managedTemplate = computed(() => props.managedTemplates.find(item => item.code === managedCode.value) || null)
const stages = computed(() => managedTemplate.value?.stages || [])
const stageConfig = ref({ schema_version: 1, template_code: '', stages: {} })
const view = ref('business'), technical = ref(null), loading = ref(false), resolveError = ref('')
let resolveSequence = 0
async function resolveTechnical() {
  if (view.value !== 'technical') return
  const sequence = ++resolveSequence
  loading.value = true; technical.value = null; resolveError.value = ''
  try {
    const result = await api.resolveStandardFlow({ authoring_mode: 'standard', managed_template_code: managedCode.value, definition: stageConfig.value })
    if (sequence === resolveSequence) technical.value = result
  } catch (error) { if (sequence === resolveSequence) resolveError.value = error.message }
  finally { if (sequence === resolveSequence) loading.value = false }
}
watch([view, stageConfig, managedCode], resolveTechnical, { deep: true })

function syncFromTemplate() {
  const code = managedCode.value
  const def = props.definition || props.template?.definition || managedTemplate.value?.default_definition
  stageConfig.value = { schema_version: 1, template_code: code, stages: def?.template_code === code ? JSON.parse(JSON.stringify(def.stages || {})) : {} }
}
watch([managedCode, () => props.template?.id, () => props.definition || props.template?.definition], syncFromTemplate, { immediate: true })
function configOf(stageCode) {
  if (!stageConfig.value.stages[stageCode]) stageConfig.value.stages[stageCode] = { config: {} }
  if (!stageConfig.value.stages[stageCode].config) stageConfig.value.stages[stageCode].config = {}
  return stageConfig.value.stages[stageCode].config
}
function updateStage(stageCode, value) { stageConfig.value.stages[stageCode] = { config: value }; emit('update:definition', JSON.parse(JSON.stringify(stageConfig.value))) }
</script>

<template>
  <div class="standard-editor">
    <nav class="standard-views" aria-label="流程视图"><button :aria-pressed="view === 'business'" @click="view = 'business'">业务流程</button><button :aria-pressed="view === 'technical'" @click="view = 'technical'">技术流程</button></nav>
    <template v-if="view === 'technical'"><p v-if="loading" role="status">正在解析实际算子…</p><p v-else-if="resolveError" role="alert">{{ resolveError }} <button @click="resolveTechnical">重试</button></p><StandardTechnicalFlow v-else-if="technical" :value="technical" /></template>
    <template v-else>
    <template v-for="(stage, index) in stages" :key="stage.code">
      <span v-if="index" class="arrow">↓</span>
      <article class="business-stage" :data-stage="stage.code"><span class="number">{{ index + 1 }}</span><div class="stage-content">
        <template v-if="stage.code === 'input'"><h3>输入</h3><b>已审核文档块</b><p>正式运行由 SourceReviewSnapshot 自动注入；开发预览默认使用 DataForge 内置示例审核数据。</p><span class="badge blue">运行时绑定</span></template>
        <template v-else-if="stage.code === 'mapping'"><h3>文本知识映射</h3><p>审核正文直接映射为文本知识，保留原文和来源，不调用模型。</p></template>
        <template v-else-if="stage.code === 'generation'">
          <h3>知识生成</h3>
          <p v-for="operator in (stage.operators || []).filter(item => item.provider === 'dataflow')" :key="operator.node_id" class="stage-operator"><b>{{ operatorPrimaryName(operator) }}</b><small>{{ operatorSubtitle(operator) }}</small></p>
          <div v-if="stage.configurable && stage.config_schema" class="config-sections"><section><h4>{{ stage.name }}</h4><OperatorParameterForm :key="`${managedCode}:${template?.id || 'new'}`" :schema="stage.config_schema" :model-value="configOf(stage.code)" @update:model-value="updateStage(stage.code,$event)" /></section></div>
          <small v-if="managedCode === 'standard-qa' || managedCode === 'standard-multi'">QA 提取要求用于两阶段问答生成；输出格式和来源由系统维护。没有匹配内容时产出零条，重跑会按 Diff 撤销该切片旧问答。要求随流程版本冻结。</small>
          <small v-else>图谱 Prompt 由系统根据目标和 Schema 生成。</small>
        </template>
        <template v-else-if="stage.code === 'quality'"><h3>图谱校验</h3><div class="checks"><span>✓ Graph Schema</span><span>✓ Graph Quality</span></div><p>仅校验图谱分支的实体、关系和 Evidence；硬失败阻止该分支提交。</p></template>
        <template v-else-if="stage.code === 'submit'"><h3>输出知识</h3><div class="outputs"><section v-for="item in managedTemplate?.output_types || []" :key="item"><b>{{ runtimeArtifactLabel(`candidate:${item}`) }}</b><span>Knowledge Sink · 正式知识提交</span></section></div><p>Sink 统一保证 Schema、审核血缘、来源绑定、Diff 和事务提交。输出类型由固定模板维护，不绑定具体 KnowledgeLibrary。</p></template>
      </div></article>
    </template>
    </template>
  </div>
</template>

<style scoped>
.standard-views{display:flex;gap:8px}.standard-views button[aria-pressed="true"]{background:#2f6fed;color:white;border-color:#2f6fed}
.standard-editor{display:grid;justify-items:stretch;gap:8px;max-width:900px;margin:0 auto}.business-stage{display:grid;grid-template-columns:42px 1fr;gap:13px;padding:17px;border:1px solid #dfe5ed;border-radius:13px;background:#fff}.number{display:grid;width:36px;height:36px;place-items:center;border-radius:50%;background:#2f6fed;color:#fff;font-weight:900}.stage-content h3{margin:0 0 10px;color:#2f4058}.stage-content p{margin:6px 0;color:#647287;line-height:1.6}.stage-content small{display:block;margin-top:10px;color:#7b8798}.arrow{text-align:center;color:#7c8ca3;font-size:20px}.config-sections{display:grid;gap:12px}.config-sections section{display:grid;gap:8px;padding:12px;border:1px solid #e5e9ef;border-radius:10px;background:#fafbfd}.config-sections h4{margin:0}.config-sections label{display:grid;grid-template-columns:150px 1fr;gap:10px;align-items:center}.checks{display:flex;flex-wrap:wrap;gap:8px}.checks span{padding:8px 11px;border-radius:8px;background:#edf8f3;color:#207a5c;font-weight:700}.outputs{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}.outputs section{display:grid;gap:4px;padding:11px;border:1px solid #dfe8f5;border-radius:9px;background:#f7faff}.outputs span{color:#6f7d91;font-size:11px}
</style>

<style scoped>.stage-operator{overflow-wrap:anywhere;white-space:normal}</style>
