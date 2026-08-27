// Representative Managed Stage API fixtures shared by the two component suites.
export const entityTypeCatalog = {
  base: [['person', '人物'], ['organization', '组织'], ['location', '地点'], ['event', '事件'], ['concept', '概念']].map(([code, label]) => ({ code, label, description: '', source: 'base' })),
  presets: [{ code: 'medical', label: '医疗', entity_types: [['disease', '疾病'], ['symptom', '症状'], ['drug', '药品'], ['examination', '检查'], ['treatment', '治疗'], ['body_part', '人体部位'], ['department', '科室'], ['medical_indicator', '医学指标']].map(([code, label]) => ({ code, label, description: '', source: 'preset', preset: 'medical' })) }],
}
const llm = { type: 'string', title: '模型服务', 'x-dataforge-ui': { widget: 'llm-serving-selector' } }
const quality = { code: 'quality', name: '质量治理', configurable: true, config_schema: {
  type: 'object', properties: { quality_profile_revision_id: { type: 'string', title: '质量规则', default: 'qualityrev_default', 'x-dataforge-ui': { widget: 'quality-profile-selector' } } },
} }
export const managedTemplates = [
  ['standard-text', '文本知识', ['text']],
  ['standard-qa', '问答知识', ['qa']],
  ['standard-graph-triple', '三元组图谱', ['graph:triple']],
  ['standard-graph-semantic', '语义图谱', ['graph:semantic']],
  ['standard-multi', '多产出知识', ['text', 'qa', 'graph']],
].map(([code, name, output_types]) => ({
  code, name, output_types,
  default_definition: { schema_version: 1, template_code: code, stages: code.startsWith('standard-graph-') ? { generation: { config: { entity_types: entityTypeCatalog.base } } } : {} },
  stages: [
    { code: 'input', configurable: false },
    ...(code === 'standard-text' || code === 'standard-multi' ? [{ code: 'mapping', configurable: false }] : []),
    ...(code === 'standard-text' ? [] : [{ code: 'generation', name: code === 'standard-qa' ? '问答生成' : '实体关系抽取', configurable: true,
      config_schema: { type: 'object', properties: { llm_serving: llm, ...(code === 'standard-qa' ? {} : {
        entity_types: { type: 'array', title: '实体类型', 'x-dataforge-ui': { widget: 'entity-type-editor' } }, relation_types: { type: 'array', title: '关系类型' },
      }) } },
    }]),
    quality, { code: 'binding', configurable: false }, { code: 'submit', configurable: false },
  ],
}))

export function standardTemplate(code = 'standard-text') {
  const managed = managedTemplates.find(item => item.code === code)
  return { id: code, code, name: managed.name, is_builtin: true, revision: 1,
    authoring_mode: 'standard', managed_template_code: code, output_types: managed.output_types,
    definition: JSON.parse(JSON.stringify(managed.default_definition)) }
}

export const qualityProfiles = [{ name: '默认质量', revisions: [{ id: 'qualityrev_default', revision: 1 }] }]
export const modelServings = [{ id: 'model', name: '测试模型', serving_code: 'model', is_enabled: true, is_default: true, last_check_status: 'healthy' }]
