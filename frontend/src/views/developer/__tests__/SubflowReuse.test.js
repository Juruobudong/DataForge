import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import OperatorPalette from '../../../components/flow/palette/OperatorPalette.vue'
import AdvancedFlowEditor from '../../../components/flow/advanced/AdvancedFlowEditor.vue'
import SubgraphView from '../SubgraphView.vue'
import { api } from '../../../api/platform'
import { dataflowOperators } from '../../../components/flow/__tests__/flowFixtures'

const router = vi.hoisted(() => ({ push: vi.fn() }))
const route = vi.hoisted(() => ({ params: { subflowId: 'parent', revision: '1' }, query: { return_template_id: 'consumer' } }))
vi.mock('vue-router', () => ({ useRouter: () => router, useRoute: () => route }))
vi.mock('../../../api/platform', () => ({ api: Object.fromEntries(['createFlowSubgraph', 'flowSubgraphRevision', 'flowSubgraphs', 'operatorCatalog', 'flowSubgraphReferences', 'operatorCandidates'].map(name => [name, vi.fn()])) }))
vi.mock('../../../components/flow/DataForgeFlowCanvas.vue', () => ({ default: {
  name: 'CanvasStub', props: ['nodes', 'edges'], template: '<div class="canvas-stub"></div>',
  methods: { fit() {}, screenToFlowCoordinate(value) { return value } },
} }))

const catalog = [{ code: 'quality', name: 'Quality', display_name_zh: '质量评估', category: 'quality-processing', source: 'dataforge', catalog_group: 'dataforge', version: 1,
  input_ports: { input: { artifact_type: 'candidate:text', cardinality: 'one', binding: 'edge' } },
  output_ports: { output: { artifact_type: 'candidate:text' } }, parameter_schema: {} }]
const inner = { entry_node: 'q', exit_node: 'q', nodes: [{ id: 'q', kind: 'operator', ref: 'quality' }], edges: [] }
const version = revision => ({ id: 'quality', code: 'quality-flow', name: '质量治理', revision, revision_id: `r${revision}`, revision_status: 'published', status: 'active', usage: 'knowledge', definition: inner,
  input_contract: catalog[0].input_ports, output_contract: catalog[0].output_ports })
const asset = { ...version(2), revisions: [version(2), version(1)] }
let wrapper
beforeEach(() => {
  api.operatorCandidates.mockResolvedValue(catalog)
  api.operatorCatalog.mockResolvedValue(catalog)
  api.flowSubgraphs.mockResolvedValue([asset])
  api.flowSubgraphReferences.mockResolvedValue({ reference_count: 0, references: [], unlocked_references: [] })
})
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })
const button = label => wrapper.findAll('button').find(item => item.text() === label)

describe('reusable subflow production and consumption', () => {
  it('offers Hash and MinHash as separate bilingual nodes with exact refs', async () => {
    const operators = dataflowOperators.filter(item => item.code.endsWith('DeduplicateFilter'))
    wrapper = mount(OperatorPalette, { props: { catalog: operators, subflows: [] } })
    for (const [index, card] of wrapper.findAll('.palette-entry').entries()) {
      expect(card.text()).toContain(operators[index].display_name_zh)
      expect(card.text()).toContain(`${operators[index].code}`)
      await card.trigger('dblclick')
      expect(wrapper.emitted('add-item').at(-1)[0].code).toBe(operators[index].code)
    }
    expect(wrapper.findAll('.palette-entry')).toHaveLength(2)
  })
  it('edits QA extraction instructions as a normal Advanced business parameter', async () => {
    const qa = { ...catalog[0], code: 'qa-extractor', version: 1, source: 'dataforge', catalog_group: 'dataforge',
      input_ports: { input: { artifact_type: 'source_chunk_set', binding: 'edge' } },
      output_ports: { output: { artifact_type: 'candidate:qa' } },
      parameter_schema: { properties: { extraction_instructions: { type: 'string', title: 'QA 提取要求', 'x-dataforge-ui': { widget: 'textarea' } } } } }
    wrapper = mount(AdvancedFlowEditor, { props: { catalog: [qa], subflows: [], outputTypes: ['qa'] } })
    wrapper.vm.loadDefinition({ nodes: [{ id: 'qa', kind: 'operator', ref: 'qa-extractor', operator_version: 1, params: { extraction_instructions: '原要求' } }], edges: [] })
    wrapper.findComponent({ name: 'CanvasStub' }).vm.$emit('select-node', wrapper.vm.nodes[0])
    await flushPromises()
    await wrapper.get('textarea[aria-label="QA 提取要求"]').setValue('只提取随访事项\n使用患者口吻')
    expect(wrapper.vm.serialize().nodes[0].params.extraction_instructions).toBe('只提取随访事项\n使用患者口吻')
  })
  it('adding a sink never inserts a Diff even when an old catalog contains it', async () => {
    wrapper = mount(AdvancedFlowEditor, { props: { catalog: [...catalog, { ...catalog[0], code: 'knowledge-diff' }], subflows: [], outputTypes: ['text'] } })
    wrapper.findComponent(OperatorPalette).vm.$emit('add-sink', 'text')
    await flushPromises()
    expect(wrapper.vm.serialize().nodes.map(node => node.kind)).toEqual(['knowledge_sink'])
    expect(wrapper.vm.serialize().edges).toEqual([])
  })
  it('queries contextual candidates for the selected node and direction', async () => {
    wrapper = mount(AdvancedFlowEditor, { props: { catalog, subflows: [], outputTypes: ['text'] } })
    wrapper.vm.loadDefinition({ nodes: [{ id: 'quality-node', kind: 'operator', ref: 'quality', operator_version: 1 }], edges: [] })
    await flushPromises()
    api.operatorCandidates.mockResolvedValue([{ ...catalog[0], compatibility: { compatible: true, direction: 'downstream', source_port: 'output', target_port: 'input' }, runtime_status: { status: 'ready' } }])
    wrapper.findComponent({ name: 'CanvasStub' }).vm.$emit('select-node', wrapper.vm.nodes[0])
    await vi.waitFor(() => expect(api.operatorCandidates).toHaveBeenCalledWith(expect.objectContaining({ node_id: 'quality-node', direction: 'downstream', include_incompatible: true })))
    await flushPromises()
    expect(wrapper.findComponent(OperatorPalette).props('candidateResults')[0].code).toBe('quality')
    api.operatorCandidates.mockResolvedValue([])
    wrapper.findComponent(OperatorPalette).vm.$emit('change-direction', 'upstream')
    await vi.waitFor(() => expect(api.operatorCandidates).toHaveBeenCalledWith(expect.objectContaining({ node_id: 'quality-node', direction: 'upstream', include_incompatible: true })))
  })
  it('adds a compatible discovery result with its validated edge', async () => {
    const result = { ...catalog[0], compatibility: { compatible: true, direction: 'downstream', source_port: 'output', target_port: 'input' }, runtime_status: { status: 'ready' } }
    api.operatorCandidates.mockResolvedValue([result])
    wrapper = mount(AdvancedFlowEditor, { props: { catalog, subflows: [], outputTypes: ['text'] } })
    wrapper.vm.loadDefinition({ nodes: [{ id: 'quality-node', kind: 'operator', ref: 'quality', operator_version: 1 }], edges: [] })
    await flushPromises()
    wrapper.findComponent({ name: 'CanvasStub' }).vm.$emit('select-node', wrapper.vm.nodes[0])
    await vi.waitFor(() => expect(wrapper.findComponent(OperatorPalette).props('candidateResults')?.[0]?.code).toBe('quality'))
    wrapper.findComponent(OperatorPalette).vm.$emit('add-item', catalog[0], 'operator')
    await flushPromises()
    const definition = wrapper.vm.serialize()
    expect(definition.nodes).toHaveLength(2)
    expect(definition.edges).toEqual([{ source: 'quality-node', source_port: 'output', target: expect.stringMatching(/^quality-/), target_port: 'input' }])
  })
  it('adds a compatible Knowledge Sink as a connected downstream node', async () => {
    api.operatorCandidates.mockResolvedValue([{ ...catalog[0], compatibility: { compatible: true, direction: 'downstream', source_port: 'output', target_port: 'input' }, runtime_status: { status: 'ready' } }])
    wrapper = mount(AdvancedFlowEditor, { props: { catalog, subflows: [], outputTypes: ['text'] } })
    wrapper.vm.loadDefinition({ nodes: [{ id: 'quality-node', kind: 'operator', ref: 'quality', operator_version: 1 }], edges: [] })
    await flushPromises()
    wrapper.findComponent({ name: 'CanvasStub' }).vm.$emit('select-node', wrapper.vm.nodes[0])
    await vi.waitFor(() => expect(wrapper.find('.sink-item').attributes('disabled')).toBeUndefined())
    await wrapper.find('.sink-item').trigger('dblclick')
    const definition = wrapper.vm.serialize()
    expect(definition.nodes.at(-1)).toMatchObject({ kind: 'knowledge_sink', output_key: 'text' })
    expect(definition.edges).toEqual([{ source: 'quality-node', source_port: 'output', target: expect.stringMatching(/^sink-text-/), target_port: 'input' }])
  })
  it('searches subflows, selects old published versions and excludes preparation from insertion', async () => {
    wrapper = mount(OperatorPalette, { props: { catalog, subflows: [asset, { ...asset, id: 'prepare', name: '文档预处理', usage: 'source_preparation' }] } })
    expect(wrapper.findAll('.subflow-item')).toHaveLength(1)
    await wrapper.get('input').setValue('quality-flow')
    await wrapper.findAll('.subflow-item select')[0].setValue('r1')
    const card = wrapper.get('.subflow-item')
    expect(card.find('button').exists()).toBe(false)
    await card.trigger('dragstart')
    expect(wrapper.emitted('drag-start')[0].slice(1)).toEqual([version(1), 'subflow'])
    expect(wrapper.emitted('add-item')).toBeUndefined()
    await wrapper.get('input').setValue('absent')
    expect(wrapper.findAll('.subflow-item')).toHaveLength(0)
  })

  it('inserts pinned revisions, switches explicitly without deleting edges and opens the exact revision', async () => {
    wrapper = mount(AdvancedFlowEditor, { props: { catalog, subflows: [asset], outputTypes: ['text'] } })
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' }
    await wrapper.get('.subflow-item').trigger('dragstart', { dataTransfer })
    expect(wrapper.vm.nodes).toHaveLength(0)
    expect(dataTransfer.effectAllowed).toBe('copy')
    const [type, payload] = dataTransfer.setData.mock.calls[0]
    expect(type).toBe('application/dataforge-operator')
    wrapper.findComponent({ name: 'CanvasStub' }).vm.$emit('add-definition', JSON.parse(payload), { x: 600, y: 350 })
    await flushPromises()
    expect(wrapper.vm.nodes).toHaveLength(1)
    expect(wrapper.vm.nodes[0].position).toEqual({ x: 465, y: 280 })
    const graph = wrapper.vm.serialize()
    expect(graph.nodes[0].subflow_revision_id).toBe('r2')
    const node = wrapper.vm.nodes[0]
    wrapper.vm.edges.push({ id: 'retained', source: node.id, target: 'missing', sourceHandle: 'output', targetHandle: 'input' })
    wrapper.findComponent({ name: 'CanvasStub' }).vm.$emit('select-node', node)
    await flushPromises()
    await wrapper.get('[aria-label="子流程引用版本"]').setValue('r1')
    expect(wrapper.vm.serialize().nodes[0].subflow_revision_id).toBe('r1')
    expect(wrapper.vm.edges).toHaveLength(1)
    await button('查看内部 DAG').trigger('click')
    expect(wrapper.emitted('open-subflow')[0][0].revision).toBe(1)
  })

  it('drags an operator without an add button and inserts only on canvas drop with undo', async () => {
    wrapper = mount(AdvancedFlowEditor, { props: { catalog, subflows: [], outputTypes: ['text'] } })
    const card = wrapper.get('.palette-entry')
    expect(card.findAll('button').map(item => item.text())).toEqual(['i'])
    await card.trigger('click')
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' }
    await card.trigger('dragstart', { dataTransfer })
    expect(wrapper.vm.nodes).toHaveLength(0)
    const [type, payload] = dataTransfer.setData.mock.calls[0]
    expect(type).toBe('application/dataforge-operator')
    expect(JSON.parse(payload)).toEqual({ kind: 'operator', ref: 'quality', params: {}, operator_version: 1 })
    wrapper.findComponent({ name: 'CanvasStub' }).vm.$emit('add-definition', JSON.parse(payload), { x: 600, y: 350 })
    await flushPromises()
    expect(wrapper.vm.nodes).toHaveLength(1)
    expect(wrapper.vm.nodes[0].position).toEqual({ x: 465, y: 280 })
    await button('↶ 撤销').trigger('click')
    expect(wrapper.vm.nodes).toHaveLength(0)
  })

  it('keeps keyboard insertion on cards without hijacking subflow revision selection', async () => {
    wrapper = mount(OperatorPalette, { props: { catalog, subflows: [asset] } })
    await wrapper.get('.palette-entry .entry-body').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('add-item')[0]).toEqual([catalog[0], 'operator'])
    await wrapper.get('.subflow-item select').setValue('r1')
    await wrapper.get('.subflow-item select').trigger('keydown', { key: 'Enter' })
    await wrapper.get('.subflow-item select').trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('add-item')).toHaveLength(1)
    await wrapper.get('.subflow-item').trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('add-item')[1]).toEqual([version(1), 'subflow'])
  })

  it('extracts selected current nodes without replacing the source graph', async () => {
    wrapper = mount(AdvancedFlowEditor, { props: { catalog, subflows: [asset], outputTypes: ['text'] } })
    wrapper.vm.loadDefinition({ nodes: [{ id: 'a', kind: 'operator', ref: 'quality' }, { id: 'b', kind: 'operator', ref: 'quality' }], edges: [{ source: 'a', target: 'b' }] })
    wrapper.vm.nodes.forEach(node => { node.selected = true })
    await flushPromises()
    const original = wrapper.vm.serialize()
    api.createFlowSubgraph.mockResolvedValue({ id: 'new', revision: 1 })
    await button('另存为可复用子流程').trigger('click')
    const inputs = wrapper.findAll('[role="dialog"] input')
    await inputs[0].setValue('新子流程'); await inputs[1].setValue('new-flow')
    await wrapper.get('[role="dialog"] form').trigger('submit')
    await flushPromises()
    expect(api.createFlowSubgraph).toHaveBeenCalledWith(expect.objectContaining({ selected_node_ids: ['a', 'b'], definition: original }))
    expect(wrapper.vm.serialize()).toEqual(original)
    expect(wrapper.emitted('subflow-created')[0][0].id).toBe('new')
    await button('查看草稿').trigger('click')
    expect(wrapper.emitted('open-subflow')[0][0]).toEqual({ id: 'new', revision: 1 })
  })

  it('drills into the pinned nested revision and retains the source flow breadcrumb', async () => {
    const parent = { id: 'parent', name: '父流程', revision: 1, revision_status: 'published', definition: { nodes: [{ id: 'nested', kind: 'subflow', ref: asset.code, subflow_revision_id: 'r1' }], edges: [] } }
    api.flowSubgraphRevision.mockResolvedValue(parent)
    wrapper = mount(SubgraphView)
    await flushPromises()
    const canvas = wrapper.findComponent({ name: 'CanvasStub' })
    canvas.vm.$emit('open-subflow', canvas.props('nodes')[0])
    await flushPromises()
    expect(router.push).toHaveBeenCalledWith(expect.objectContaining({ path: '/developer/flow-templates/subgraphs/quality/revisions/1', query: expect.objectContaining({ return_template_id: 'consumer' }) }))
    expect(wrapper.text()).toContain('返回来源知识流程')
    await button('查看引用').trigger('click'); await flushPromises()
    expect(api.flowSubgraphReferences).toHaveBeenCalledWith('parent', 1)
  })
})
