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

it('distinguishes operator providers with labelled badges without changing exposure badges', async () => {
  api.operatorCatalog.mockResolvedValue(['dataforge', 'dataflow', 'custom'].map(provider => ({
    id: provider, code: `${provider}-operator`, name: `${provider} operator`, provider,
    version: 1, category: '知识生成', exposure: 'public', dependency_status: { status: 'ready' },
  })))
  api.operatorCatalogFacets.mockResolvedValue({ categories: [], knowledge_types: [], statuses: [] })
  wrapper = mount(OperatorCatalogView, { global: { stubs: { OperatorInspector: true, OperatorPluginManager: true } } })
  await flushPromises()

  const badges = wrapper.findAll('.provider-badge')
  expect(badges.map(badge => badge.text())).toEqual(['DataForge', 'DataFlow', 'Custom'])
  expect(badges.map(badge => badge.classes().includes('provider-dataflow'))).toEqual([false, true, false])
  expect(badges.map(badge => badge.classes().includes('blue'))).toEqual([true, false, false])
  expect(wrapper.findAll('.operator-row > .badge').map(badge => badge.text())).toEqual(Array(3).fill('可直接使用'))
})

const retiredOperator = {
  id: 'knowledge-diff', code: 'knowledge-diff', name: 'Knowledge Diff', display_name_zh: '知识差异',
  category: '质量治理', exposure: 'internal', status: 'deprecated', version: 1,
}
const currentOperator = {
  id: 'schema-validator', code: 'schema-validator', name: 'Schema Validator', display_name_zh: '图谱结构校验器',
  category: '质量治理', exposure: 'public', status: 'published', version: 1,
}

async function mountRetiredCatalog(items = [retiredOperator, currentOperator]) {
  api.operatorCatalog.mockResolvedValue(items)
  api.operatorCatalogFacets.mockResolvedValue({ categories: [{ name: '质量治理', count: items.length }], knowledge_types: [], statuses: ['published', 'deprecated'] })
  wrapper = mount(OperatorCatalogView, { global: { stubs: { OperatorInspector: true, OperatorPluginManager: true } } })
  await flushPromises()
}

it('places retired operators last and collapses them by default without hiding active internal or disabled operators', async () => {
  const internal = { ...currentOperator, id: 'internal', exposure: 'internal' }
  const disabled = { ...currentOperator, id: 'disabled', exposure: 'disabled', status: 'deprecated' }
  await mountRetiredCatalog([retiredOperator, currentOperator, internal, disabled])

  expect(wrapper.findAll('.operator-row')).toHaveLength(3)
  expect(wrapper.get('.catalog-list').element.lastElementChild.className).toBe('retired-operators')
  expect(wrapper.get('.retired-toggle').attributes('aria-expanded')).toBe('false')
  expect(wrapper.get('.retired-toggle').text()).toContain('已退出新编排')
  expect(wrapper.get('.retired-toggle .badge').text()).toBe('1')
  expect(wrapper.find('#retired-operator-list').exists()).toBe(false)
  expect(wrapper.findComponent({ name: 'OperatorInspector' }).props('operator')).toEqual(currentOperator)

  await wrapper.get('.retired-toggle').trigger('click')
  expect(wrapper.findAll('.operator-row').map(row => row.text().includes('Knowledge Diff'))).toEqual([false, false, false, true])
  expect(wrapper.get('.retired-toggle').attributes('aria-expanded')).toBe('true')
  await wrapper.get('#retired-operator-list .operator-row').trigger('click')
  expect(wrapper.findComponent({ name: 'OperatorInspector' }).props('operator')).toEqual(retiredOperator)

  await wrapper.get('.retired-toggle').trigger('click')
  expect(wrapper.find('#retired-operator-list').exists()).toBe(false)
  expect(wrapper.findComponent({ name: 'OperatorInspector' }).props('operator')).toEqual(currentOperator)
})

it('filters retired entries without expanding them or displaying their details while collapsed', async () => {
  await mountRetiredCatalog()
  await wrapper.get('input').setValue('Knowledge Diff')
  expect(wrapper.findAll('.operator-row')).toHaveLength(0)
  expect(wrapper.get('.retired-toggle .badge').text()).toBe('1')
  expect(wrapper.find('.empty-catalog').exists()).toBe(false)
  expect(wrapper.findComponent({ name: 'OperatorInspector' }).props('operator')).toBeUndefined()
  await wrapper.get('.retired-toggle').trigger('click')
  expect(wrapper.get('#retired-operator-list').text()).toContain('知识差异')

  await wrapper.get('input').setValue('不存在的算子')
  expect(wrapper.find('.retired-operators').exists()).toBe(false)
  expect(wrapper.get('.empty-catalog').text()).toBe('没有匹配的算子。')
  expect(wrapper.findComponent({ name: 'OperatorInspector' }).props('operator')).toBeUndefined()

  await wrapper.get('input').setValue('')
  await wrapper.get('.catalog-filters select').setValue('质量治理')
  await wrapper.findAll('.catalog-filters select')[3].setValue('published')
  expect(wrapper.find('.retired-operators').exists()).toBe(false)
  expect(wrapper.findAll('.operator-row')).toHaveLength(1)
})

it('starts collapsed again when the catalog page is reopened', async () => {
  await mountRetiredCatalog([retiredOperator])
  await wrapper.get('.retired-toggle').trigger('click')
  expect(wrapper.findAll('.operator-row')).toHaveLength(1)
  wrapper.unmount()
  await mountRetiredCatalog([retiredOperator])
  expect(wrapper.get('.retired-toggle').attributes('aria-expanded')).toBe('false')
  expect(wrapper.findAll('.operator-row')).toHaveLength(0)
})
