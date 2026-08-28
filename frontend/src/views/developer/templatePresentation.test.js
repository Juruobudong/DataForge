import assert from 'node:assert/strict'
import test from 'node:test'

import {
  groupFlowTemplates,
  normaliseTemplateOutputKey,
  templateOutputLabel,
  templateOutputSummary,
  templateRevisionSummary,
} from './templatePresentation.js'

test('版本摘要区分最新草稿、已发布修订和未保存的新流程', () => {
  const cases = [
    [null, '最新草稿：无 · 已发布版本：未发布'],
    [{ revision: 1, revision_status: 'draft', published_revision: null }, '最新草稿：r1 · 已发布版本：未发布'],
    [{ revision: 1, revision_status: 'published', published_revision: 1 }, '最新草稿：无 · 已发布版本：r1'],
    [{ revision: 2, revision_status: 'draft', published_revision: 1 }, '最新草稿：r2 · 已发布版本：r1'],
  ]
  for (const [template, expected] of cases) assert.equal(templateRevisionSummary(template), expected)
})

test('内置模板按业务顺序分组且不通过 standard 前缀推断', () => {
  const templates = [
    { code: 'standard-multi', is_builtin: true },
    { code: 'standard-customer', is_builtin: false },
    { code: 'standard-text', is_builtin: true },
    { code: 'custom-flow', is_builtin: false },
  ]

  const grouped = groupFlowTemplates(templates)

  assert.deepEqual(grouped.builtin.map(item => item.code), ['standard-text', 'standard-multi'])
  assert.deepEqual(grouped.custom.map(item => item.code), ['standard-customer', 'custom-flow'])
})

test('多输出说明使用客户可读知识类型并规范化兼容 graph 键', () => {
  const knowledgeTypes = [{ code: 'text', name: '文本知识' }, { code: 'qa', name: '问答知识' }]
  const template = { output_types: ['text', 'qa', 'graph'] }

  assert.equal(templateOutputSummary(template, knowledgeTypes), '输出：文本知识、问答知识、三元组图谱知识')
  assert.equal(templateOutputSummary({ output_types: ['text'] }, knowledgeTypes), '')
  assert.equal(normaliseTemplateOutputKey('graph'), 'graph:triple')
  assert.equal(templateOutputLabel('graph:triple', knowledgeTypes), '三元组图谱知识')
  assert.equal(templateOutputLabel('graph:semantic', knowledgeTypes), '语义图谱知识')
})
