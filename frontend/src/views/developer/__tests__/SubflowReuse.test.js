import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import OperatorPalette from '../../../components/flow/palette/OperatorPalette.vue'
import AdvancedFlowEditor from '../../../components/flow/advanced/AdvancedFlowEditor.vue'
import SubgraphView from '../SubgraphView.vue'
import { api } from '../../../api/platform'

const router = vi.hoisted(() => ({ push: vi.fn() }))
const route = vi.hoisted(() => ({ params: { subflowId: 'parent', revision: '1' }, query: { return_template_id: 'consumer' } }))
vi.mock('vue-router', () => ({ useRouter: () => router, useRoute: () => route }))
vi.mock('../../../api/platform', () => ({ api: Object.fromEntries(['createFlowSubgraph', 'flowSubgraphRevision', 'flowSubgraphs', 'operatorCatalog', 'flowSubgraphReferences', 'operatorCandidates'].map(name => [name, vi.fn()])) }))
vi.mock('../../../components/flow/DataForgeFlowCanvas.vue', () => ({ default: {
  name: 'CanvasStub', props: ['nodes', 'edges'], template: '<div class="canvas-stub"></div>',
  methods: { fit() {}, screenToFlowCoordinate(value) { return value } },
} }))

const catalog = [{ code: 'quality', name: 'Quality', display_name_zh: '质量评估', category: '质量治理', version: 1,
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
  it('queries server candidates for the selected port and keeps only returned operators', async () => {
    wrapper = mount(AdvancedFlowEditor, { props: { catalog, subflows: [], outputTypes: ['text'] } })
    wrapper.vm.loadDefinition({ nodes: [{ id: 'quality-node', kind: 'operator', ref: 'quality', operator_version: 1 }], edges: [] })
    await flushPromises()
    wrapper.findComponent({ name: 'CanvasStub' }).vm.$emit('select-node', wrapper.vm.nodes[0])
    await vi.waitFor(() => expect(api.operatorCandidates).toHaveBeenCalledWith(expect.objectContaining({ source_node_id: 'quality-node', source_port: 'output' })))
    await flushPromises()
    expect(wrapper.findComponent(OperatorPalette).props('candidateCodes')).toEqual(['quality'])
    api.operatorCandidates.mockResolvedValue([])
    wrapper.findComponent(OperatorPalette).vm.$emit('clear-source')
    await vi.waitFor(() => expect(wrapper.findComponent(OperatorPalette).props('candidateCodes')).toEqual([]))
  })
  it('searches subflows, selects old published versions and excludes preparation from insertion', async () => {
    wrapper = mount(OperatorPalette, { props: { catalog, subflows: [asset, { ...asset, id: 'prepare', name: '文档预处理', usage: 'source_preparation' }] } })
    expect(wrapper.findAll('.subflow-item')).toHaveLength(1)
    await wrapper.get('input').setValue('quality-flow')
    await wrapper.findAll('.subflow-item select')[0].setValue('r1')
    await wrapper.findAll('.subflow-item button')[0].trigger('click')
    expect(wrapper.emitted('add-item')[0]).toEqual([version(1), 'subflow'])
    await wrapper.get('input').setValue('absent')
    expect(wrapper.findAll('.subflow-item')).toHaveLength(0)
  })

  it('inserts pinned revisions, switches explicitly without deleting edges and opens the exact revision', async () => {
    wrapper = mount(AdvancedFlowEditor, { props: { catalog, subflows: [asset], outputTypes: ['text'] } })
    await wrapper.get('.subflow-item button').trigger('click')
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
