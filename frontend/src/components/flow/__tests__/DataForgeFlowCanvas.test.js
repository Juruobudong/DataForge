import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import DataForgeFlowCanvas from '../DataForgeFlowCanvas.vue'
import { makeCanvasNode } from '../flowModel.js'

vi.mock('@vue-flow/core', async importOriginal => ({
  ...await importOriginal(),
  useVueFlow: () => ({ fitView: vi.fn(), screenToFlowCoordinate: vi.fn(), setCenter: vi.fn(), endConnection: vi.fn() }),
}))

const VueFlowStub = defineComponent({
  name: 'VueFlow', props: ['nodes', 'edges', 'isValidConnection'],
  emits: ['connect', 'connectStart', 'connectEnd', 'edgeUpdateStart', 'edgeUpdate', 'edgeUpdateEnd'],
  template: '<div><div v-for="node in nodes" :key="node.id" class="typed-handle" :data-port-key="`${node.id}::input::input`" /></div>',
})
const catalog = [
  { code: 'qa-extractor', output_ports: { output: { artifact_type: 'candidate:qa' } } },
  { code: 'PIIAnonymizeRefiner', input_ports: { input: { artifact_type: 'text_record_set', accepted_types: ['source_chunk_set', 'derived_text_set', 'candidate:text', 'candidate:qa'], cardinality: 'one' } },
    output_ports: { output: { artifact_type: 'text_record_set', output_by_input: { source_chunk_set: 'derived_text_set', derived_text_set: 'derived_text_set', 'candidate:text': 'candidate:text', 'candidate:qa': 'candidate:qa' } } } },
]
const connection = { source: 'qa', sourceHandle: 'output', target: 'pii', targetHandle: 'input' }
let wrapper
afterEach(() => wrapper?.unmount())

function setup(edges = []) {
  const nodes = ['qa', 'pii'].map((id, index) => makeCanvasNode({ id, kind: 'operator', ref: catalog[index].code }, { x: 0, y: 0 }, catalog))
  wrapper = mount(DataForgeFlowCanvas, { attachTo: document.body, props: { nodes, edges, flowContext: { schemaVersion: 3, outputTypes: ['qa'] } },
    global: { stubs: { VueFlow: VueFlowStub, Background: true, Controls: true, MiniMap: true } } })
  wrapper.find('[data-port-key="pii::input::input"]').element.getBoundingClientRect = () => ({ left: 500, top: 300, width: 10, height: 10 })
  return { flow: wrapper.findComponent(VueFlowStub), edges }
}
async function start(flow) {
  flow.vm.$emit('connectStart', { nodeId: 'qa', handleId: 'output', handleType: 'source' })
  await nextTick()
  wrapper.element.dispatchEvent(new MouseEvent('pointermove', { clientX: 505, clientY: 305, bubbles: true }))
  await nextTick()
}
async function release(flow, x = 505) {
  flow.vm.$emit('connectEnd', { clientX: x, clientY: 305 })
  await nextTick()
}

describe('typed port release', () => {
  it('commits QA → PII at the highlighted port even when native connect is not emitted', async () => {
    const { flow, edges } = setup()
    await start(flow)
    await release(flow)
    expect(edges).toHaveLength(1)
    expect(edges[0]).toMatchObject(connection)
    expect(wrapper.emitted('before-change')).toHaveLength(1)
  })

  it('does not commit a second edge after native connect already succeeded', async () => {
    const { flow, edges } = setup()
    await start(flow)
    flow.vm.$emit('connect', connection)
    await release(flow)
    expect(edges).toHaveLength(1)
    expect(wrapper.emitted('before-change')).toHaveLength(1)
  })

  it('rechecks live graph constraints when an input becomes occupied during dragging', async () => {
    const { flow, edges } = setup()
    await start(flow)
    edges.push({ id: 'other', ...connection })
    await release(flow)
    expect(edges).toHaveLength(1)
    expect(wrapper.emitted('before-change')).toBeUndefined()
    expect(wrapper.emitted('connection-error').at(-1)[0].code).toBe('EDGE_DUPLICATED')
  })

  it('does not commit outside the snap radius or after Escape', async () => {
    const { flow, edges } = setup()
    await start(flow)
    await release(flow, 600)
    expect(edges).toHaveLength(0)
    await start(flow)
    await wrapper.trigger('keydown', { key: 'Escape' })
    await release(flow)
    expect(edges).toHaveLength(0)
  })

  it('native reconnect validation excludes the original edge and replaces it atomically', async () => {
    const original = { id: 'original', ...connection }
    const { flow, edges } = setup([original])
    flow.vm.$emit('edgeUpdateStart', { edge: original })
    await start(flow)
    expect(flow.props('isValidConnection')(connection)).toBe(true)
    await release(flow)
    expect(edges).toHaveLength(1)
    expect(edges[0]).toMatchObject({ id: 'original', ...connection })
    expect(wrapper.emitted('before-change')).toHaveLength(1)
  })
})
