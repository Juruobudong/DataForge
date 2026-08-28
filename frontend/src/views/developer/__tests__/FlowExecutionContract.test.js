import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync, writeFileSync } from 'node:fs'
import { flushPromises, mount } from '@vue/test-utils'
import { VueFlow } from '@vue-flow/core'
import AdvancedFlowEditor from '../../../components/flow/advanced/AdvancedFlowEditor.vue'
import OperatorPalette from '../../../components/flow/palette/OperatorPalette.vue'
import DataForgeFlowCanvas from '../../../components/flow/DataForgeFlowCanvas.vue'
import TemplateListView from '../TemplateListView.vue'
import { api } from '../../../api/platform'
import { keepCompatibleParams } from '../../../components/flow/flowModel.js'

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => router, useRoute: () => ({ query: {} }) }))
vi.mock('../../../api/platform', () => ({ api: Object.fromEntries(['operatorCandidates', 'flowTemplates', 'operatorCatalog', 'flowSubgraphs', 'knowledgeTypes', 'managedFlowTemplates', 'updateFlowTemplate', 'createFlowTemplate', 'modelServings', 'promptTemplates', 'qualityProfiles', 'publishFlowTemplate', 'previewFlowToAdvanced', 'resolveStandardFlow', 'graphEntityTypes'].map(name => [name, vi.fn()])) }))
const operator = (code, input = 'candidate:*', output = 'candidate:*', properties = {}) => ({ code, name: code, display_name_zh: code, version: 1, version_status: 'published', provider: 'dataforge', exposure: 'public', enabled: true, approved: true, node_role: 'operator', surfaces: ['advanced-canvas'], knowledge_types: ['text'], input_ports: { input: { artifact_type: input, binding: 'edge', required: true } }, output_ports: { output: { artifact_type: output } }, parameter_schema: { type: 'object', properties, additionalProperties: false } })
const catalog = [
  { ...operator('reviewed-source-chunk-input', 'approved_source_chunks', 'source_chunk_set'), node_role: 'flow_input', input_ports: { input: { artifact_type: 'approved_source_chunks', binding: 'runtime_input' } } },
  operator('mapper', 'source_chunk_set', 'candidate:text'),
  operator('old-filter', 'candidate:*', 'candidate:*', { threshold: { type: 'number', default: 0.5 } }),
  operator('new-filter', 'candidate:*', 'candidate:*', { threshold: { type: 'number', maximum: 1, default: 0.4 } }),
  operator('generator', 'source_chunk_set', 'candidate:text'),
]
const definition = () => ({ schema_version: 3, nodes: [
  { id: 'input', kind: 'operator', ref: 'reviewed-source-chunk-input', operator_version: 1 },
  { id: 'map', kind: 'operator', ref: 'mapper', operator_version: 1 },
  { id: 'filter', kind: 'operator', ref: 'old-filter', operator_version: 1, params: { threshold: 0.7, knowledge_type: 'text' } },
  { id: 'sink', kind: 'knowledge_sink', knowledge_type: 'text', output_key: 'text' },
], edges: [['input', 'map'], ['map', 'filter'], ['filter', 'sink']], ui: { positions: { input: { x: 0, y: 0 }, map: { x: 200, y: 0 }, filter: { x: 400, y: 100 }, sink: { x: 700, y: 0 } } } })
let wrapper
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  api.operatorCandidates.mockResolvedValue(catalog)
  api.operatorCatalog.mockResolvedValue(catalog)
  for (const name of ['flowSubgraphs', 'knowledgeTypes', 'managedFlowTemplates', 'modelServings', 'promptTemplates', 'qualityProfiles']) api[name].mockResolvedValue([])
  api.resolveStandardFlow.mockResolvedValue({})
  api.graphEntityTypes.mockResolvedValue({ base: [], presets: [] })
})

// Injected by the backend four-DAG matrix from a freshly seeded database.
// The resulting real component save body is compiled and executed by pytest.
// No checked-in copy of the backend DAG/catalog is allowed to drift silently.
if (process.env.DATAFORGE_DAG_EDITOR_INPUT) {
  const fixture = JSON.parse(readFileSync(process.env.DATAFORGE_DAG_EDITOR_INPUT, 'utf8'))
  const saved = {}
  describe(`builtin DAG editor bridge: ${fixture.template.code}`, () => {
    it.each(['保存草稿', '运行当前流程'])('preserves the original DAG through %s', async action => {
      api.flowTemplates.mockResolvedValue([fixture.template])
      api.operatorCatalog.mockResolvedValue(fixture.catalog)
      api.operatorCandidates.mockResolvedValue(fixture.catalog)
      api.managedFlowTemplates.mockResolvedValue(fixture.managed_templates)
      api.previewFlowToAdvanced.mockResolvedValue(fixture.preview)
      api.createFlowTemplate.mockImplementation(async body => ({ id: 'converted', definition: body.definition,
        output_types: body.output_types, revision_id: 'converted-r1', revision: 1,
        revision_status: 'draft', source_definition_checksum: 'saved-checksum' }))
      wrapper = mount(TemplateListView, { attachTo: document.body }); await flushPromises()
      await wrapper.get('.template-list button').trigger('click'); await flushPromises()
      await button('转换为高级编排').trigger('click'); await flushPromises()
      expect(api.previewFlowToAdvanced).toHaveBeenCalledWith(fixture.template.id)
      expect(api.createFlowTemplate).not.toHaveBeenCalled()
      const active = wrapper.findComponent(AdvancedFlowEditor)
      expect(active.findComponent(DataForgeFlowCanvas).exists()).toBe(true)
      expect(active.vm.validate()).toBe(true)
      const serialized = active.vm.serialize()
      expect(serialized.nodes).toEqual(fixture.preview.definition.nodes.map(node => ({ ...node,
        node_role: node.node_role || (node.kind === 'knowledge_sink' ? 'knowledge_output' : 'operator') })))
      expect(serialized.edges).toEqual(fixture.preview.definition.edges)
      await button(action).trigger('click'); await flushPromises()
      expect(api.createFlowTemplate).toHaveBeenCalledTimes(1)
      const body = api.createFlowTemplate.mock.calls[0][0]
      expect(body).toMatchObject({ authoring_mode: 'advanced', definition: serialized,
        output_types: fixture.template.output_types, derived_from_template_id: fixture.template.id,
        derived_from_revision_id: fixture.preview.source_revision_id })
      expect(api.updateFlowTemplate).not.toHaveBeenCalled()
      if (action === '运行当前流程') expect(router.push).toHaveBeenCalledWith(
        '/developer/dataflow-debug?template_id=converted&revision_kind=draft&draft_checksum=saved-checksum&prepare=1')
      else expect(router.push).not.toHaveBeenCalled()
      saved[action] = body
      writeFileSync(process.env.DATAFORGE_DAG_EDITOR_OUTPUT, JSON.stringify(saved), 'utf8')
    })
  })
}
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = ''; vi.unstubAllGlobals() })
const button = text => wrapper.findAll('button').find(item => item.text() === text)
async function editor() {
  wrapper = mount(AdvancedFlowEditor, { props: { catalog, outputTypes: ['text'] }, attachTo: document.body })
  wrapper.vm.loadDefinition(definition())
  await flushPromises()
  return wrapper
}
async function selectNode(editorWrapper, id) {
  const node = editorWrapper.vm.nodes.find(item => item.id === id)
  editorWrapper.findComponent(VueFlow).vm.$emit('nodeClick', { node })
  await flushPromises()
}

describe('real editor, palette and canvas mutations', () => {
  it('replaces atomically, preserves compatible edges/parameters and undoes once', async () => {
    await editor()
    expect(wrapper.findComponent(DataForgeFlowCanvas).exists()).toBe(true)
    await selectNode(wrapper, 'filter')
    const before = wrapper.vm.serialize(), dirty = wrapper.emitted('dirty')?.length || 0
    await button('替换算子').trigger('click')
    await wrapper.get('[aria-label="替换目标算子"]').setValue('new-filter')
    await button('确认替换').trigger('click')
    const after = wrapper.vm.serialize()
    expect(after.nodes.find(item => item.id === 'filter')).toMatchObject({ ref: 'new-filter', params: { threshold: 0.7 } })
    expect(after.ui.positions.filter).toEqual(before.ui.positions.filter)
    expect(after.edges).toEqual(before.edges)
    expect(wrapper.emitted('dirty').length - dirty).toBe(1)
    await button('↶ 撤销').trigger('click')
    expect(wrapper.vm.serialize()).toEqual(before)
  })
  it('prunes only incompatible edges, reports them and restores everything with one undo', async () => {
    await editor(); await selectNode(wrapper, 'filter')
    const before = wrapper.vm.serialize()
    await button('替换算子').trigger('click')
    await wrapper.get('[aria-label="替换目标算子"]').setValue('generator')
    await button('确认替换').trigger('click')
    await flushPromises()
    expect(wrapper.vm.serialize().nodes.find(node => node.id === 'filter').ref).toBe('generator')
    expect(wrapper.vm.serialize().edges).toHaveLength(2)
    expect(wrapper.emitted('error').at(-1)[0]).toContain('1 条不兼容连线')
    expect(wrapper.vm.validate()).toBe(false)
    await button('↶ 撤销').trigger('click')
    expect(wrapper.vm.serialize()).toEqual(before)
  })
  it('adds from the real palette, deletes without auto-wiring and connects through the real canvas', async () => {
    await editor()
    await vi.waitFor(() => expect(wrapper.findAll('.palette-entry').length).toBeGreaterThan(0))
    await wrapper.findAll('.palette-entry').find(item => item.text().includes('new-filter')).trigger('dblclick')
    expect(wrapper.vm.serialize().nodes).toHaveLength(5)
    const added = wrapper.vm.nodes.find(node => node.data.definition.ref === 'new-filter')
    wrapper.vm.nodes.forEach(node => { node.selected = node.id === added.id })
    wrapper.get('.flow-canvas').element.focus()
    await wrapper.get('.flow-canvas').trigger('keydown', { key: 'Delete' })
    expect(wrapper.vm.serialize().nodes).toHaveLength(4)
    const edge = wrapper.vm.edges.find(item => item.source === 'map')
    wrapper.findComponent(DataForgeFlowCanvas).vm.deleteEdge(edge.id)
    await flushPromises()
    expect(wrapper.vm.validate()).toBe(false)
    await flushPromises()
    wrapper.findComponent(VueFlow).vm.$emit('connect', { source: 'map', target: 'filter', sourceHandle: 'output', targetHandle: 'input' })
    await flushPromises()
    expect(wrapper.vm.serialize().edges).toHaveLength(3)
    expect(wrapper.vm.validate()).toBe(true)
  })
  it('saves the actual parameter mutation before routing to Debug', async () => {
    api.flowTemplates.mockResolvedValue([{ id: 'flow', code: 'flow', name: 'Flow', authoring_mode: 'advanced', output_types: ['text'], definition: definition(), revision_id: 'r1', revision: 1, revision_status: 'published', source_definition_checksum: 'old' }])
    api.updateFlowTemplate.mockImplementation(async (id, body) => ({ id, definition: body.definition, revision_id: 'r2', revision: 2, revision_status: 'draft', source_definition_checksum: 'new' }))
    wrapper = mount(TemplateListView, { attachTo: document.body }); await flushPromises()
    await wrapper.get('.template-list button').trigger('click'); await flushPromises()
    const active = wrapper.findComponent(AdvancedFlowEditor)
    await selectNode(active, 'filter')
    await wrapper.get('.parameter-form input[type="number"]').setValue('0.8')
    await button('运行当前流程').trigger('click'); await flushPromises()
    expect(api.updateFlowTemplate.mock.calls.at(-1)[1].definition.nodes.find(node => node.id === 'filter').params.threshold).toBe(0.8)
    expect(router.push).toHaveBeenCalledWith(expect.stringContaining('revision_kind=draft&draft_checksum=new'))
  })
})

it('keeps only schema-compatible business values and applies target defaults', () => {
  expect(keepCompatibleParams({ n: 3.5, mode: 'invalid', _resolved_prompt_template: {}, keep: 'yes' }, { properties: { n: { type: 'integer', default: 3 }, mode: { type: 'string', enum: ['safe'], default: 'safe' }, keep: { type: 'string' } } })).toEqual({ n: 3, mode: 'safe', keep: 'yes' })
})

it('shows ten curated cards, business groups and actual readiness counts', async () => {
  const groups = { Text2QAGenerator: '内容生成', PromptedRefiner: '内容处理', ContentNullFilter: '内容清洗', CharNumberFilter: '内容清洗', SpecialCharacterFilter: '内容清洗', HashDeduplicateFilter: '内容去重', MinHashDeduplicateFilter: '内容去重', NgramHashDeduplicateFilter: '内容去重', SimHashDeduplicateFilter: '内容去重', PromptedFilter: '智能过滤' }
  const items = Object.entries(groups).map(([code, subcategory]) => ({ ...operator(code), provider: 'dataflow', subcategory, dependency_status: { status: 'ready' } }))
  wrapper = mount(OperatorPalette, { props: { catalog: items } })
  expect(wrapper.findAll('.palette-entry')).toHaveLength(10)
  expect(wrapper.get('.capability-count').text()).toBe('10')
  expect(wrapper.findAll('h4').map(item => item.text())).toEqual(['文本处理', '去重', '文本优化', '知识生成', '质量治理'])
  expect(wrapper.text()).not.toContain('DataFlow ·')
  await wrapper.setProps({ catalog: items.map((item, index) => index === 0 ? { ...item, dependency_status: { status: 'missing' } } : item) })
  expect(wrapper.findAll('.palette-entry')).toHaveLength(9)
})
