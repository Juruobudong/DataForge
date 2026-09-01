import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import RoutingPublishPanel from './RoutingPublishPanel.vue'
import RouteVersionTable from './RouteVersionTable.vue'

describe('project publishing release workbench', () => {
  it('requires successful preflight and renders a semantic route diff', async () => {
    const wrapper = mount(RoutingPublishPanel, { props: {
      validation: { available: true, valid: true, deferred: false, checks: [] },
      result: { snapshot: { schema_version: 3 } }, institution: false, ready: false, busy: false,
      problems: [], actionLabel: '发布测试版本', libraries: [{ id: 'kl1', name: '业务知识库' }],
      diff: { from_version: 2, summary: { added: 0, removed: 0, changed: 1, total: 1 }, added: [], removed: [], changed: [{
        before: { task_code: 'lookup', org_code: 'general', top_k: 10, final_top_k: 5, reranker_serving_code: null, libraries: [{ knowledge_library_id: 'kl1', asset_version_no: 2 }] },
        after: { task_code: 'lookup', org_code: 'general', top_k: 20, final_top_k: 8, reranker_serving_code: 'bge', libraries: [{ knowledge_library_id: 'kl1', asset_version_no: 3 }] },
      }] },
    } })
    const buttons = wrapper.findAll('button')
    expect(buttons.find(item => item.text() === '发布测试版本').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('业务知识库 · Asset V2')
    expect(wrapper.text()).toContain('业务知识库 · Asset V3')
    expect(wrapper.text()).toContain('召回 20 · 最终 8 · bge')
    await wrapper.setProps({ ready: true })
    expect(wrapper.findAll('button').find(item => item.text() === '发布测试版本').attributes('disabled')).toBeUndefined()
  })

  it('marks current and latest frozen versions and only rolls back historical published versions', async () => {
    const wrapper = mount(RouteVersionTable, { props: { allowRollback: true, versions: [
      { id: 'v3', version_no: 3, release_stage: 'test', origin: 'central', status: 'published', is_current: true, change_summary: { added: 0, removed: 0, changed: 1, total: 1 }, created_at: 'now' },
      { id: 'v2', version_no: 2, release_stage: 'test', origin: 'central', status: 'published', is_current: false, change_summary: { added: 1, removed: 0, changed: 0, total: 1 }, created_at: 'before' },
      { id: 'v1', version_no: 1, release_stage: 'test', origin: 'central_offline', status: 'frozen', is_latest_frozen: true, change_summary: { added: 1, removed: 0, changed: 0, total: 1 }, created_at: 'old' },
    ] } })
    expect(wrapper.text()).toContain('当前发布')
    expect(wrapper.text()).toContain('最新冻结')
    const rollback = wrapper.findAll('button').filter(item => item.text() === '回滚')
    expect(rollback).toHaveLength(1)
    await rollback[0].trigger('click')
    expect(wrapper.emitted('rollback')[0]).toEqual([2])
  })
})
