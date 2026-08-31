import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import EntityTypeEditor from './EntityTypeEditor.vue'
import PromptPreview from './PromptPreview.vue'
import AdvancedFlowEditor from '../flow/advanced/AdvancedFlowEditor.vue'
import OperatorParameterForm from '../flow/inspector/OperatorParameterForm.vue'
import NodeInspector from '../flow/inspector/NodeInspector.vue'
import { entityTypeCatalog } from '../flow/__tests__/flowFixtures'
import { api } from '../../api/platform'

vi.mock('../../api/platform', () => ({ api: { graphEntityTypes: vi.fn(), resolveGraphEntityTypes: vi.fn(), previewGraphPrompt: vi.fn() } }))
const clone = value => JSON.parse(JSON.stringify(value))
const originalScroll = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView')
let wrapper, scroll
const button = text => wrapper.findAll('button').find(item => item.text() === text)
const graph = () => ({
  schema_version: 3, graph_config: { entity_types: clone(entityTypeCatalog.base), relation_types: [] },
  nodes: [{ id: 'entity-1', kind: 'operator', ref: 'entity-extractor', operator_version: 7, params: { extraction_instructions: '实体要求' } },
    { id: 'relation-1', kind: 'operator', ref: 'relation-extractor', operator_version: 7, params: { extraction_instructions: '关系要求' } }],
  edges: [],
})
const preview = (body, text = '实际提示词') => ({ node_id: body.node_id, operator_version: 7, system: '系统规则', user: text, will_call_model: true, notice: '不调用模型', placeholders: { source_chunk: '运行时原文' } })

beforeEach(() => {
  vi.useFakeTimers()
  scroll = vi.fn()
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: scroll })
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })))
  api.graphEntityTypes.mockResolvedValue(clone(entityTypeCatalog))
  api.previewGraphPrompt.mockImplementation(async body => preview(body))
  api.resolveGraphEntityTypes.mockImplementation(async body => ({ entity_types: body.entity_types.map(item => item.code === body.code
    ? { code: item.code, label: body.label.trim(), description: body.description.trim(), source: 'custom' } : item) }))
})
afterEach(() => {
  wrapper?.unmount(); wrapper = null
  vi.useRealTimers(); vi.unstubAllGlobals()
  if (originalScroll) Object.defineProperty(Element.prototype, 'scrollIntoView', originalScroll)
  else delete Element.prototype.scrollIntoView
})
async function settlePreview() { await nextTick(); await vi.advanceTimersByTimeAsync(220); await flushPromises() }

it('offers joint extraction in the existing prompt preview without upstream entity input', async () => {
  wrapper = mount(PromptPreview, { props: { definition: { nodes: [
    { id: 'joint', kind: 'operator', ref: 'entity-relation-extractor', params: {
      entity_extraction_instructions: '设备和模块', relation_extraction_instructions: '组成关系' } },
  ], edges: [] } } })
  await settlePreview()
  expect(wrapper.get('option').text()).toContain('实体关系联合抽取器')
  const request = api.previewGraphPrompt.mock.calls.at(-1)[0]
  expect(request.node_id).toBe('joint')
  expect(request.definition.nodes[0].params.relation_extraction_instructions).toBe('组成关系')
})

it('edits an entity atomically, preserves code, cancels, and protects renamed presets', async () => {
  wrapper = mount(EntityTypeEditor, { props: { modelValue: clone(entityTypeCatalog.presets[0].entity_types),
    'onUpdate:modelValue': value => wrapper.setProps({ modelValue: value }) } })
  await flushPromises()
  await wrapper.get('[aria-label="编辑疾病"]').trigger('click')
  await wrapper.get('[aria-label="编辑实体名称"]').setValue('明确诊断')
  await wrapper.setProps({ modelValue: clone(wrapper.props('modelValue')) })
  expect(wrapper.get('[aria-label="编辑实体名称"]').element.value).toBe('明确诊断')
  await button('取消').trigger('click')
  expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  await wrapper.get('[aria-label="编辑疾病"]').trigger('click')
  await wrapper.get('[aria-label="编辑实体名称"]').setValue('明确诊断')
  await wrapper.get('[aria-label="实体抽取说明"]').setValue('只抽明确诊断')
  await button('应用').trigger('click'); await flushPromises()
  expect(wrapper.props('modelValue')[0]).toEqual({ code: 'disease', label: '明确诊断', description: '只抽明确诊断', source: 'custom' })
  expect(wrapper.get('[role="status"]').text()).toContain('不随医疗预设移除')
  expect(wrapper.get('.medical-preset').text()).toBe('－ 医疗')
  expect(wrapper.emitted('update:modelValue')).toHaveLength(1)
  await wrapper.get('[aria-label="编辑明确诊断"]').trigger('click')
  await wrapper.get('[aria-label="编辑实体名称"]').setValue('药品')
  await button('应用').trigger('click')
  expect(wrapper.get('[role="alert"]').text()).toContain('已存在')
  await wrapper.get('[aria-label="编辑实体名称"]').setValue('　')
  await button('应用').trigger('click')
  expect(wrapper.get('[role="alert"]').text()).toContain('不能为空')
})

it('keeps entity data unchanged on failed or stale edits', async () => {
  wrapper = mount(EntityTypeEditor, { props: { modelValue: clone(entityTypeCatalog.base) } })
  await flushPromises()
  await wrapper.get('[aria-label="编辑人物"]').trigger('click')
  await wrapper.get('[aria-label="编辑实体名称"]').setValue('参与人')
  api.resolveGraphEntityTypes.mockRejectedValueOnce(new Error('保存解析失败'))
  await button('应用').trigger('click'); await flushPromises()
  expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  expect(wrapper.get('[role="alert"]').text()).toContain('保存解析失败')
  let finish
  api.resolveGraphEntityTypes.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await button('应用').trigger('click')
  await wrapper.setProps({ modelValue: [] })
  finish({ entity_types: clone(entityTypeCatalog.base) }); await flushPromises()
  expect(wrapper.emitted('update:modelValue')).toBeUndefined()
})

it('renders backend prompts, switches nodes, clears old results and ignores late responses', async () => {
  let finish
  api.previewGraphPrompt.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  wrapper = mount(PromptPreview, { props: { definition: graph() } })
  await settlePreview()
  expect(api.previewGraphPrompt.mock.calls[0][0].node_id).toBe('entity-1')
  await wrapper.get('select').setValue('relation-1'); await settlePreview()
  expect(wrapper.text()).toContain('实际提示词')
  finish(preview({ node_id: 'entity-1' }, '过期实体提示词')); await flushPromises()
  expect(wrapper.text()).not.toContain('过期实体提示词')
  api.previewGraphPrompt.mockRejectedValueOnce(new Error('配置未通过校验'))
  const changed = graph(); changed.nodes[1].params.extraction_instructions = '新要求'
  await wrapper.setProps({ definition: changed })
  expect(wrapper.text()).not.toContain('实际提示词')
  await settlePreview()
  expect(wrapper.get('[role="alert"]').text()).toContain('配置未通过校验')
  await button('重试预览').trigger('click'); await settlePreview()
  expect(wrapper.text()).toContain('实际提示词')
  expect(api.previewGraphPrompt.mock.calls.at(-1)[0].definition.nodes[1].params.extraction_instructions).toBe('新要求')
})

it('shows empty-subset no-model notice and cancels work on unmount', async () => {
  api.previewGraphPrompt.mockResolvedValueOnce({ operator_version: 7, will_call_model: false, notice: '未选择实体类型，此节点不调用模型。' })
  wrapper = mount(PromptPreview, { props: { definition: graph() } })
  await settlePreview()
  expect(wrapper.text()).toContain('不调用模型')
  expect(wrapper.findAll("pre")).toHaveLength(0)
  await wrapper.setProps({ selectedNodeId: "relation-1" })
  wrapper.unmount(); wrapper = null
  await vi.advanceTimersByTimeAsync(300)
  expect(api.previewGraphPrompt).toHaveBeenCalledTimes(1)
})

it('opens and scrolls repeatedly without dirtying, navigates from nodes and collapses to the button', async () => {
  wrapper = mount(AdvancedFlowEditor, { attachTo: document.body, props: { outputTypes: ['graph:triple'] }, global: { stubs: {
    DataForgeFlowCanvas: { template: '<div />', methods: { fit() {} } }, OperatorPalette: true, NodeInspector: true,
  } } })
  wrapper.vm.loadDefinition(graph()); await nextTick()
  await button('图谱抽取配置').trigger('click'); await settlePreview()
  expect(wrapper.get('[aria-label="全流程图谱规则"]').exists()).toBe(true)
  expect(document.activeElement.getAttribute('aria-label')).toBe('全流程图谱规则')
  expect(scroll).toHaveBeenLastCalledWith({ block: 'start', behavior: 'smooth' })
  await button('图谱抽取配置').trigger('click')
  expect(wrapper.get('[aria-label="全流程图谱规则"]').exists()).toBe(true)
  expect(wrapper.emitted('dirty')).toBeUndefined()
  wrapper.findComponent(NodeInspector).vm.$emit('open-graph-config', { part: 'relations' }); await nextTick()
  expect(document.activeElement.getAttribute('aria-label')).toBe('关系类型规则')
  wrapper.findComponent(NodeInspector).vm.$emit('open-graph-config', { part: 'prompt', nodeId: 'relation-1' }); await settlePreview()
  expect(wrapper.get('[aria-label="提示词预览节点"]').element.value).toBe('relation-1')
  await wrapper.get('[aria-label="提示词预览节点"]').setValue('entity-1')
  wrapper.findComponent(NodeInspector).vm.$emit('open-graph-config', { part: 'prompt', nodeId: 'relation-1' }); await settlePreview()
  expect(wrapper.get('[aria-label="提示词预览节点"]').element.value).toBe('relation-1')
  await button('返回画布').trigger('click')
  expect(wrapper.find('[aria-label="全流程图谱规则"]').exists()).toBe(true)
  expect(document.activeElement.textContent).toBe('图谱抽取配置')
  await button('收起').trigger('click')
  expect(wrapper.find('[aria-label="全流程图谱规则"]').exists()).toBe(false)
  expect(document.activeElement.textContent).toBe('图谱抽取配置')
  expect(wrapper.emitted('dirty')).toBeUndefined()
  window.matchMedia.mockReturnValue({ matches: true })
  await button('图谱抽取配置').trigger('click')
  expect(scroll).toHaveBeenLastCalledWith({ block: 'start', behavior: 'instant' })
})

it('serializes renamed types and restores their names and provenance with undo', async () => {
  wrapper = mount(AdvancedFlowEditor, { props: { outputTypes: ['graph:triple'] }, global: { stubs: {
    DataForgeFlowCanvas: { template: '<div />', methods: { fit() {} } }, OperatorPalette: true, NodeInspector: true,
  } } })
  const value = graph()
  value.nodes[0].params.entity_types = ['person']
  value.graph_config.relation_types = [{ code: 'related', label: '关联', source_types: ['person'], target_types: ['concept'] }]
  wrapper.vm.loadDefinition(value); await nextTick()
  await button('图谱抽取配置').trigger('click'); await flushPromises()
  await wrapper.get('[aria-label="编辑人物"]').trigger('click')
  await wrapper.get('[aria-label="编辑实体名称"]').setValue('参与人')
  await wrapper.get('[aria-label="实体抽取说明"]').setValue('')
  await button('应用').trigger('click'); await flushPromises()
  let saved = wrapper.vm.serialize()
  expect(saved.graph_config.entity_types[0]).toMatchObject({ code: 'person', label: '参与人', source: 'custom' })
  expect(saved.nodes[0].params.entity_types).toEqual(['person'])
  expect(saved.graph_config.relation_types[0].source_types).toEqual(['person'])
  await button('↶ 撤销').trigger('click')
  saved = wrapper.vm.serialize()
  expect(saved.graph_config.entity_types[0]).toMatchObject({ code: 'person', label: '人物', source: 'base' })
  await button('↷ 重做').trigger('click')
  expect(wrapper.vm.serialize().graph_config.entity_types[0].label).toBe('参与人')
})

it('edits real parameter field and restores the system default', async () => {
  wrapper = mount(OperatorParameterForm, { props: {
    schema: { properties: { extraction_instructions: { type: 'string', title: '实体抽取要求', 'x-dataforge-ui': { widget: 'extraction-instructions' } } } },
    modelValue: { extraction_instructions: '' }, 'onUpdate:modelValue': value => wrapper.setProps({ modelValue: value }),
  } })
  await wrapper.get('[aria-label="实体抽取要求"]').setValue('只抽明确诊断')
  expect(wrapper.props('modelValue').extraction_instructions).toBe('只抽明确诊断')
  await button('恢复默认').trigger('click')
  expect(wrapper.props('modelValue').extraction_instructions).toBe('')
})
