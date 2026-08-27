import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import TemplateListView from '../TemplateListView.vue'
import { api } from '../../../api/platform'
import { managedTemplates, modelServings, qualityProfiles, standardTemplate } from '../../../components/flow/__tests__/flowFixtures'

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => router, useRoute: () => ({ query: {} }) }))
vi.mock('../../../api/platform', () => ({ api: Object.fromEntries([
  'flowTemplates', 'operatorCatalog', 'flowSubgraphs', 'knowledgeTypes', 'managedFlowTemplates',
  'modelServings', 'qualityProfiles', 'detachFlowToAdvanced', 'updateFlowTemplate',
].map(name => [name, vi.fn()])) }))
vi.mock('../../../components/flow/advanced/AdvancedFlowEditor.vue', () => ({ default: {
  template: '<div class="advanced-editor-test"></div>',
  methods: { loadDefinition() {}, reset() {}, serialize() { return { schema_version: 3, nodes: [], edges: [] } } },
} }))

let wrapper
const advanced = { id: 'custom', code: 'custom', name: '自定义高级', authoring_mode: 'advanced', output_types: ['text'],
  definition: { schema_version: 3, nodes: [], edges: [] }, revision: 1 }
beforeEach(() => {
  api.flowTemplates.mockResolvedValue([standardTemplate(), advanced])
  for (const name of ['operatorCatalog', 'flowSubgraphs', 'knowledgeTypes']) api[name].mockResolvedValue([])
  api.managedFlowTemplates.mockResolvedValue(managedTemplates)
  api.modelServings.mockResolvedValue(modelServings)
  api.qualityProfiles.mockResolvedValue(qualityProfiles)
  api.detachFlowToAdvanced.mockResolvedValue(advanced)
  api.updateFlowTemplate.mockResolvedValue({ revision: 2, status: 'draft' })
})
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })
async function render() {
  wrapper = mount(TemplateListView, { attachTo: document.body })
  await flushPromises()
}
const button = text => wrapper.findAll('button').find(item => item.text() === text)

describe('knowledge flow editing toolbar', () => {
  it.each(['取消', '放弃修改', '保存后前往'])('handles %s before leaving a dirty flow for a subflow', async choice => {
    await render()
    await wrapper.findAll('.template-list button')[1].trigger('click')
    await button('流程设置').trigger('click')
    await wrapper.findAll('.template-settings input')[1].setValue('修改后')
    wrapper.findComponent('.advanced-editor-test').vm.$emit('open-subflow', { id: 'asset', revision: 1 })
    await flushPromises()
    await button(choice).trigger('click'); await flushPromises()
    if (choice === '取消') expect(router.push).not.toHaveBeenCalled()
    else expect(router.push).toHaveBeenCalledWith(expect.objectContaining({ path: '/developer/flow-templates/subgraphs/asset/revisions/1' }))
    expect(api.updateFlowTemplate).toHaveBeenCalledTimes(choice === '保存后前往' ? 1 : 0)
  })

  it('stays in the editor when save-before-navigation fails', async () => {
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')
    await button('流程设置').trigger('click'); await wrapper.findAll('.template-settings input')[1].setValue('修改后')
    wrapper.findComponent('.advanced-editor-test').vm.$emit('open-subflow', { id: 'asset', revision: 1 })
    api.updateFlowTemplate.mockRejectedValueOnce(new Error('保存失败'))
    await flushPromises(); await button('保存后前往').trigger('click'); await flushPromises()
    expect(router.push).not.toHaveBeenCalled()
    expect(wrapper.get('[role="dialog"]').text()).toContain('保存失败')
  })
  it('orders conversion, help and exit outside the header, then keeps Advanced exit', async () => {
    await render()
    await wrapper.findAll('.template-list button')[0].trigger('click')
    await flushPromises()
    expect(wrapper.get('.header-actions').text()).not.toContain('退出编辑')
    expect(wrapper.get('.authoring-mode-actions').findAll('button').map(item => item.text())).toEqual(['转换为高级编排', 'i', '‹ 退出编辑'])
    const help = wrapper.get('[aria-label="高级编排转换说明"]')
    help.element.focus()
    await flushPromises()
    expect(wrapper.get('[role="status"]').text()).toContain('原标准流程不会被修改')
    await button('转换为高级编排').trigger('click')
    await flushPromises()
    expect(api.detachFlowToAdvanced).toHaveBeenCalledWith('standard-text')
    expect(wrapper.get('.authoring-mode-actions').text()).toContain('高级编排 · Authoring DAG')
    expect(wrapper.get('.authoring-mode-actions').findAll('button').map(item => item.text())).toEqual(['‹ 退出编辑'])
    await button('‹ 退出编辑').trigger('click')
    expect(wrapper.find('.flow-toolbar').exists()).toBe(false)
  })

  it.each([0, 1])('preserves dirty confirm/cancel when exiting template %i', async index => {
    await render()
    await wrapper.findAll('.template-list button')[index].trigger('click')
    await button('流程设置').trigger('click')
    await wrapper.findAll('.template-settings input')[1].setValue('已修改名称')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    await button('‹ 退出编辑').trigger('click')
    expect(confirm).toHaveBeenCalledWith('当前画布有未保存修改，确定退出编辑吗？')
    expect(wrapper.find('.flow-toolbar').exists()).toBe(true)
    await button('‹ 退出编辑').trigger('click')
    expect(wrapper.find('.flow-toolbar').exists()).toBe(false)
  })

  it('new unsaved Standard draft renders the selected managed stages', async () => {
    await render()
    await button('＋ 新建知识流程').trigger('click')
    await wrapper.findAll('.goal-grid button')[1].trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.business-stage')).toHaveLength(4)
    expect(wrapper.find('.serving-selector').exists()).toBe(true)
    expect(wrapper.get('.authoring-mode-actions').text()).not.toContain('转换为高级编排')
    expect(button('‹ 退出编辑')).toBeDefined()
  })
})
