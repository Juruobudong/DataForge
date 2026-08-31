import { afterEach, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import OperatorPalette from './OperatorPalette.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })

it('renders 26 distinct featured identities and preserves keyboard adding of a quality signal', async () => {
  const codes = ['Text2QAGenerator', 'PromptedRefiner', 'HashDeduplicateFilter', 'MinHashDeduplicateFilter',
    'ContentNullFilter', 'CharNumberFilter', 'SpecialCharacterFilter', 'NgramHashDeduplicateFilter', 'SimHashDeduplicateFilter', 'PromptedFilter',
    'MeanWordLengthFilter', 'LexicalDiversityFilter', 'UniqueWordsFilter', 'WatermarkFilter', 'HtmlEntityFilter', 'BlocklistFilter',
    'PresidioFilter', 'PIIAnonymizeRefiner', 'Text2MultiHopQAGenerator', 'Text2QASampleEvaluator', 'GeneralFilter',
    'PromptedEvaluator', 'SemDeduplicateFilter', 'SentenceNumberFilter', 'SymbolWordRatioFilter', 'RemoveRepetitionsPunctuationRefiner']
  const catalog = codes.map(code => ({ code, name: code, display_name_zh: code, source: 'dataflow', catalog_group: 'dataflow_featured',
    category: 'content-filtering', version: code === 'GeneralFilter' ? 2 : 1, enabled: true, approved: true,
    status: 'published', exposure: 'canvas', surfaces: ['advanced-canvas'], knowledge_types: ['text', 'qa'],
    dependency_status: { status: 'ready' }, input_ports: { input: { artifact_type: 'candidate:text' } },
    output_ports: { output: { artifact_type: 'candidate:text' } } }))
  wrapper = mount(OperatorPalette, { props: { catalog, outputTypes: ['text'] } })
  expect(wrapper.get('.capability-name').text()).toBe('DataFlow 精选')
  expect(wrapper.get('.capability-count').text()).toBe('26')
  expect(wrapper.findAll('.palette-entry')).toHaveLength(26)
  await wrapper.get('[aria-label="添加PromptedEvaluator"]').trigger('keydown', { key: 'Enter' })
  expect(wrapper.emitted('add-item').at(-1)[0].code).toBe('PromptedEvaluator')
  expect(wrapper.emitted('add-item').at(-1)[1]).toBe('operator')
})
