import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import OperatorCatalogView from '../OperatorCatalogView.vue'
import { api } from '../../../api/platform'
import { dataflowOperators } from '../../../components/flow/__tests__/flowFixtures'

vi.mock('../../../api/platform', () => ({ api: {
  operatorCatalog: vi.fn(), operatorCatalogFacets: vi.fn(),
} }))

let wrapper
afterEach(() => wrapper?.unmount())

it('shows upstream class names and Chinese names for all renamed DataFlow operators', async () => {
  api.operatorCatalog.mockResolvedValue(dataflowOperators)
  api.operatorCatalogFacets.mockResolvedValue({ categories: [], knowledge_types: [], statuses: [] })
  wrapper = mount(OperatorCatalogView, { global: { stubs: { OperatorInspector: true, OperatorPluginManager: true } } })
  await flushPromises()
  for (const item of dataflowOperators) {
    const row = wrapper.findAll('.operator-row').find(candidate => candidate.text().includes(item.code))
    expect(row.text()).toContain(item.display_name_zh)
    expect(row.get('.operator-bilingual').text()).toContain(item.code)
    expect(row.get('.operator-bilingual').text().split(item.code)).toHaveLength(2)
  }
})

it('groups visible operators by facet order with matched counts and source badges', async () => {
  api.operatorCatalog.mockResolvedValue([
    { id: 'knowledge', code: 'knowledge', name: 'Knowledge', display_name_zh: '知识生成器', source: 'dataflow', version: 1, category: 'knowledge-generation', exposure: 'public', dependency_status: { status: 'ready' } },
    { id: 'content-native', code: 'content-native', name: 'Native Content', display_name_zh: '原生内容处理', source: 'dataforge', version: 1, category: 'content-processing', exposure: 'public', dependency_status: { status: 'ready' } },
    { id: 'content-custom', code: 'content-custom', name: 'Custom Content', display_name_zh: '自定义内容处理', source: 'custom', version: 1, category: 'content-processing', exposure: 'public', dependency_status: { status: 'ready' } },
    { id: 'future', code: 'future', name: 'Future', display_name_zh: '未来算子', source: 'custom', version: 1, category: 'future-category', exposure: 'public', dependency_status: { status: 'ready' } },
  ])
  api.operatorCatalogFacets.mockResolvedValue({
    categories: [{ name: 'content-processing', count: 2 }, { name: 'quality-processing', count: 0 }, { name: 'knowledge-generation', count: 1 }],
    knowledge_types: [], statuses: [],
  })
  wrapper = mount(OperatorCatalogView, { global: { stubs: { OperatorInspector: true, OperatorPluginManager: true } } })
  await flushPromises()

  const groups = wrapper.findAll('.operator-category-group')
  expect(groups.map(group => group.get('h3').text())).toEqual(['内容处理', '知识生成', 'future-category'])
  expect(groups.map(group => group.get('.category-count').text())).toEqual(['2', '1', '1'])
  expect(groups[0].findAll('.source-badge').map(badge => badge.text())).toEqual(['DataForge', '自定义'])
  expect(wrapper.text()).not.toContain('质量处理')
  expect(groups[0].findAll('.operator-row')[0].get('.operator-meta').text()).not.toContain('内容处理')
})

it('regroups after filters and falls back to the first visible inspector item', async () => {
  const items = [
    { id: 'alpha', code: 'alpha', name: 'Alpha', display_name_zh: '甲算子', summary: 'alpha summary', source: 'dataforge', version: 1, category: 'content-processing', knowledge_types: ['text'], exposure: 'public', status: 'published', dependency_status: { status: 'ready' } },
    { id: 'beta', code: 'beta', name: 'Beta', display_name_zh: '乙算子', summary: 'beta summary', source: 'dataflow', version: 1, category: 'knowledge-generation', knowledge_types: ['qa'], exposure: 'controlled', status: 'published', dependency_status: { status: 'ready' } },
    { id: 'gamma', code: 'gamma', name: 'Gamma', display_name_zh: '丙算子', summary: 'gamma summary', source: 'custom', version: 1, category: 'quality-processing', knowledge_types: ['text'], exposure: 'public', status: 'draft', dependency_status: { status: 'ready' } },
  ]
  api.operatorCatalog.mockResolvedValue(items)
  api.operatorCatalogFacets.mockResolvedValue({
    categories: ['content-processing', 'knowledge-generation', 'quality-processing'].map(name => ({ name, count: 1 })),
    knowledge_types: ['qa', 'text'], statuses: ['draft', 'published'],
  })
  const InspectorStub = { props: ['operator'], template: '<div class="inspector-stub">{{ operator && operator.id }}</div>' }
  wrapper = mount(OperatorCatalogView, { global: { stubs: { OperatorInspector: InspectorStub, OperatorPluginManager: true } } })
  await flushPromises()

  await wrapper.findAll('.operator-row')[2].trigger('click')
  expect(wrapper.get('.inspector-stub').text()).toBe('gamma')

  await wrapper.get('input').setValue('alpha')
  expect(wrapper.findAll('.operator-category-group').map(group => group.get('h3').text())).toEqual(['内容处理'])
  expect(wrapper.get('.category-count').text()).toBe('1')
  expect(wrapper.get('.inspector-stub').text()).toBe('alpha')

  await wrapper.get('input').setValue('')
  const filters = wrapper.findAll('.catalog-filters select')
  await filters[0].setValue('knowledge-generation')
  expect(wrapper.findAll('.operator-category-group').map(group => group.get('h3').text())).toEqual(['知识生成'])
  await filters[0].setValue('')
  await filters[1].setValue('qa')
  expect(wrapper.findAll('.operator-row')).toHaveLength(1)
  expect(wrapper.get('.operator-row').text()).toContain('乙算子')
  await filters[1].setValue('')
  await filters[2].setValue('controlled')
  expect(wrapper.get('.operator-row').text()).toContain('乙算子')
  await filters[2].setValue('')
  await filters[3].setValue('draft')
  expect(wrapper.get('.operator-row').text()).toContain('丙算子')

  await wrapper.get('input').setValue('no match')
  expect(wrapper.findAll('.operator-category-group')).toHaveLength(0)
  expect(wrapper.get('.empty-catalog').text()).toBe('没有匹配的算子。')
  expect(wrapper.get('.inspector-stub').text()).toBe('')
})

it('distinguishes operator sources without changing exposure badges', async () => {
  api.operatorCatalog.mockResolvedValue(['dataforge', 'dataflow', 'custom'].map(source => ({
    id: source, code: `${source}-operator`, name: `${source} operator`, source,
    catalog_group: source === 'custom' ? 'custom' : source === 'dataflow' ? 'dataflow_featured' : 'dataforge',
    version: 1, category: source === 'custom' ? 'quality-processing' : 'knowledge-generation', exposure: 'public', dependency_status: { status: 'ready' },
  })))
  api.operatorCatalogFacets.mockResolvedValue({ categories: [], knowledge_types: [], statuses: [] })
  wrapper = mount(OperatorCatalogView, { global: { stubs: { OperatorInspector: true, OperatorPluginManager: true } } })
  await flushPromises()

  const badges = wrapper.findAll('.source-badge')
  expect(badges.map(badge => badge.text())).toEqual(['DataForge', 'DataFlow', '自定义'])
  expect(badges.map(badge => badge.classes().includes('source-dataflow'))).toEqual([false, true, false])
  expect(badges.map(badge => badge.classes().includes('blue'))).toEqual([true, false, false])
  expect(wrapper.findAll('.operator-row > .badge').map(badge => badge.text())).toEqual(Array(3).fill('可直接使用'))
})

it('contains no retired-operator presentation path', async () => {
  api.operatorCatalog.mockResolvedValue(dataflowOperators)
  api.operatorCatalogFacets.mockResolvedValue({ categories: [], knowledge_types: [], statuses: [] })
  wrapper = mount(OperatorCatalogView, { global: { stubs: { OperatorInspector: true, OperatorPluginManager: true } } })
  await flushPromises()
  expect(wrapper.find('.retired-toggle').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('已退出新编排')
})
