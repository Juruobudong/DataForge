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
  for (const [index, row] of wrapper.findAll('.operator-row').entries()) {
    expect(row.text()).toContain(dataflowOperators[index].display_name_zh)
    expect(row.get('.operator-bilingual').text()).toContain(`${dataflowOperators[index].code}`)
    expect(row.get('.operator-bilingual').text().split(dataflowOperators[index].code)).toHaveLength(2)
  }
})

it('distinguishes operator sources without changing exposure badges', async () => {
  api.operatorCatalog.mockResolvedValue(['dataforge', 'dataflow', 'custom'].map(source => ({
    id: source, code: `${source}-operator`, name: `${source} operator`, source,
    catalog_group: source === 'custom' ? 'extension' : source === 'dataflow' ? 'dataflow_featured' : 'dataforge',
    version: 1, category: source === 'custom' ? 'extension' : 'knowledge-generation', exposure: 'public', dependency_status: { status: 'ready' },
  })))
  api.operatorCatalogFacets.mockResolvedValue({ categories: [], knowledge_types: [], statuses: [] })
  wrapper = mount(OperatorCatalogView, { global: { stubs: { OperatorInspector: true, OperatorPluginManager: true } } })
  await flushPromises()

  const badges = wrapper.findAll('.source-badge')
  expect(badges.map(badge => badge.text())).toEqual(['DataForge', 'DataFlow', '扩展'])
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
