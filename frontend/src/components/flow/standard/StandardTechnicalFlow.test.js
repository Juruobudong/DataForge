import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import StandardTechnicalFlow from './StandardTechnicalFlow.vue'
import { nativeQaOperator } from '../__tests__/flowFixtures'

function projection(outputs) {
  const nodes = [{ node_id: 'input', code: 'reviewed-source-chunk-input', kind: 'operator', display_name_zh: '已审核文档块' }], edges = []
  for (const output of outputs) {
    let previous = 'input'
    for (const code of ['generator', ...(output.startsWith('graph:') ? ['schema-validator', 'graph-quality-validator'] : []), 'knowledge-sink']) {
      const id = `${output}-${code}`
      nodes.push({ node_id: id, code, kind: code === 'knowledge-sink' ? 'knowledge_sink' : 'operator', display_name_zh: code, output_key: output, name: code, version: 1, provider: 'dataforge' })
      edges.push({ source: previous, target: id }); previous = id
    }
  }
  return { resolved_operators: nodes, edges }
}

describe('Standard technical responsibilities', () => {
  it('shows the actual native QA name in the chain and its detail', async () => {
    const value = projection(['qa'])
    const generator = value.resolved_operators.find(node => node.code === 'generator')
    Object.assign(generator, nativeQaOperator, { node_id: generator.node_id })
    const wrapper = mount(StandardTechnicalFlow, { props: { value } })
    const node = wrapper.findAll('.operator-step').find(item => item.text().includes('QA Extractor'))
    expect(node.text()).toContain('问答提取器')
    expect(node.text()).toContain('QA Extractor')
    await node.trigger('click')
    expect(wrapper.get('.operator-detail').text()).toContain('QA Extractor · qa-extractor · v1')
    wrapper.unmount()
  })
  it.each([['text'], ['qa'], ['graph:triple'], ['graph:semantic'], ['text', 'qa', 'graph:triple']])('projects %s without inventing runtime operators', (...outputs) => {
    const value = projection(outputs), before = JSON.stringify(value)
    const wrapper = mount(StandardTechnicalFlow, { props: { value } })
    expect(wrapper.findAll('.branch')).toHaveLength(outputs.length)
    for (const [index, branch] of wrapper.findAll('.branch').entries()) {
      const operators = branch.findAll('.operator-step').map(node => node.text()).join(' ')
      expect(operators).not.toMatch(/source-binding|knowledge-diff|knowledge-sink/)
      expect(operators.includes('schema-validator')).toBe(outputs[index].startsWith('graph:'))
      expect(operators.includes('graph-quality-validator')).toBe(outputs[index].startsWith('graph:'))
      expect(branch.findAll('.system-gate')).toHaveLength(0)
      expect(branch.findAll('.sink-step')).toHaveLength(1)
      expect(branch.text()).toContain('同一 Sink 事务')
      expect(branch.get('details').text()).toContain(`${outputs[index]}-knowledge-sink`)
    }
    expect(wrapper.text()).toContain('运行调试只预览')
    expect(JSON.stringify(value)).toBe(before)
    wrapper.unmount()
  })
  it('does not hide a node returned by the real projection', () => {
    const value = projection(['qa'])
    value.resolved_operators.find(node => node.code === 'generator').code = 'source-binding'
    const wrapper = mount(StandardTechnicalFlow, { props: { value } })
    expect(wrapper.findAll('.operator-step')).toHaveLength(value.resolved_operators.length)
    wrapper.unmount()
  })
})
