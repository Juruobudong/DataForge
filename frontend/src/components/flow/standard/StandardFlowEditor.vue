<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../../../api/platform'
import CompiledDagPreview from './CompiledDagPreview.vue'

const props = defineProps({
  template: { type: Object, default: null },
  managedTemplates: { type: Array, default: () => [] },
  catalog: { type: Array, default: () => [] },
  outputTypes: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:definition', 'preview'])

const managedTemplate = computed(() =>
  props.managedTemplates.find(item => item.code === props.template?.managed_template_code) || null,
)
const stages = computed(() => managedTemplate.value?.stages || [])
const stageConfig = ref({ schema_version: 1, template_code: '', stages: {} })
const activeStage = ref('generation')
const compiled = ref(null)
const showDag = ref(false)
const loading = ref(false)
const previewError = ref('')
let debounceTimer = null

function syncFromTemplate() {
  const code = props.template?.managed_template_code || ''
  const def = props.template?.definition
  stageConfig.value = {
    schema_version: 1,
    template_code: code,
    stages: def?.template_code === code ? { ...(def.stages || {}) } : {},
  }
  activeStage.value = stages.value.find(stage => stage.configurable)?.code || 'generation'
}

watch(() => props.template?.id, syncFromTemplate, { immediate: true })
watch(() => managedTemplate.value?.code, syncFromTemplate)

function configOf(stageCode) {
  if (!stageConfig.value.stages[stageCode]) stageConfig.value.stages[stageCode] = { config: {} }
  if (!stageConfig.value.stages[stageCode].config) stageConfig.value.stages[stageCode].config = {}
  return stageConfig.value.stages[stageCode].config
}

function emitDefinition() {
  emit('update:definition', JSON.parse(JSON.stringify(stageConfig.value)))
}

function onChange(stageCode, key, value) {
  configOf(stageCode)[key] = value
  emitDefinition()
  schedulePreview()
}

function listValue(items) { return Array.isArray(items) ? items.join(', ') : '' }
function listChange(stageCode, key, raw) {
  const value = raw.split(',').map(item => item.trim()).filter(Boolean)
  onChange(stageCode, key, value)
}

function schedulePreview() {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => preview(), 450)
}

async function preview() {
  if (!managedTemplate.value) return
  loading.value = true; previewError.value = ''
  try {
    const result = await api.previewFlowCompilation({
      authoring_mode: 'standard',
      managed_template_code: managedTemplate.value.code,
      output_types: props.outputTypes,
      definition: stageConfig.value,
    })
    compiled.value = result
    emit('preview', result)
  } catch (e) { previewError.value = e.message }
  finally { loading.value = false }
}

function toggleDag() { showDag.value = !showDag.value; if (showDag.value) preview() }

onBeforeUnmount(() => clearTimeout(debounceTimer))
</script>

<template>
  <div class="standard-editor">
    <div class="standard-grid">
      <div class="stage-pipeline">
        <header class="pane-head"><b>流程阶段</b><span>{{ managedTemplate?.name || '标准流程' }}</span></header>
        <ol class="stage-list">
          <li
            v-for="(stage, index) in stages"
            :key="stage.code"
            :class="{ active: activeStage === stage.code, locked: stage.locked }"
            @click="activeStage = stage.code"
          >
            <span class="stage-index">{{ index + 1 }}</span>
            <div class="stage-body">
              <b>{{ stage.name }}</b>
              <small>{{ stage.locked ? (stage.configurable ? '可配置 · 系统保护' : '系统管理 · 不可删除') : '可替换' }}</small>
            </div>
            <span v-if="stage.locked" class="lock-tag">锁定</span>
          </li>
        </ol>
        <p class="stage-note">阶段顺序由系统控制，用户只配置参数与允许替换的实现。</p>
      </div>

      <div class="stage-inspector">
        <header class="pane-head"><b>阶段配置</b></header>
        <template v-if="managedTemplate">
          <div v-for="stage in stages.filter(item => item.code === activeStage)" :key="stage.code" class="inspector-body">
            <template v-if="stage.configurable && stage.config_schema">
              <label v-for="(spec, key) in stage.config_schema.properties || {}" :key="key" class="field">
                <span>{{ key === 'llm_serving' ? '模型服务' : key === 'entity_types' ? '实体类型' : key === 'relation_types' ? '关系类型' : key }}</span>
                <input
                  v-if="spec.type === 'string'"
                  :value="configOf(stage.code)[key] ?? ''"
                  :placeholder="spec.description || ''"
                  @input="onChange(stage.code, key, $event.target.value)"
                >
                <textarea
                  v-else-if="spec.type === 'array'"
                  :value="listValue(configOf(stage.code)[key])"
                  rows="2"
                  :placeholder="spec.description || '逗号分隔'"
                  @input="listChange(stage.code, key, $event.target.value)"
                ></textarea>
              </label>
            </template>
            <p v-else class="readonly-stage">该阶段由系统管理，无可配置参数。</p>
          </div>
        </template>
        <p v-else class="readonly-stage">请先选择标准模板。</p>
      </div>
    </div>

    <div class="dag-toggle">
      <button :class="{ active: showDag }" @click="toggleDag">{{ showDag ? '隐藏执行 DAG' : '查看执行 DAG' }}</button>
      <span v-if="loading" class="loading-hint">编译中…</span>
      <span v-if="previewError" class="preview-error">{{ previewError }}</span>
    </div>
    <CompiledDagPreview v-if="showDag" :compiled-definition="compiled?.compiled_definition || null" :catalog="catalog" />
  </div>
</template>

<style scoped>
.standard-editor { display: grid; gap: 12px; }
.standard-grid { display: grid; grid-template-columns: minmax(240px, 1fr) minmax(320px, 1.2fr); gap: 12px; }
.stage-pipeline, .stage-inspector { border: 1px solid var(--border, #dfe5ed); border-radius: 11px; background: #ffffff; overflow: hidden; }
.pane-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 14px; border-bottom: 1px solid var(--border, #dfe5ed); }
.pane-head b { color: #34445a; }
.pane-head span { color: #8a97a8; font-size: 11px; }
.stage-list { list-style: none; margin: 0; padding: 8px; display: grid; gap: 6px; }
.stage-list li { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border: 1px solid var(--border, #dfe5ed); border-radius: 9px; cursor: pointer; background: #fafbfd; }
.stage-list li.active { border-color: #b9cff7; background: #eff5ff; }
.stage-index { width: 22px; height: 22px; border-radius: 50%; background: #2f6fed; color: #fff; display: grid; place-items: center; font-size: 11px; font-weight: 800; }
.stage-body { flex: 1; display: grid; gap: 2px; }
.stage-body b { color: #34445a; font-size: 13px; }
.stage-body small { color: #8290a3; font-size: 11px; }
.lock-tag { padding: 2px 7px; border-radius: 999px; background: #eef2f7; color: #66758a; font-size: 10px; }
.stage-note { margin: 8px 12px; color: #8a97a8; font-size: 11px; }
.inspector-body { padding: 12px 14px; display: grid; gap: 12px; }
.field { display: grid; gap: 5px; }
.field span { color: #66758a; font-size: 11px; font-weight: 700; }
.field input, .field textarea { padding: 8px 10px; border: 1px solid var(--border, #dfe5ed); border-radius: 8px; font: inherit; font-size: 12px; }
.readonly-stage { padding: 22px 14px; color: #8290a3; font-size: 12px; text-align: center; }
.dag-toggle { display: flex; align-items: center; gap: 10px; }
.loading-hint { color: #b97917; font-size: 11px; }
.preview-error { color: #c0392b; font-size: 11px; }
</style>
