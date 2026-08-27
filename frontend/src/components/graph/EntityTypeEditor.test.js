import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import EntityTypeEditor from './EntityTypeEditor.vue'
import AdvancedFlowEditor from '../flow/advanced/AdvancedFlowEditor.vue'
import { entityTypeCatalog } from '../flow/__tests__/flowFixtures'
import { api } from '../../api/platform'

vi.mock('../../api/platform', () => ({ api: { graphEntityTypes: vi.fn(), resolveGraphEntityTypes: vi.fn() } }))
let wrapper
const clone = value => JSON.parse(JSON.stringify(value))
const button = text => wrapper.findAll('button').find(item => item.text() === text)
beforeEach(() => {
  api.graphEntityTypes.mockResolvedValue(clone(entityTypeCatalog))
  api.resolveGraphEntityTypes.mockImplementation(async ({ entity_types, action, label }) => {
    let types = clone(entity_types)
    if (action === 'add_medical') types.push(...clone(entityTypeCatalog.presets[0].entity_types).filter(item => !types.some(existing => existing.label === item.label)))
    if (action === 'remove_medical') types = types.filter(item => item.source !== 'preset' || item.preset !== 'medical')
    if (action === 'add_custom') types.push({ code: 'custom_server_generated', label: label.normalize('NFKC').trim(), source: 'custom', description: '' })
    return { entity_types: types }
  })
})
afterEach(() => wrapper?.unmount())
async function render(types = entityTypeCatalog.base) {
  wrapper = mount(EntityTypeEditor, { props: { modelValue: clone(types), 'onUpdate:modelValue': value => wrapper.setProps({ modelValue: value }) } })
  await flushPromises()
}

it('adds, partially deletes, completes and removes a medical package while keeping custom types', async () => {
  await render()
  await button('＋ 医疗').trigger('click'); await flushPromises()
  expect(wrapper.findAll('.entity-chip')).toHaveLength(13)
  await wrapper.get('[aria-label="删除药品"]').trigger('click')
  await wrapper.get('[aria-label="删除检查"]').trigger('click')
  expect(wrapper.get('.medical-preset').text()).toBe('＋ 补全医疗 6/8')
  await button('＋ 补全医疗 6/8').trigger('click'); await flushPromises()
  expect(wrapper.findAll('.entity-chip')).toHaveLength(13)
  await wrapper.get('[aria-label="删除药品"]').trigger('click')
  await button('＋ 添加实体类型').trigger('click')
  await wrapper.get('input').setValue('医疗设备')
  await wrapper.get('input').trigger('keydown.enter'); await flushPromises()
  expect(wrapper.props('modelValue').at(-1).code).toBe('custom_server_generated')
  await button('－ 医疗').trigger('click'); await flushPromises()
  expect(wrapper.props('modelValue').map(item => item.label)).toEqual(['人物', '组织', '地点', '事件', '概念', '医疗设备'])
  expect(wrapper.get('.medical-preset').text()).toBe('＋ 医疗')
})

it('does not claim a preexisting custom disease and rejects duplicate normalized names', async () => {
  await render([...entityTypeCatalog.base, { code: 'custom_disease', label: '疾病', source: 'custom' }])
  await button('＋ 补全医疗 1/8').trigger('click'); await flushPromises()
  expect(wrapper.findAll('.entity-chip')).toHaveLength(13)
  await button('－ 医疗').trigger('click'); await flushPromises()
  expect(wrapper.props('modelValue').at(-1).source).toBe('custom')
  await button('＋ 添加实体类型').trigger('click')
  await wrapper.get('input').setValue('　疾病 ')
  await button('添加').trigger('click')
  expect(wrapper.get('[role="alert"]').text()).toContain('已存在')
  expect(api.resolveGraphEntityTypes).toHaveBeenCalledTimes(2)
})

it('keeps draft unchanged on failure and drops stale responses', async () => {
  await render()
  api.resolveGraphEntityTypes.mockRejectedValueOnce(new Error('服务暂不可用'))
  await button('＋ 医疗').trigger('click'); await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('服务暂不可用')
  expect(wrapper.props('modelValue')).toHaveLength(5)
  let finish
  api.resolveGraphEntityTypes.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await button('＋ 医疗').trigger('click')
  await wrapper.setProps({ modelValue: [] })
  finish({ entity_types: entityTypeCatalog.base }); await flushPromises()
  expect(wrapper.props('modelValue')).toEqual([])
})

it('Advanced uses the same editor, keeps provenance, cleans references and undoes the whole edit', async () => {
  wrapper = mount(AdvancedFlowEditor, { props: { outputTypes: ['graph:triple'] }, global: { stubs: {
    DataForgeFlowCanvas: { template: '<div />', methods: { fit() {} } }, OperatorPalette: true, NodeInspector: true,
  } } })
  const types = clone(entityTypeCatalog.presets[0].entity_types)
  const relation = { code: 'uses', label: '使用', source_types: ['disease'], target_types: ['drug'] }
  wrapper.vm.loadDefinition({ schema_version: 3, graph_config: { entity_types: types, relation_types: [relation] }, nodes: [
    { id: 'entity', kind: 'operator', ref: 'entity-extractor', params: { entity_types: ['drug'], entity_type_scope: 'subset' } },
    { id: 'relation', kind: 'operator', ref: 'relation-extractor', params: { relation_constraints: [{ relation_type: 'uses', ...relation }] } },
  ], edges: [] })
  await button('图谱抽取配置').trigger('click'); await flushPromises()
  expect(wrapper.findComponent(EntityTypeEditor).exists()).toBe(true)
  await wrapper.get('[aria-label="删除药品"]').trigger('click')
  let saved = wrapper.vm.serialize()
  expect(saved.graph_config.entity_types).toHaveLength(7)
  expect(saved.graph_config.relation_types[0].target_types).toEqual([])
  expect(saved.nodes[0].params.entity_types).toEqual([])
  expect(saved.nodes[0].params.entity_type_scope).toBe('subset')
  expect(saved.nodes[1].params.relation_constraints[0].target_types).toEqual([])
  expect(wrapper.emitted('dirty')).toBeTruthy()
  await button('↶ 撤销').trigger('click')
  saved = wrapper.vm.serialize()
  expect(saved.graph_config.entity_types).toEqual(types)
  expect(saved.nodes[0].params.entity_types).toEqual(['drug'])
  wrapper.vm.applyNormalizedDefinition({ ...saved, graph_config: { ...saved.graph_config, entity_types: [...types, { code: 'custom_123', label: '设备', source: 'custom' }] } })
  expect(wrapper.vm.serialize().graph_config.entity_types.at(-1).code).toBe('custom_123')
})
