import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ChunkCard from './ChunkCard.vue'

const chunk = (overrides = {}) => ({
  id: 'chunk-1',
  chunk_index: 0,
  content: '第一行\n第二行\n第三行\n第四行\n第五行\n第六行\n第七行',
  review_status: 'approved',
  anchor: { page: 1 },
  ...overrides,
})

it('shows six-line previews by default and expands only the focused card', async () => {
  const first = mount(ChunkCard, { props: { chunk: chunk(), focused: false } })
  const second = mount(ChunkCard, { props: { chunk: chunk({ id: 'chunk-2', chunk_index: 1 }), focused: false } })

  expect(first.get('.chunk-content').classes()).not.toContain('expanded')
  expect(second.get('.chunk-content').classes()).not.toContain('expanded')

  await first.setProps({ focused: true })
  expect(first.get('.chunk-content').classes()).toContain('expanded')
  expect(second.get('.chunk-content').classes()).not.toContain('expanded')

  await first.setProps({ focused: false })
  await second.setProps({ focused: true })
  expect(first.get('.chunk-content').classes()).not.toContain('expanded')
  expect(second.get('.chunk-content').classes()).toContain('expanded')
})

it('emits focus for mouse and keyboard entry without changing action-button behavior', async () => {
  const wrapper = mount(ChunkCard, { props: { chunk: chunk(), focused: false } })

  await wrapper.get('article').trigger('click')
  await wrapper.get('article').trigger('keydown', { key: 'Enter' })
  await wrapper.get('article').trigger('keydown', { key: ' ' })
  expect(wrapper.emitted('focus')).toHaveLength(3)

  await wrapper.get('footer button').trigger('click')
  expect(wrapper.emitted('reopen')).toHaveLength(1)
  expect(wrapper.emitted('focus')).toHaveLength(3)
})

it('keeps editable content complete and preserves click-to-edit', async () => {
  const editable = chunk({ review_status: 'pending_review' })
  const wrapper = mount(ChunkCard, { props: { chunk: editable, focused: false, editing: false, editContent: editable.content } })

  await wrapper.get('.chunk-content').trigger('click')
  expect(wrapper.emitted('edit')).toHaveLength(1)
  expect(wrapper.emitted('focus')).toHaveLength(1)

  await wrapper.setProps({ editing: true })
  expect(wrapper.find('.chunk-content').exists()).toBe(false)
  expect(wrapper.get('textarea').element.value).toBe(editable.content)
})
