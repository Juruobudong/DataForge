// Representative Managed Stage API fixtures shared by the two component suites.
export const dataflowOperators = [
  ['Text2QAGenerator', '文本转问答生成器', 6],
  ['PromptedRefiner', '提示词修订器', 4],
  ['HashDeduplicateFilter', '哈希去重过滤器', 4],
  ['MinHashDeduplicateFilter', 'MinHash 相似去重过滤器', 4],
].map(([code, display_name_zh, version]) => ({ code, name: code, display_name_zh, version, provider: 'dataflow',
  id: code, node_id: code, kind: 'operator', surfaces: ['advanced-canvas'], category: '知识生成', exposure: 'public', dependency_status: { status: 'ready' } }))
export const nativeQaOperator = { code: 'qa-extractor', name: 'QA Extractor', display_name_zh: '问答提取器', version: 1, provider: 'dataforge', kind: 'operator', surfaces: ['standard-template', 'advanced-canvas'], dependency_status: { status: 'ready' } }
export const entityTypeCatalog = {
  base: [['person', '人物'], ['organization', '组织'], ['location', '地点'], ['event', '事件'], ['concept', '概念']].map(([code, label]) => ({ code, label, description: '', source: 'base' })),
  presets: [{ code: 'medical', label: '医疗', entity_types: [['disease', '疾病'], ['symptom', '症状'], ['drug', '药品'], ['examination', '检查'], ['treatment', '治疗'], ['body_part', '人体部位'], ['department', '科室'], ['medical_indicator', '医学指标']].map(([code, label]) => ({ code, label, description: '', source: 'preset', preset: 'medical' })) }],
}
const llm = { type: 'string', title: '模型服务', 'x-dataforge-ui': { widget: 'llm-serving-selector' } }
const quality = { code: 'quality', name: '图谱校验', configurable: false }
const extraction = { type: 'string', title: 'QA 提取要求', default: '仅基于审核通过的原文生成问答。问题应清晰、具体且可独立理解，避免依赖上下文的模糊指代。答案必须能够从原文直接得到或由原文明确归纳，不使用外部知识，不猜测或补充原文未提供的信息。优先提取定义、事实、条件、步骤、规则、结论和数值等具有明确答案的知识。答案保持原文语言，简洁但信息完整；文本不足以形成可靠问答时不生成。', 'x-dataforge-ui': { widget: 'textarea' } }
export const managedTemplates = [
  ['standard-text', '文本知识', ['text']],
  ['standard-qa', '问答知识', ['qa']],
  ['standard-graph-triple', '三元组图谱', ['graph:triple']],
  ['standard-graph-semantic', '语义图谱', ['graph:semantic']],
  ['standard-multi', '多产出知识', ['text', 'qa', 'graph:triple']],
].map(([code, name, output_types]) => ({
  code, name, output_types,
  default_definition: { schema_version: 1, template_code: code, stages: code.startsWith('standard-graph-') || code === 'standard-multi' ? { generation: { config: { entity_types: entityTypeCatalog.base } } } : {} },
  stages: [
    { code: 'input', configurable: false },
    ...(code === 'standard-text' || code === 'standard-multi' ? [{ code: 'mapping', configurable: false }] : []),
    ...(code === 'standard-text' ? [] : [{ code: 'generation', name: code === 'standard-qa' ? '问答生成' : '实体关系抽取', configurable: true,
      operators: ['standard-qa', 'standard-multi'].includes(code) ? [nativeQaOperator] : [],
      config_schema: { type: 'object', properties: { llm_serving: llm, ...(['standard-qa', 'standard-multi'].includes(code) ? { extraction_instructions: extraction } : {}), ...(code === 'standard-qa' ? {} : {
        entity_types: { type: 'array', title: '实体类型', 'x-dataforge-ui': { widget: 'entity-type-editor' } }, relation_types: { type: 'array', title: '关系类型' },
      }) } },
    }]),
    ...(output_types.some(value => value.startsWith('graph:')) ? [quality] : []), { code: 'submit', configurable: false },
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
