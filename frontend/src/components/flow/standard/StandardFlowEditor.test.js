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
    { node_id: 'input', kind: 'operator', code: 'reviewed-source-chunk-input', display_name_zh: '已审核来源切片', name: 'Reviewed SourceChunk Input', version: 3, provider: 'dataforge', input_ports: {}, output_ports: { output: { artifact_type: 'source_chunk_set' } }, parameters: {}, locked: true },
    { node_id: 'qa', kind: 'operator', code: 'qa-generator', display_name_zh: '问答生成器', name: 'QA Generator', version: 5, provider: 'dataflow', stage_label: '问答生成', uses_llm: true, input_ports: { input: { artifact_type: 'source_chunk_set' } }, output_ports: { output: { artifact_type: 'candidate:qa' } }, parameters: { questions_per_chunk: 1 }, output_key: 'qa', locked: true },
    { node_id: 'sink', kind: 'knowledge_sink', code: 'knowledge-sink', display_name_zh: '知识输出', name: 'Knowledge Sink', version: 1, provider: 'dataforge', input_ports: {}, output_ports: {}, parameters: {}, output_key: 'qa', locked: true },
  ], edges: [{ source: 'input', target: 'qa' }, { source: 'qa', target: 'sink' }], issues: [] })
})
afterEach(() => wrapper?.unmount())

function render(code, extra = {}) {
  const template = standardTemplate(code)
  wrapper = mount(StandardFlowEditor, { props: { template, managedTemplates, outputTypes: template.output_types, ...extra } })
  return flushPromises()
}

describe('Standard business stages follow the managed contract', () => {
  it.each(['standard-graph-triple', 'standard-graph-semantic'])('%s renders defaults and persists chip removal', async code => {
    await render(code)
    expect(wrapper.findAll('.entity-chip')).toHaveLength(5)
    expect(wrapper.get('.medical-preset').text()).toBe('＋ 医疗')
    await wrapper.get('[aria-label="删除人物"]').trigger('click')
    const types = wrapper.emitted('update:definition').at(-1)[0].stages.generation.config.entity_types
    expect(types.map(item => item.code)).toEqual(['organization', 'location', 'event', 'concept'])
  })
  it('text has three stages, no generation UI or model request, and editable quality', async () => {
    await render('standard-text')
    expect(wrapper.findAll('h3').map(item => item.text())).toEqual(['输入', '质量治理', '输出知识'])
    expect(wrapper.findAll('.number').map(item => item.text())).toEqual(['1', '2', '3'])
    expect(wrapper.text()).not.toMatch(/知识生成|文本生成|Prompt|模型服务|生成参数/)
    expect(api.modelServings).not.toHaveBeenCalled()
    const quality = wrapper.findAll('.business-stage')[1]
    expect(quality.get('select').element.value).toBe('qualityrev_default')
    await quality.get('select').setValue('qualityrev_default')
    expect(wrapper.emitted('update:definition').at(-1)[0].stages.quality.config).toEqual({ quality_profile_revision_id: 'qualityrev_default' })
  })

  it.each(['standard-qa', 'standard-graph-triple', 'standard-graph-semantic', 'standard-multi'])('%s keeps generation controls separate from quality', async code => {
    await render(code)
    expect(wrapper.findAll('.number').map(item => item.text())).toEqual(['1', '2', '3', '4'])
    const generation = wrapper.findAll('.business-stage')[1]
    expect(generation.text()).toContain('模型服务')
    expect(generation.text()).not.toContain('质量规则')
    expect(wrapper.findAll('.business-stage')[2].text()).toContain('质量规则')
    if (code !== 'standard-qa') {
      expect(generation.text()).toContain('实体类型')
      expect(generation.text()).toContain('关系类型')
    }
    await generation.get('select').setValue('model')
    expect(wrapper.emitted('update:definition').at(-1)[0].stages.generation.config.llm_serving).toBe('model')
  })

  it('uses stages rather than output type and supports an unsaved draft', async () => {
    await render('standard-qa', { template: null, managedTemplateCode: 'standard-qa', outputTypes: ['text'],
      definition: { template_code: 'standard-qa', stages: {} } })
    expect(wrapper.findAll('.business-stage')).toHaveLength(4)
    await wrapper.get('.serving-selector select').setValue('model')
    expect(wrapper.emitted('update:definition').at(-1)[0].template_code).toBe('standard-qa')
    await wrapper.setProps({ managedTemplateCode: 'standard-text', definition: { template_code: 'standard-text', stages: {} } })
    expect(wrapper.findAll('.business-stage')).toHaveLength(3)
  })

  it('resolves the current Standard config into a readonly technical chain', async () => {
    await render('standard-qa')
    await wrapper.findAll('.standard-views button')[1].trigger('click')
    await flushPromises()
    expect(api.resolveStandardFlow).toHaveBeenCalledWith(expect.objectContaining({ managed_template_code: 'standard-qa', authoring_mode: 'standard' }))
    expect(wrapper.get('[aria-label="只读技术流程"]').text()).toMatch(/已审核来源切片|问答生成器|Knowledge Sink/)
    expect(wrapper.findAll('.operator-step')).toHaveLength(3)
    await wrapper.findAll('.operator-step')[1].trigger('click')
    expect(wrapper.get('[aria-label="算子详情"]').text()).toMatch(/DataFlow.*使用 LLM/s)
    expect(wrapper.text()).not.toMatch(/删除算子|替换算子|添加分支/)
  })
})
