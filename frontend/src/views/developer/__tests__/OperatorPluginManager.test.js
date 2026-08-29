import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import OperatorPluginManager from '../../../components/flow/OperatorPluginManager.vue'
import { api } from '../../../api/platform'

vi.mock('../../../api/platform', () => ({ api: {
  operatorPlugins: vi.fn(), registerOperatorPlugin: vi.fn(), validateOperatorPlugin: vi.fn(),
  operatorValidation: vi.fn(), publishOperatorPlugin: vi.fn(),
} }))

let wrapper
afterEach(() => wrapper?.unmount())

it('uses the custom-operator product name for the manager and empty state', async () => {
  api.operatorPlugins.mockResolvedValue([])
  wrapper = mount(OperatorPluginManager)
  await flushPromises()

  expect(wrapper.get('h3').text()).toBe('自定义算子')
  expect(wrapper.text()).toContain('尚无自定义算子。')
  expect(wrapper.text()).not.toContain('扩展算子')
})
