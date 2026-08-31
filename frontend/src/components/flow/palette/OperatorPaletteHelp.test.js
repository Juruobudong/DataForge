import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import OperatorPalette from './OperatorPalette.vue'
import FilterRulesEditor from '../inspector/FilterRulesEditor.vue'
import { makeCanvasNode } from '../flowModel.js'
import { checkEdgeCompatibility } from '../edge/edgeCompatibility.js'

let wrapper
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = ''; vi.useRealTimers() })
const item = (code = 'HtmlEntityFilter', source = 'dataflow') => ({ code, name: code, display_name_zh: 'HTML 实体过滤器', summary: '删除含HTML实体的整条文本，不是局部清理。', description: '删除含HTML实体的整条文本，不是局部清理。', source, catalog_group: source === 'custom' ? 'custom' : source === 'dataflow' ? 'dataflow_featured' : 'dataforge', category: source === 'custom' ? 'content-filtering' : source === 'dataflow' ? 'content-filtering' : 'content-processing', version: 1, exposure: 'canvas', status: 'published', enabled: true, approved: true, surfaces: ['advanced-canvas'], knowledge_types: ['text'], dependency_status: { status: 'ready' }, runtime_requirements: { driver: source === 'custom' ? 'custom' : source === 'dataflow' ? 'dataflow' : 'builtin', executor: source === 'dataforge' ? 'dataforge-native' : 'dataflow-storage', uses_llm: false, resources: 'CPU', data_behavior: '过滤整条文本', limitations: '不进行HTML实体替换' }, input_ports: { input: { artifact_type: 'candidate:text' } }, output_ports: { output: { artifact_type: 'candidate:text' } } })
function palette(catalog = [item()]) { wrapper = mount(OperatorPalette, { props: { catalog, outputTypes: ['text'] }, attachTo: document.body }); return wrapper }
const info = () => wrapper.get('.operator-info')
const dialog = () => document.querySelector('[role="dialog"]')

describe('operators already on the canvas', () => {
  const node = (id, ref, kind = 'operator', version = 1) => ({ id, data: { definition: { id, kind, ref, operator_version: version } } })

  it('marks direct operator identities across providers and versions, not matching names or subflow refs', async () => {
    const catalog = [item('native', 'dataforge'), item('dataflow'), item('custom', 'custom'), item('unused')]
    palette(catalog)
    await wrapper.setProps({ nodes: [node('a', 'native', 'operator', 2), node('b', 'dataflow'), node('c', 'custom'), node('d', 'unused', 'subflow'), { id: 'unknown' }] })
    expect(wrapper.findAll('.is-in-canvas')).toHaveLength(3)
    expect(wrapper.findAll('.canvas-used-badge').map(badge => badge.text())).toEqual(['已在画布', '已在画布', '已在画布'])
    const unused = wrapper.findAll('.palette-entry').find(card => card.text().includes('unused'))
    expect(unused.classes()).not.toContain('is-in-canvas')
    expect(wrapper.emitted('add-item')).toBeUndefined()
  })

  it('updates after adding/removing/restoring nodes and still permits repeated insertion', async () => {
    palette()
    const first = node('one', 'HtmlEntityFilter'), second = node('two', 'HtmlEntityFilter')
    await wrapper.setProps({ nodes: [first, second] })
    expect(wrapper.get('.palette-entry').classes()).toContain('is-in-canvas')
    expect(wrapper.get('.entry-body').attributes('aria-label')).toContain('已在画布')
    await wrapper.get('.palette-entry').trigger('dblclick')
    await wrapper.get('.entry-body').trigger('keydown', { key: 'Enter' })
    await wrapper.get('.palette-entry').trigger('dragstart')
    expect(wrapper.emitted('add-item')).toHaveLength(2)
    expect(wrapper.emitted('drag-start')).toHaveLength(1)
    await wrapper.setProps({ nodes: [second] })
    expect(wrapper.find('.canvas-used-badge').exists()).toBe(true)
    await wrapper.setProps({ nodes: [] })
    expect(wrapper.find('.is-in-canvas').exists()).toBe(false)
    await wrapper.setProps({ nodes: [first] })
    expect(wrapper.find('.canvas-used-badge').exists()).toBe(true)
    await wrapper.get('input').setValue('no-match')
    expect(wrapper.find('.palette-entry').exists()).toBe(false)
    await wrapper.get('input').setValue('')
    expect(wrapper.find('.canvas-used-badge').exists()).toBe(true)
  })

  it('keeps compatibility and dependency restrictions separate from used highlighting', async () => {
    const catalog = [item('ready'), item('unready'), item('blocked')]
    palette(catalog)
    await wrapper.setProps({
      nodes: catalog.map(entry => node(entry.code, entry.code)), selectedNode: node('selected', 'ready'),
      candidateResults: catalog.map((entry, index) => ({ ...entry,
        compatibility: { compatible: index !== 2, reason: index === 2 ? '端口不兼容' : '', source_port: 'output', target_port: 'input' },
        runtime_status: index === 1 ? { status: 'missing', reason: '运行依赖未就绪' } : { status: 'ready' },
      })),
    })
    for (const state of ['compatible', 'unready', 'incompatible']) {
      expect(wrapper.get(`.compatibility-${state}`).classes()).toContain('is-in-canvas')
    }
    await wrapper.get('.compatibility-unready').trigger('dblclick')
    await wrapper.get('.compatibility-incompatible').trigger('dblclick')
    expect(wrapper.emitted('add-item')).toBeUndefined()
    expect(wrapper.text()).toContain('运行依赖未就绪')
    expect(wrapper.text()).toContain('端口不兼容')
  })
})

describe('operator help has no graph mutations', () => {
  it('keeps names only, reveals a two-line summary without a native title', async () => {
    palette()
    expect(wrapper.get('.palette-entry').text()).toContain('HtmlEntityFilter')
    expect(wrapper.get('.palette-entry').text()).not.toContain('整条文本')
    expect(wrapper.get('.palette-entry').text()).toContain('v1')
    expect(wrapper.findAll('.palette-entry [title]')).toHaveLength(0)
    await wrapper.get('.palette-entry').trigger('mouseenter'); await flushPromises()
    const tooltip = document.querySelector('[role="tooltip"]')
    expect(tooltip.textContent).toContain('整条文本')
    expect(tooltip.querySelector('.summary-text')).not.toBeNull()
    expect(wrapper.emitted('add-item')).toBeUndefined()
  })
  it('shows details on hover, pins on click and closes with Escape', async () => {
    palette()
    await info().trigger('mouseenter'); await flushPromises()
    expect(dialog().textContent).toContain('输入')
    expect(dialog().textContent).toContain('CPU')
    expect(document.querySelector('[role="tooltip"]')).toBeNull()
    await info().trigger('click'); await info().trigger('mouseleave')
    expect(dialog().textContent).toContain('已固定')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flushPromises()
    expect(dialog()).toBeNull()
    expect(wrapper.emitted('add-item')).toBeUndefined()
  })
  it('does not add or drag when interacting with the information button', async () => {
    palette()
    await info().trigger('click'); await info().trigger('dblclick'); await info().trigger('keydown', { key: 'Enter' }); await info().trigger('keydown', { key: ' ' }); await info().trigger('dragstart')
    expect(wrapper.emitted('add-item')).toBeUndefined()
    expect(wrapper.emitted('drag-start')).toBeUndefined()
    await wrapper.get('.entry-body').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('add-item')).toHaveLength(1)
  })
  it('keeps one pinned detail and switches only on another information click', async () => {
    palette([item(), { ...item('WatermarkFilter'), display_name_zh: '水印文本过滤器' }])
    const buttons = wrapper.findAll('.operator-info')
    await buttons[0].trigger('click'); await buttons[1].trigger('mouseenter')
    expect(dialog().textContent).toContain('HtmlEntityFilter')
    await buttons[1].trigger('click'); await flushPromises()
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1)
    expect(dialog().textContent).toContain('WatermarkFilter')
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true })); await flushPromises()
    expect(dialog()).toBeNull()
  })
  it('uses a body portal and safely renders custom descriptions as plain text', async () => {
    palette([{ ...item('custom', 'custom'), description: '<img src=x onerror=alert(1)>', summary: 'custom summary' }])
    await info().trigger('click'); await flushPromises()
    expect(dialog().parentElement).toBe(document.body)
    expect(dialog().querySelector('img')).toBeNull()
    expect(dialog().textContent).toContain('<img src=x onerror=alert(1)>')
    expect(dialog().style.position || '').toBe('') // positioning belongs to the scoped fixed CSS
    expect(dialog().style.left).toMatch(/px$/)
  })
  it('keeps detail open while crossing the hover gap and closes after leaving', async () => {
    vi.useFakeTimers(); palette()
    await info().trigger('mouseenter'); await info().trigger('mouseleave')
    dialog().dispatchEvent(new MouseEvent('mouseenter')); await vi.advanceTimersByTimeAsync(200)
    expect(dialog()).not.toBeNull()
    dialog().dispatchEvent(new MouseEvent('mouseleave')); await vi.advanceTimersByTimeAsync(200)
    expect(dialog()).toBeNull()
  })
  it('provides structured conditions and upstream evaluation selection', async () => {
    wrapper = mount(FilterRulesEditor, { props: { modelValue: [{ field: 'length', operator: 'ge', value: 1 }], evaluationNodes: [{ id: 'score', operator: 'Text2QASampleEvaluator', label: 'QA评估' }] } })
    await wrapper.get('[aria-label="条件1字段"]').setValue('question_quality')
    expect(wrapper.emitted('update:modelValue').at(-1)[0][0]).toMatchObject({ field: 'question_quality', evaluation_node: 'score', operator: 'ge', value: 4 })
  })
  it('separates generic evaluation and semantic marker node references', async () => {
    wrapper = mount(FilterRulesEditor, { props: {
      modelValue: [{ field: 'length', operator: 'ge', value: 1 }],
      evaluationNodes: [{ id: 'qa-score', operator: 'Text2QASampleEvaluator', label: 'QA评估' }, { id: 'generic-score', operator: 'PromptedEvaluator', label: '通用评估' }],
      deduplicationNodes: [{ id: 'semantic', operator: 'SemDeduplicateFilter', label: '语义标记' }],
    } })
    await wrapper.get('[aria-label="条件1字段"]').setValue('evaluation_score')
    expect(wrapper.emitted('update:modelValue').at(-1)[0][0]).toMatchObject({ evaluation_node: 'generic-score', value: 4 })
    await wrapper.setProps({ modelValue: wrapper.emitted('update:modelValue').at(-1)[0] })
    await wrapper.get('[aria-label="条件1字段"]').setValue('semantic_duplicate')
    expect(wrapper.emitted('update:modelValue').at(-1)[0][0]).toMatchObject({ deduplication_node: 'semantic', operator: 'eq', value: false })
  })
  it('does not intercept Escape when no help is open', async () => {
    palette()
    const target = document.createElement('button'), listener = vi.fn()
    document.body.append(target); target.addEventListener('keydown', listener)
    target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(listener).toHaveBeenCalledTimes(1)
  })
  it('replaces unpinned details when keyboard focus moves to another card', async () => {
    palette([item(), { ...item('WatermarkFilter'), display_name_zh: '水印文本过滤器', summary: '水印用途' }])
    await wrapper.findAll('.operator-info')[0].trigger('focus')
    expect(dialog()).not.toBeNull()
    await wrapper.findAll('.entry-body')[1].trigger('focus'); await flushPromises()
    expect(dialog()).toBeNull()
    expect(document.querySelector('[role="tooltip"]').textContent).toContain('水印用途')
  })
})

it('resolves source processing ports without weakening direct Sink checks', () => {
  const catalog = [
    { ...item('source', 'dataforge'), output_ports: { output: { artifact_type: 'source_chunk_set' } } },
    { ...item('filter'), input_ports: { input: { artifact_type: 'text_record_set', accepted_types: ['source_chunk_set', 'derived_text_set', 'candidate:text'] } }, output_ports: { output: { artifact_type: 'text_record_set', output_by_input: { source_chunk_set: 'derived_text_set', derived_text_set: 'derived_text_set', 'candidate:text': 'candidate:text' } } } },
    { ...item('mapper', 'dataforge'), input_ports: { input: { artifact_type: 'source_chunk_set', accepted_types: ['source_chunk_set', 'derived_text_set'] } } },
  ]
  const nodes = catalog.map(entry => makeCanvasNode({ id: entry.code, kind: 'operator', ref: entry.code }, { x: 0, y: 0 }, catalog))
  nodes.push(makeCanvasNode({ id: 'sink', kind: 'knowledge_sink', knowledge_type: 'text' }, { x: 0, y: 0 }, catalog))
  const edges = [{ id: 'one', source: 'source', target: 'filter' }]
  expect(checkEdgeCompatibility({ nodes, edges, sourceNodeId: 'filter', targetNodeId: 'mapper' }).allowed).toBe(true)
  expect(checkEdgeCompatibility({ nodes, edges, sourceNodeId: 'filter', targetNodeId: 'sink' }).allowed).toBe(false)
})

it('shows compatible, runtime-unready and incompatible operators without hiding reasons', async () => {
  const catalog = [item('ready'), item('unready'), item('blocked')]
  const candidateResults = [
    { ...catalog[0], compatibility: { compatible: true, direction: 'downstream', source_port: 'output', target_port: 'input' }, runtime_status: { status: 'ready' } },
    { ...catalog[1], compatibility: { compatible: true, direction: 'downstream', source_port: 'output', target_port: 'input' }, runtime_status: { status: 'missing', reason: 'Runner 缺少资源' } },
    { ...catalog[2], compatibility: { compatible: false, direction: 'downstream', reason_code: 'PORT_TYPE_MISMATCH', reason: '端口数据类型不兼容：candidate:qa → candidate:text' }, runtime_status: { status: 'ready' } },
  ]
  const selectedNode = { id: 'qa', data: { meta: { name: '问答生成器' } } }
  wrapper = mount(OperatorPalette, { props: { catalog, candidateResults, selectedNode, direction: 'downstream', outputTypes: ['qa'] }, attachTo: document.body })

  expect(wrapper.text()).toContain('当前节点：问答生成器')
  expect(wrapper.findAll('.compatibility-compatible')).toHaveLength(1)
  expect(wrapper.findAll('.compatibility-unready')).toHaveLength(1)
  expect(wrapper.findAll('.compatibility-incompatible')).toHaveLength(1)
  expect(wrapper.text()).toContain('Runner 缺少资源')
  expect(wrapper.text()).toContain('端口数据类型不兼容')
  await wrapper.find('.compatibility-compatible').trigger('dblclick')
  await wrapper.find('.compatibility-incompatible').trigger('dblclick')
  expect(wrapper.emitted('add-item')).toHaveLength(1)
  await wrapper.get('[role="tab"][aria-selected="false"]').trigger('click')
  expect(wrapper.emitted('change-direction')[0]).toEqual(['upstream'])
})
