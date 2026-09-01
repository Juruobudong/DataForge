import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import KnowledgeScopePanel from './KnowledgeScopePanel.vue'

const libraries = [
  { id: 'a', name: '知识库 A' },
  { id: 'b', name: '知识库 B' },
  { id: 'c', name: '知识库 C' },
]

describe('KnowledgeScopePanel', () => {
  it('places selected libraries first in priority order and emits drag reorder details', async () => {
    const wrapper = mount(KnowledgeScopePanel, { props: { libraries, chosen: ['b', 'a'] } })
    expect(wrapper.findAll('[data-priority-library]').map(item => item.attributes('data-priority-library'))).toEqual(['b', 'a'])
    expect(wrapper.findAll('[data-available-library]').map(item => item.attributes('data-available-library'))).toEqual(['c'])

    const dataTransfer = { effectAllowed: '', setData: vi.fn() }
    const source = wrapper.get('[data-priority-library="b"]')
    const target = wrapper.get('[data-priority-library="a"]')
    target.element.getBoundingClientRect = () => ({ top: 0, height: 20 })
    await source.trigger('dragstart', { dataTransfer })
    await target.trigger('dragover', { dataTransfer, clientY: 16 })
    await target.trigger('drop', { dataTransfer, clientY: 16 })

    expect(dataTransfer.effectAllowed).toBe('move')
    expect(dataTransfer.setData).toHaveBeenCalledWith('text/plain', 'b')
    expect(wrapper.emitted('reorder')[0]).toEqual([{ id: 'b', targetId: 'a', after: true }])
  })

  it('retains checkbox selection and move-button alternatives', async () => {
    const wrapper = mount(KnowledgeScopePanel, { props: { libraries, chosen: ['b', 'a'] } })
    await wrapper.get('[data-available-library="c"] input').trigger('change')
    await wrapper.get('[data-priority-library="a"] button').trigger('click')

    expect(wrapper.emitted('toggle')[0]).toEqual(['c'])
    expect(wrapper.emitted('move')[0]).toEqual(['a', -1])
  })
})
