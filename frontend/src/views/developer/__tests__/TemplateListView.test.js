import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import TemplateListView from '../TemplateListView.vue'
import { api } from '../../../api/platform'
import { managedTemplates, modelServings, qualityProfiles, standardTemplate } from '../../../components/flow/__tests__/flowFixtures'

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => router, useRoute: () => ({ query: {} }) }))
vi.mock('../../../api/platform', () => ({ api: Object.fromEntries([
  'flowTemplates', 'operatorCatalog', 'flowSubgraphs', 'knowledgeTypes', 'managedFlowTemplates',
  'modelServings', 'qualityProfiles', 'detachFlowToAdvanced', 'previewFlowToAdvanced', 'materializeManagedFlow', 'createFlowTemplate', 'updateFlowTemplate', 'publishFlowTemplate',
  'validateFlowTemplate',
].map(name => [name, vi.fn()])) }))
vi.mock('../../../components/flow/advanced/AdvancedFlowEditor.vue', () => ({ default: {
  template: '<div class="advanced-editor-test"></div>',
  data: () => ({ definition: { schema_version: 3, nodes: [], edges: [] } }),
  methods: { loadDefinition(value) { this.definition = value }, reset() {}, validate() { return true }, serialize() { return this.definition }, applyNormalizedDefinition(value) { this.definition = value } },
} }))

let wrapper
const advanced = { id: 'custom', code: 'custom', name: '自定义高级', authoring_mode: 'advanced', output_types: ['text'],
  definition: { schema_version: 3, nodes: [], edges: [] }, revision: 1, revision_id: 'r1', source_definition_checksum: 'checksum1' }
const conversionPreview = { code: 'custom-converted', name: '转换后的高级流程', output_types: ['text'],
  definition: advanced.definition, source_template_id: 'standard-text', source_revision_id: 'standard-text-r1' }
beforeEach(() => {
  api.flowTemplates.mockResolvedValue([standardTemplate(), advanced])
  for (const name of ['operatorCatalog', 'flowSubgraphs', 'knowledgeTypes']) api[name].mockResolvedValue([])
  api.managedFlowTemplates.mockResolvedValue(managedTemplates)
  api.modelServings.mockResolvedValue(modelServings)
  api.qualityProfiles.mockResolvedValue(qualityProfiles)
  api.detachFlowToAdvanced.mockResolvedValue(advanced)
  api.previewFlowToAdvanced.mockResolvedValue(conversionPreview)
  api.materializeManagedFlow.mockResolvedValue({ output_types: ['text'], definition: advanced.definition })
  api.createFlowTemplate.mockResolvedValue({ id: 'converted', revision: 1, status: 'draft' })
  api.updateFlowTemplate.mockResolvedValue({ revision: 2, revision_id: 'r2', source_definition_checksum: 'checksum2', status: 'draft' })
  api.validateFlowTemplate.mockResolvedValue({ valid: true, compiled_checksum: 'compiled' })
})
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = ''; delete HTMLElement.prototype.scrollIntoView; vi.unstubAllGlobals(); vi.useRealTimers() })
async function render() {
  wrapper = mount(TemplateListView, { attachTo: document.body })
  await flushPromises()
}
const button = text => wrapper.findAll('button').find(item => item.text() === text)

describe('knowledge flow draft and published versions', () => {
  const states = [
    { revision: 1, revision_status: 'draft', published_revision: null, expected: '最新草稿：r1 · 已发布版本：未发布' },
    { revision: 1, revision_status: 'published', published_revision: 1, expected: '最新草稿：无 · 已发布版本：r1' },
    { revision: 2, revision_status: 'draft', published_revision: 1, expected: '最新草稿：r2 · 已发布版本：r1' },
  ]
  const flows = [
    { label: 'builtin Standard', template: standardTemplate() },
    { label: 'custom Standard', template: { ...standardTemplate(), id: 'custom-standard', code: 'custom-standard', is_builtin: false } },
    { label: 'custom Advanced', template: advanced },
  ]
  it.each(flows.flatMap(flow => states.map(state => ({ ...flow, state, expected: state.expected }))))(
    'shows $expected on the $label card and editor', async ({ template, state, expected }) => {
      api.flowTemplates.mockResolvedValue([{ ...template, ...state }])
      await render()
      expect(wrapper.get('.template-list .template-revisions').text()).toBe(expected)
      await wrapper.get('.template-list button').trigger('click')
      expect(wrapper.get('.template-page-head .template-revisions').text()).toBe(expected)
    },
  )

  it('preserves the published version when saving and refreshes both versions after publishing', async () => {
    vi.useFakeTimers()
    const published = { ...advanced, revision: 1, revision_status: 'published', published_revision: 1 }
    api.flowTemplates.mockResolvedValue([published])
    await render(); await wrapper.get('.template-list button').trigger('click')
    wrapper.findComponent('.advanced-editor-test').vm.$emit('dirty')
    await flushPromises()
    expect(wrapper.get('.save-state').text()).toContain('未保存')
    expect(wrapper.get('.template-page-head .template-revisions').text()).toBe('最新草稿：无 · 已发布版本：r1')
    await button('保存草稿').trigger('click'); await flushPromises()
    expect(wrapper.get('.template-page-head .template-revisions').text()).toBe('最新草稿：r2 · 已发布版本：r1')
    await button('‹ 退出编辑').trigger('click')
    expect(wrapper.get('.template-list .template-revisions').text()).toBe('最新草稿：r2 · 已发布版本：r1')
    await wrapper.get('.template-list button').trigger('click')

    let finishPublish
    api.publishFlowTemplate.mockImplementationOnce(() => new Promise(resolve => { finishPublish = resolve }))
    api.flowTemplates.mockResolvedValue([{ ...published, revision: 2, published_revision: 2 }])
    await button('发布').trigger('click')
    expect(api.publishFlowTemplate).toHaveBeenCalledWith(advanced.id, { revision_id: 'r2', expected_definition_checksum: 'checksum2' })
    expect(wrapper.find('.publish-notice').exists()).toBe(false)
    expect(wrapper.get('.template-page-head .template-revisions').text()).toBe('最新草稿：r2 · 已发布版本：r1')
    finishPublish({ id: advanced.id, revision: 2, status: 'published' })
    await flushPromises()
    expect(wrapper.get('.publish-notice').text()).toContain('发布成功：自定义高级 · r2')
    expect(wrapper.get('.template-page-head .template-revisions').text()).toBe('最新草稿：无 · 已发布版本：r2')
    await button('‹ 退出编辑').trigger('click')
    expect(wrapper.find('.publish-notice').exists()).toBe(false)
    expect(wrapper.get('.template-list .template-revisions').text()).toBe('最新草稿：无 · 已发布版本：r2')
  })

  it.each(['save', 'publish'])('keeps the confirmed versions after a failed %s', async operation => {
    vi.useFakeTimers()
    const state = operation === 'save' ? states[1] : states[2]
    api.flowTemplates.mockResolvedValue([{ ...advanced, ...state }])
    await render(); await wrapper.get('.template-list button').trigger('click')
    if (operation === 'save') {
      wrapper.findComponent('.advanced-editor-test').vm.$emit('dirty')
      api.updateFlowTemplate.mockRejectedValueOnce(new Error('保存失败'))
      await button('保存草稿').trigger('click')
    } else {
      api.publishFlowTemplate.mockRejectedValueOnce(new Error('发布失败'))
      await button('发布').trigger('click')
    }
    await flushPromises()
    expect(wrapper.text()).toContain(operation === 'save' ? '保存失败' : '发布失败')
    expect(wrapper.find('.publish-notice').exists()).toBe(false)
    expect(wrapper.get('.template-page-head .template-revisions').text()).toBe(state.expected)
    vi.spyOn(window, 'confirm').mockReturnValueOnce(true)
    await button('‹ 退出编辑').trigger('click')
    expect(wrapper.get('.template-list .template-revisions').text()).toBe(state.expected)
  })
})

describe('new knowledge flow identity', () => {
  async function startNew(index) {
    await button('＋ 新建知识流程').trigger('click')
    if (index === 'advanced') await wrapper.get('.advanced-choice button').trigger('click')
    else await wrapper.findAll('.goal-grid button')[index].trigger('click')
    await flushPromises()
  }

  it.each(managedTemplates.map((item, index) => ({ item, index })))(
    'prefills the $item.name Standard identity',
    async ({ item, index }) => {
      await render(); await startNew(index)
      const flowName = item.name.endsWith('流程') ? item.name : `${item.name}流程`
      expect(wrapper.get('#template-code').element.value).toBe(`custom-${item.code}`)
      expect(wrapper.get('#template-name').element.value).toBe(`${flowName}（自定义）`)
      expect(wrapper.get('#template-code').attributes('disabled')).toBeUndefined()
    },
  )

  it('prefills an editable identity for a blank Advanced flow', async () => {
    await render(); await startNew('advanced')
    expect(wrapper.get('#template-code').element.value).toBe('custom-advanced-flow')
    expect(wrapper.get('#template-name').element.value).toBe('自定义高级知识流程')
    expect(wrapper.get('#template-code').attributes('disabled')).toBeUndefined()
  })

  it('uses one shared suffix when either preferred value already exists', async () => {
    api.flowTemplates.mockResolvedValue([
      standardTemplate(), advanced,
      { ...advanced, id: 'custom-qa', code: 'custom-standard-qa', name: '问答知识流程（自定义）' },
    ])
    await render(); await startNew(1)
    expect(wrapper.get('#template-code').element.value).toBe('custom-standard-qa-2')
    expect(wrapper.get('#template-name').element.value).toBe('问答知识流程（自定义）（2）')
  })

  it('shows and clears field-level required errors immediately', async () => {
    await render(); await startNew('advanced')
    await wrapper.get('#template-code').setValue('')
    await wrapper.get('#template-name').setValue('')
    expect(wrapper.get('#template-code').attributes()).toMatchObject({ 'aria-invalid': 'true', 'aria-describedby': 'template-code-error' })
    expect(wrapper.get('#template-name').attributes()).toMatchObject({ 'aria-invalid': 'true', 'aria-describedby': 'template-name-error' })
    expect(wrapper.get('#template-code-error').text()).toBe('模板编码不能为空')
    expect(wrapper.get('#template-name-error').text()).toBe('模板名称不能为空')

    await wrapper.get('#template-code').setValue('my-flow')
    await wrapper.get('#template-name').setValue('我的流程')
    expect(wrapper.find('#template-code-error').exists()).toBe(false)
    expect(wrapper.find('#template-name-error').exists()).toBe(false)
  })

  it.each(['保存草稿', '运行当前流程'])('reveals and focuses the first empty field before %s without creating a flow', async action => {
    await render(); await startNew('advanced')
    await wrapper.get('#template-code').setValue('')
    await button('流程设置').trigger('click')
    await button(action).trigger('click'); await flushPromises()
    expect(wrapper.find('.template-settings').exists()).toBe(true)
    expect(document.activeElement).toBe(wrapper.get('#template-code').element)
    expect(api.createFlowTemplate).not.toHaveBeenCalled()
    expect(router.push).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('模板编码和名称不能为空')
  })

  it('locks the code after first save while keeping the name editable', async () => {
    await render(); await startNew(0)
    await button('保存草稿').trigger('click'); await flushPromises()
    expect(api.createFlowTemplate).toHaveBeenCalledWith(expect.objectContaining({ code: 'custom-standard-text', name: '文本知识流程（自定义）' }))
    expect(wrapper.get('#template-code').attributes('disabled')).toBeDefined()
    expect(wrapper.get('#template-name').attributes('disabled')).toBeUndefined()
  })

  it('prefills the unsaved identity when saving a builtin as custom', async () => {
    vi.spyOn(window, 'confirm').mockReturnValueOnce(true)
    await render(); await wrapper.findAll('.template-list button')[0].trigger('click')
    await button('保存草稿').trigger('click'); await flushPromises()
    expect(api.materializeManagedFlow).toHaveBeenCalledWith('standard-text')
    expect(api.createFlowTemplate).not.toHaveBeenCalled()
    expect(wrapper.get('#template-code').element.value).toBe('custom-standard-text')
    expect(wrapper.get('#template-name').element.value).toBe('文本知识流程（自定义）')
    expect(wrapper.get('#template-code').attributes('disabled')).toBeUndefined()
  })
})

describe('publish success notification', () => {
  it.each(['timeout', 'close'])('dismisses the Standard success notification via %s', async method => {
    vi.useFakeTimers()
    api.publishFlowTemplate.mockResolvedValueOnce({ revision: 3, status: 'published' })
    await render(); await wrapper.findAll('.template-list button')[0].trigger('click')
    await button('发布').trigger('click'); await flushPromises()
    expect(wrapper.get('.publish-notice[role="status"]').text()).toContain(`发布成功：${standardTemplate().name} · r3`)
    if (method === 'timeout') {
      await vi.advanceTimersByTimeAsync(4999)
      expect(wrapper.find('.publish-notice').exists()).toBe(true)
      await vi.advanceTimersByTimeAsync(1)
    } else await wrapper.get('[aria-label="关闭发布成功提示"]').trigger('click')
    expect(wrapper.find('.publish-notice').exists()).toBe(false)
  })

  it('clears previous success when the next publication fails', async () => {
    api.publishFlowTemplate.mockResolvedValueOnce({ revision: 1, status: 'published' })
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')
    await button('发布').trigger('click'); await flushPromises()
    expect(wrapper.find('.publish-notice').exists()).toBe(true)
    api.publishFlowTemplate.mockRejectedValueOnce(new Error('发布失败'))
    await button('发布').trigger('click'); await flushPromises()
    expect(wrapper.find('.publish-notice').exists()).toBe(false)
    expect(wrapper.text()).toContain('发布失败')
  })

  it('does not show a late publication notification after leaving the editor', async () => {
    let finish
    api.publishFlowTemplate.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')
    await button('发布').trigger('click')
    await button('‹ 退出编辑').trigger('click')
    finish({ revision: 1, status: 'published' }); await flushPromises()
    expect(wrapper.find('.publish-notice').exists()).toBe(false)
    expect(wrapper.find('.flow-toolbar').exists()).toBe(false)
  })
})

describe('temporary Advanced conversion', () => {
  async function convert() {
    await render(); await wrapper.findAll('.template-list button')[0].trigger('click')
    await button('转换为高级编排').trigger('click'); await flushPromises()
  }

  it('does not autosave preview edits and discards them when exiting', async () => {
    vi.useFakeTimers()
    await convert()
    wrapper.findComponent('.advanced-editor-test').vm.$emit('dirty')
    await vi.advanceTimersByTimeAsync(1000)
    expect(api.createFlowTemplate).not.toHaveBeenCalled()
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    await button('‹ 退出编辑').trigger('click')
    expect(wrapper.find('.conversion-notice').exists()).toBe(true)
    await button('‹ 退出编辑').trigger('click')
    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.findAll('.template-list button')).toHaveLength(2)
    expect(api.createFlowTemplate).not.toHaveBeenCalled()
    expect(api.updateFlowTemplate).not.toHaveBeenCalled()
  })

  it.each(['保存草稿', '运行当前流程'])('creates the preview only on %s and resumes autosave', async action => {
    vi.useFakeTimers()
    await convert()
    await button(action).trigger('click'); await flushPromises()
    expect(api.createFlowTemplate).toHaveBeenCalledTimes(1)
    expect(api.createFlowTemplate).toHaveBeenCalledWith(expect.objectContaining({
      code: conversionPreview.code, name: conversionPreview.name, authoring_mode: 'advanced',
      definition: conversionPreview.definition,
      derived_from_template_id: conversionPreview.source_template_id,
      derived_from_revision_id: conversionPreview.source_revision_id,
    }))
    expect(wrapper.find('.conversion-notice').exists()).toBe(false)
    expect(wrapper.get('.template-page-head .template-revisions').text()).toBe('最新草稿：r1 · 已发布版本：未发布')
    if (action === '运行当前流程') expect(router.push).toHaveBeenCalledWith(expect.stringContaining('template_id=converted'))
    wrapper.findComponent('.advanced-editor-test').vm.$emit('dirty')
    await vi.advanceTimersByTimeAsync(500); await flushPromises()
    expect(api.updateFlowTemplate).toHaveBeenCalledWith('converted', expect.any(Object))
    expect(api.createFlowTemplate).toHaveBeenCalledTimes(1)
  })

  it('keeps a failed first save temporary without background retries', async () => {
    vi.useFakeTimers()
    await convert()
    api.createFlowTemplate.mockRejectedValueOnce(new Error('保存失败'))
    await button('保存草稿').trigger('click'); await flushPromises()
    expect(wrapper.find('.conversion-notice').exists()).toBe(true)
    expect(wrapper.get('.template-page-head .template-revisions').text()).toBe('最新草稿：无 · 已发布版本：未发布')
    wrapper.findComponent('.advanced-editor-test').vm.$emit('dirty')
    await vi.advanceTimersByTimeAsync(1000)
    expect(api.createFlowTemplate).toHaveBeenCalledTimes(1)
    expect(api.updateFlowTemplate).not.toHaveBeenCalled()
  })

  it('ignores a conversion response arriving after exit', async () => {
    let finish
    api.previewFlowToAdvanced.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await render(); await wrapper.findAll('.template-list button')[0].trigger('click')
    await button('转换为高级编排').trigger('click')
    await button('‹ 退出编辑').trigger('click')
    finish(conversionPreview); await flushPromises()
    expect(wrapper.find('.flow-toolbar').exists()).toBe(false)
    expect(api.createFlowTemplate).not.toHaveBeenCalled()
    expect(router.replace).not.toHaveBeenCalled()
  })
})

describe('knowledge flow editing toolbar', () => {
  it('keeps the current viewport after successful compilation and only jumps to Console on failure', async () => {
    const scrollIntoView = vi.fn(), scrollTo = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView })
    vi.stubGlobal('scrollTo', scrollTo)
    vi.spyOn(window, 'scrollX', 'get').mockReturnValue(17)
    vi.spyOn(window, 'scrollY', 'get').mockReturnValue(260)
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')

    await button('编译校验').trigger('click'); await flushPromises()
    expect(api.validateFlowTemplate).toHaveBeenCalledWith('custom')
    expect(wrapper.get('.action-console').text()).toContain('compiled')
    expect(scrollIntoView).not.toHaveBeenCalled()
    expect(scrollTo).toHaveBeenLastCalledWith({ left: 17, top: 260, behavior: 'auto' })

    scrollTo.mockClear()
    api.validateFlowTemplate.mockRejectedValueOnce(new Error('PORT_TYPE_MISMATCH'))
    await button('编译校验').trigger('click'); await flushPromises()
    expect(wrapper.get('.action-console').text()).toContain('PORT_TYPE_MISMATCH')
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    expect(scrollIntoView).toHaveBeenCalledWith(expect.objectContaining({ block: 'start' }))
    expect(scrollTo).not.toHaveBeenCalled()
  })
  it('runs a clean published flow without manufacturing a new draft', async () => {
    api.flowTemplates.mockResolvedValue([{ ...advanced, revision_status: 'published' }])
    await render(); await wrapper.get('.template-list button').trigger('click')
    await button('运行当前流程').trigger('click'); await flushPromises()
    expect(api.updateFlowTemplate).not.toHaveBeenCalled()
    expect(api.createFlowTemplate).not.toHaveBeenCalled()
    expect(router.push).toHaveBeenCalledWith(expect.stringContaining('revision_kind=published'))
  })
  it('retries a failed old generation once for newer edits, without an endless timer retry', async () => {
    vi.useFakeTimers()
    let fail
    api.updateFlowTemplate.mockImplementationOnce(() => new Promise((resolve, reject) => { fail = reject }))
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')
    const editor = wrapper.findComponent('.advanced-editor-test')
    editor.vm.$emit('dirty'); await vi.advanceTimersByTimeAsync(500)
    editor.vm.definition = { nodes: [{ id: 'newest' }], edges: [] }
    editor.vm.$emit('dirty')
    fail(new Error('old request failed')); await flushPromises()
    expect(api.updateFlowTemplate).toHaveBeenCalledTimes(2)
    expect(api.updateFlowTemplate.mock.calls[1][1].definition.nodes).toEqual([{ id: 'newest' }])
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.updateFlowTemplate).toHaveBeenCalledTimes(2)
  })
  it('does not retry a failed unchanged generation and never announces a 409 publish as success', async () => {
    vi.useFakeTimers()
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')
    api.updateFlowTemplate.mockRejectedValueOnce(new Error('network failed'))
    wrapper.findComponent('.advanced-editor-test').vm.$emit('dirty')
    await vi.advanceTimersByTimeAsync(2500); await flushPromises()
    expect(api.updateFlowTemplate).toHaveBeenCalledTimes(1)
    api.publishFlowTemplate.mockRejectedValueOnce(Object.assign(new Error('STALE_FLOW_DRAFT'), { status: 409 }))
    await button('发布').trigger('click'); await flushPromises()
    expect(wrapper.find('.publish-notice').exists()).toBe(false)
    expect(wrapper.text()).toContain('STALE_FLOW_DRAFT')
  })
  it('keeps edits made while a publish request is in flight', async () => {
    vi.useFakeTimers()
    let finish
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')
    api.publishFlowTemplate.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await button('发布').trigger('click')
    const editor = wrapper.findComponent('.advanced-editor-test')
    editor.vm.definition = { nodes: [{ id: 'after-publish-request' }], edges: [] }
    editor.vm.$emit('dirty')
    finish({ revision: 1, status: 'published' }); await flushPromises()
    expect(editor.vm.definition.nodes).toEqual([{ id: 'after-publish-request' }])
    expect(wrapper.get('.save-state').text()).toContain('未保存')
  })
  it('debounces Advanced edits and flushes the latest DAG before running', async () => {
    vi.useFakeTimers()
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')
    const editor = wrapper.findComponent('.advanced-editor-test')
    editor.vm.definition = { schema_version: 3, nodes: [{ id: 'latest' }], edges: [] }
    editor.vm.$emit('dirty')
    await vi.advanceTimersByTimeAsync(499)
    expect(api.updateFlowTemplate).not.toHaveBeenCalled()
    await button('运行当前流程').trigger('click'); await flushPromises()
    expect(api.updateFlowTemplate.mock.calls[0][1].definition.nodes).toEqual([{ id: 'latest' }])
    expect(router.push).toHaveBeenCalledWith(expect.stringContaining('revision_kind=draft'))
    await vi.advanceTimersByTimeAsync(501)
    expect(api.updateFlowTemplate).toHaveBeenCalledTimes(1)
  })
  it('serializes slow saves, preserves subsequent edits and only then opens a run', async () => {
    vi.useFakeTimers()
    let finish
    api.updateFlowTemplate.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')
    const editor = wrapper.findComponent('.advanced-editor-test')
    editor.vm.$emit('dirty')
    await vi.advanceTimersByTimeAsync(500)
    editor.vm.definition = { schema_version: 3, nodes: [{ id: 'new-node' }], edges: [] }
    editor.vm.$emit('dirty')
    await button('运行当前流程').trigger('click')
    expect(router.push).not.toHaveBeenCalled()
    finish({ revision: 2, status: 'draft', definition: { nodes: [] } })
    await flushPromises()
    expect(editor.vm.definition.nodes).toEqual([{ id: 'new-node' }])
    expect(api.updateFlowTemplate).toHaveBeenCalledTimes(2)
    expect(api.updateFlowTemplate.mock.calls[1][1].definition.nodes).toEqual([{ id: 'new-node' }])
    expect(router.push).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(500)
    expect(api.updateFlowTemplate).toHaveBeenCalledTimes(2)
  })
  it('does not run an old draft after a save fails', async () => {
    vi.useFakeTimers()
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')
    wrapper.findComponent('.advanced-editor-test').vm.$emit('dirty')
    api.updateFlowTemplate.mockRejectedValueOnce(new Error('PORT_TYPE_MISMATCH'))
    await button('运行当前流程').trigger('click'); await flushPromises()
    expect(router.push).not.toHaveBeenCalled()
    expect(wrapper.get('.save-state').text()).toContain('保存失败')
    expect(wrapper.text()).toContain('PORT_TYPE_MISMATCH')
  })
  it('ignores a late response after leaving the editor', async () => {
    vi.useFakeTimers()
    let finish
    api.updateFlowTemplate.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await render(); await wrapper.findAll('.template-list button')[1].trigger('click')
    wrapper.findComponent('.advanced-editor-test').vm.$emit('dirty')
    await vi.advanceTimersByTimeAsync(500)
    vi.spyOn(window, 'confirm').mockReturnValueOnce(true)
    await button('‹ 退出编辑').trigger('click')
    finish({ revision: 2, status: 'draft' }); await flushPromises()
    expect(wrapper.find('.advanced-editor-test').exists()).toBe(false)
    expect(router.push).not.toHaveBeenCalled()
  })
  it('locks Standard outputs and omits them from save requests', async () => {
    await render()
    await wrapper.findAll('.template-list button')[0].trigger('click')
    await button('流程设置').trigger('click')
    expect(wrapper.get('[data-testid="managed-outputs"]').text()).toContain('固定模板维护')
    expect(wrapper.findAll('.template-settings input[type="checkbox"]')).toHaveLength(0)
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false)
    await button('保存草稿').trigger('click'); await flushPromises()
    expect(api.updateFlowTemplate.mock.calls.at(-1)[1]).not.toHaveProperty('output_types')
  })
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
    expect(wrapper.findAll('[role="status"]').some(item => item.text().includes('原标准流程不会被修改'))).toBe(true)
    await button('转换为高级编排').trigger('click')
    await flushPromises()
    expect(api.previewFlowToAdvanced).toHaveBeenCalledWith('standard-text')
    expect(api.detachFlowToAdvanced).not.toHaveBeenCalled()
    expect(wrapper.get('.authoring-mode-actions').text()).toContain('高级编排 · Authoring DAG')
    expect(wrapper.get('.authoring-mode-actions').findAll('button').map(item => item.text())).toEqual(['‹ 退出编辑'])
    expect(wrapper.get('.save-state').text()).toBe('转换预览 · 未保存')
    await button('‹ 退出编辑').trigger('click')
    expect(wrapper.find('.flow-toolbar').exists()).toBe(false)
    expect(api.createFlowTemplate).not.toHaveBeenCalled()
    expect(api.updateFlowTemplate).not.toHaveBeenCalled()
    expect(wrapper.findAll('.template-list button')).toHaveLength(2)
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
    expect(wrapper.findAll('.business-stage')).toHaveLength(5)
    expect(wrapper.find('.serving-selector').exists()).toBe(true)
    expect(wrapper.get('.template-page-head .template-revisions').text()).toBe('最新草稿：无 · 已发布版本：未发布')
    expect(wrapper.get('.authoring-mode-actions').text()).not.toContain('转换为高级编排')
    expect(button('‹ 退出编辑')).toBeDefined()
  })
})
