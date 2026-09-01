import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import StandardFlowEditor from './StandardFlowEditor.vue'
import { api } from '../../../api/platform'
import { entityTypeCatalog, managedTemplates, modelServings, qualityProfiles, standardTemplate } from '../__tests__/flowFixtures'

vi.mock('../../../api/platform', () => ({ api: { modelServings: vi.fn(), qualityProfiles: vi.fn(), graphEntityTypes: vi.fn(), resolveGraphEntityTypes: vi.fn(), resolveStandardFlow: vi.fn() } }))
let wrapper
beforeEach(() => {
  api.modelServings.mockResolvedValue(modelServings)
  api.qualityProfiles.mockResolvedValue(qualityProfiles)
  api.graphEntityTypes.mockResolvedValue(entityTypeCatalog)
  api.resolveStandardFlow.mockResolvedValue({ resolved_operators: [
    { node_id: 'input', kind: 'operator', code: 'document-input', display_name_zh: '文档输入', name: 'Document Input', version: 3, source: 'dataforge', catalog_group: 'dataforge', driver: 'builtin', executor: 'dataforge-native', input_ports: {}, output_ports: { output: { artifact_type: 'parsed_document' } }, parameters: {}, locked: true },
    { node_id: 'chunker', kind: 'operator', code: 'document-chunker', display_name_zh: '文档切分', name: 'Document Chunker', version: 1, source: 'dataflow', catalog_group: 'dataflow', driver: 'dataflow', executor: 'dataflow-storage', input_ports: { input: { artifact_type: 'parsed_document' } }, output_ports: { output: { artifact_type: 'candidate_flow_chunk_set' } }, parameters: {}, locked: true },
    { node_id: 'gate', kind: 'execution_gate', code: 'execution-gate', display_name_zh: '自动冻结输入', name: 'Input Snapshot Gate', version: null, source: 'dataforge', catalog_group: 'dataforge', input_ports: { input: { artifact_type: 'candidate_flow_chunk_set' } }, output_ports: { output: { artifact_type: 'flow_chunk_review_snapshot' } }, parameters: {}, locked: true },
    { node_id: 'qa', kind: 'operator', code: 'qa-extractor', display_name_zh: '问答生成器', name: 'QA Extractor', version: 1, source: 'dataforge', catalog_group: 'dataforge', driver: 'builtin', executor: 'dataforge-native', stage_label: '问答生成', uses_llm: true, input_ports: { input: { artifact_type: 'flow_chunk_review_snapshot' } }, output_ports: { output: { artifact_type: 'candidate:qa' } }, parameters: { questions_per_chunk: 1 }, output_key: 'qa', locked: true },
    { node_id: 'sink', kind: 'knowledge_sink', code: 'knowledge-sink', display_name_zh: '知识输出', name: 'Knowledge Sink', version: 1, source: 'dataforge', catalog_group: 'dataforge', input_ports: {}, output_ports: {}, parameters: {}, output_key: 'qa', locked: true },
  ], edges: [{ source: 'input', target: 'chunker' }, { source: 'chunker', target: 'gate' }, { source: 'gate', target: 'qa' }, { source: 'qa', target: 'sink' }], issues: [] })
})
afterEach(() => wrapper?.unmount())

function render(code, extra = {}) {
  const template = standardTemplate(code)
  wrapper = mount(StandardFlowEditor, { props: { template, managedTemplates, outputTypes: template.output_types, ...extra } })
  return flushPromises()
}

describe('Standard business stages follow the managed contract', () => {
  it.each(['standard-graph-triple', 'standard-graph-semantic', 'standard-multi'])('%s renders defaults and persists chip removal', async code => {
    await render(code)
    expect(wrapper.findAll('.entity-chip')).toHaveLength(5)
    expect(wrapper.get('.medical-preset').text()).toBe('＋ 医疗')
    await wrapper.get('[aria-label="删除人物"]').trigger('click')
    const types = wrapper.emitted('update:definition').at(-1)[0].stages.generation.config.entity_types
    expect(types.map(item => item.code)).toEqual(['organization', 'location', 'event', 'concept'])
  })
  it('text has input, mapping and sink without generation or pretend quality', async () => {
    await render('standard-text')
    expect(wrapper.findAll('h3').map(item => item.text())).toEqual(['输入', '文档切分', '自动冻结输入', '文本知识映射', '输出知识'])
    expect(wrapper.findAll('.number').map(item => item.text())).toEqual(['1', '2', '3', '4', '5'])
    expect(wrapper.get('[data-stage="input"]').text()).toContain('已解析文档')
    expect(wrapper.get('[data-stage="chunking"]').text()).toMatch(/FlowChunkSet|切分参数/)
    expect(wrapper.get('[data-stage="input_review"]').text()).toMatch(/Flow 输入快照|系统执行/)
    expect(wrapper.text()).not.toMatch(/知识生成|文本生成|Prompt|模型服务|生成参数/)
    expect(api.modelServings).not.toHaveBeenCalled()
    expect(api.qualityProfiles).not.toHaveBeenCalled()
    expect(wrapper.find('[data-stage="quality"]').exists()).toBe(false)
  })

  it.each(['standard-qa-question', 'standard-qa-full', 'standard-graph-triple', 'standard-graph-semantic', 'standard-multi'])('%s keeps generation controls separate from quality', async code => {
    await render(code)
    const count = code.startsWith('standard-qa-') ? 5 : code === 'standard-multi' ? 7 : 6
    expect(wrapper.findAll('.number').map(item => item.text())).toEqual(Array.from({ length: count }, (_, i) => String(i + 1)))
    const generation = wrapper.get('[data-stage="generation"]')
    expect(generation.text()).toContain('模型服务')
    expect(generation.text()).not.toContain('质量规则')
    expect(wrapper.text()).not.toContain('质量规则')
    expect(wrapper.find('[data-stage="quality"]').exists()).toBe(!code.startsWith('standard-qa-'))
    if (!code.startsWith('standard-qa-')) {
      expect(generation.text()).toContain('实体类型')
      expect(generation.text()).toContain('关系类型')
    }
    await generation.get('select').setValue('model')
    expect(wrapper.emitted('update:definition').at(-1)[0].stages.generation.config.llm_serving).toBe('model')
  })

  it('uses stages rather than output type and supports an unsaved draft', async () => {
    await render('standard-qa-question', { template: null, managedTemplateCode: 'standard-qa-question', outputTypes: ['text'],
      definition: { template_code: 'standard-qa-question', stages: {} } })
    expect(wrapper.findAll('.business-stage')).toHaveLength(5)
    await wrapper.get('.serving-selector select').setValue('model')
    expect(wrapper.emitted('update:definition').at(-1)[0].template_code).toBe('standard-qa-question')
    await wrapper.setProps({ managedTemplateCode: 'standard-text', definition: { template_code: 'standard-text', stages: {} } })
    expect(wrapper.findAll('.business-stage')).toHaveLength(5)
  })

  it('resolves the current Standard config into a readonly technical chain', async () => {
    await render('standard-qa-question')
    await wrapper.findAll('.standard-views button')[1].trigger('click')
    await flushPromises()
    expect(api.resolveStandardFlow).toHaveBeenCalledWith(expect.objectContaining({ managed_template_code: 'standard-qa-question', authoring_mode: 'standard' }))
    expect(wrapper.get('[aria-label="只读技术流程"]').text()).toMatch(/文档输入|文档切分|自动冻结输入|问答生成器|Knowledge Sink/)
    expect(api.resolveStandardFlow.mock.calls.at(-1)[0]).not.toHaveProperty('output_types')
    expect(wrapper.findAll('.operator-step')).toHaveLength(5)
    await wrapper.findAll('.operator-step')[3].trigger('click')
    expect(wrapper.get('[aria-label="算子详情"]').text()).toMatch(/DataForge.*使用 LLM/s)
    expect(wrapper.text()).not.toMatch(/删除算子|替换算子|添加分支/)
  })

  it.each(['standard-qa-question', 'standard-multi'])('%s saves and reloads multiline extraction instructions', async code => {
    await render(code)
    expect(wrapper.get('[data-stage="generation"] .stage-operator').text()).toContain('问答提取器')
    expect(wrapper.get('[data-stage="generation"] .stage-operator').text()).toContain('QA Extractor')
    const requirements = '只提取就诊准备事项\n使用患者口吻，保留原文条件。'
    await wrapper.get('textarea[aria-label="QA 提取要求"]').setValue(requirements)
    const definition = wrapper.emitted('update:definition').at(-1)[0]
    expect(definition.stages.generation.config.extraction_instructions).toBe(requirements)
    await wrapper.setProps({ definition })
    expect(wrapper.get('textarea').element.value).toBe(requirements)
    await wrapper.findAll('.standard-views button')[1].trigger('click')
    await flushPromises()
    expect(api.resolveStandardFlow.mock.calls.at(-1)[0].definition.stages.generation.config.extraction_instructions).toBe(requirements)
  })

  it('defaults Multi to qa-question and updates the visible sink when changed to qa-full', async () => {
    await render('standard-multi')
    const generation = wrapper.get('[data-stage="generation"]')
    const select = generation.findAll('select').find(item => item.element.value === 'qa-question')
    expect(select).toBeTruthy()
    expect(wrapper.get('[data-stage="submit"]').text()).toContain('qa-question')
    await select.setValue('qa-full')
    expect(wrapper.emitted('update:definition').at(-1)[0].stages.generation.config.qa_output_type).toBe('qa-full')
    expect(wrapper.get('[data-stage="submit"]').text()).toContain('qa-full')
    await wrapper.setProps({ definition: wrapper.emitted('update:definition').at(-1)[0], outputTypes: ['text', 'qa-full', 'graph:triple'] })
    expect(generation.findAll('select').find(item => item.element.value === 'qa-full')).toBeTruthy()
  })
})
