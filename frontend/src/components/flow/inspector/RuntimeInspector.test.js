import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import RuntimeInspector from './RuntimeInspector.vue'
import { consoleEventMessage } from '../../../views/developer/debugConsole'
import { dataflowOperators } from '../__tests__/flowFixtures'

describe('operator diagnostic rendering', () => {
  it('shows failure reasons in overview and logs despite completed status, and clears them on node change', async () => {
    const node = { node_id: 'qa', status: 'completed', error_detail: {},
      metrics: { chunk_processing: [{ output_key: 'qa', successful_chunks: 3, failed_chunks: 2 }] },
      logs: [{ stream: 'stderr', message: 'QA_OUTPUT_INVALID: 提问方向必须为 JSON 字符串数组' }] }
    const wrapper = mount(RuntimeInspector, { props: { node } })
    expect(wrapper.get('header .badge').classes()).toContain('amber')
    expect(wrapper.get('[aria-label="失败原因"]').text()).toContain('失败 2 块')
    expect(wrapper.get('[aria-label="失败原因"]').text()).toContain('第一阶段')
    await wrapper.findAll('button').find(button => button.text() === '查看原始日志').trigger('click')
    expect(wrapper.get('nav button.active').text()).toBe('日志')
    expect(wrapper.get('[aria-label="算子日志"]').text()).toContain('QA_OUTPUT_INVALID')
    await wrapper.setProps({ node: { node_id: 'healthy', status: 'completed' } })
    expect(wrapper.find('[aria-label="失败原因"]').exists()).toBe(false)
    expect(wrapper.get('header .badge').classes()).toContain('green')
    wrapper.unmount()
  })
  it('shows frozen bilingual identity separately from the runtime node id', () => {
    const wrapper = mount(RuntimeInspector, { props: { operator: dataflowOperators[0], node: { node_id: 'generate-qa', status: 'completed' } } })
    expect(wrapper.get('h3').text()).toBe('文本转问答生成器')
    expect(wrapper.get('.operator-bilingual').text()).toBe('Text2QAGenerator')
    expect(wrapper.get('header').text()).toContain('generate-qa')
    wrapper.unmount()
  })
  it('shows separate streams, multiline text and truncation without executing HTML', async () => {
    const wrapper = mount(RuntimeInspector, { props: { node: { node_id: 'n', status: 'failed', logs: [
      { stream: 'stdout', message: 'first\nsecond', truncated: false },
      { stream: 'stderr', message: '<img src=x onerror="alert(1)">', truncated: true },
    ] } } })
    await wrapper.findAll('nav button').find(item => item.text() === '日志').trigger('click')
    expect(wrapper.get('[aria-label="算子日志"]').text()).toContain('stdout')
    expect(wrapper.get('[aria-label="算子日志"]').text()).toContain('stderr')
    expect(wrapper.get('.truncated').text()).toContain('已截断')
    expect(wrapper.findAll('pre')[0].text()).toBe('first\nsecond')
    expect(wrapper.find('img').exists()).toBe(false)
    await wrapper.setProps({ node: { node_id: 'next', status: 'completed', logs: [] } })
    await wrapper.findAll('nav button').find(item => item.text() === '日志').trigger('click')
    expect(wrapper.text()).toContain('暂无算子日志')
    expect(wrapper.text()).not.toContain('alert(1)')
    wrapper.unmount()
  })
  it('labels diagnostic events without changing the source or ordinary events', () => {
    const event = { type: 'node.operator_log', message: 'warning\nnext', payload: { stream: 'stderr', truncated: true } }
    expect(consoleEventMessage(event)).toBe('[stderr] [已截断] warning\nnext')
    expect(event.message).toBe('warning\nnext')
    expect(consoleEventMessage({ type: 'node.completed', message: 'completed' })).toBe('completed')
  })
})
