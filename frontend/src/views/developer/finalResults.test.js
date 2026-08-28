import test from 'node:test'
import assert from 'node:assert/strict'
import { finalResultCells, finalResultColumns, finalResultOutputs } from './finalResults.js'

function run(keys = ['text', 'qa', 'graph:triple']) {
  return { id: 'r', status: 'running', nodes: [], sink_previews: [], runtime_dag: {
    nodes: keys.flatMap(key => [{ id: `g-${key}`, kind: 'operator', ref: 'generator' },
      { id: `s-${key}`, kind: 'knowledge_sink', output_key: key, status: 'skipped' }]),
    edges: keys.map(key => ({ source: `g-${key}`, target: `s-${key}` })),
  } }
}
function preview(key, count = 1) { return { id: `p-${key}`, output_key: key, candidate_count: count, diff: { ADD: 0, UNCHANGED: count } } }
function processing(key, good, bad) { return [{ output_key: key, attempted_chunks: good + bad, successful_chunks: good, failed_chunks: bad }] }

test('five templates and custom types use actual frozen sink order, not names or preview arrival order', () => {
  for (const keys of [['text'], ['qa'], ['graph:triple'], ['graph:semantic'], ['text', 'qa', 'graph:triple'], ['extension', 'graph:semantic', 'qa']]) {
    const detail = run(keys)
    detail.sink_previews = [...keys].reverse().map(key => preview(key))
    assert.deepEqual(finalResultOutputs(detail).map(item => item.key), keys)
    assert.equal(finalResultOutputs(detail)[0].count, 1)
  }
})

test('typed cells preserve QA, graph entity/literal distinction and semantic relation labels', () => {
  assert.deepEqual(finalResultCells('text', { canonical_content: '全文\n第二行' }), ['全文\n第二行'])
  assert.deepEqual(finalResultCells('qa', { data_json: { question: '问题', answer: '答案' } }), ['问题', '答案'])
  assert.deepEqual(finalResultCells('graph:triple', { data_json: { subject: '体温', predicate: '数值', object: '37℃', data: { object_kind: 'literal' } } }), ['体温', '数值', '37℃', '字面值'])
  assert.equal(finalResultCells('graph:triple', { data_json: { data: { object_kind: 'entity' } } })[3], '实体')
  assert.deepEqual(finalResultCells('graph:semantic', { data_json: { source_entity: { name: '甲' }, relation: { type: 'r', type_label: '属于' }, target_entity: { name: '乙' } } }), ['甲', '属于', '乙'])
  assert.deepEqual(finalResultColumns('extension'), ['正文摘要'])
  assert.deepEqual(finalResultCells('extension', { canonical_content: '扩展内容' }), ['扩展内容'])
})

test('waiting, skipped and terminal absence are not misreported as successful empty output', () => {
  const detail = run(['text'])
  assert.equal(finalResultOutputs(detail)[0].status, '等待输出')
  detail.nodes = [{ node_id: 's-text', status: 'skipped' }]
  assert.equal(finalResultOutputs(detail)[0].status, '已跳过')
  detail.nodes = []; detail.runtime_dag.nodes[1].status = undefined; detail.status = 'completed'
  assert.equal(finalResultOutputs(detail)[0].status, '尚未到达输出')
  detail.sink_previews = [preview('text', 0)]
  assert.equal(finalResultOutputs(detail)[0].status, '零条结果')
  detail.nodes = [{ node_id: 'g-text', metrics: { chunk_processing: processing('text', 2, 0) } }]
  assert.equal(finalResultOutputs(detail)[0].status, '成功零产出')
})

test('only upstream diagnostics affect each output and counts remain per stage', () => {
  const detail = run()
  detail.sink_previews = [preview('text'), preview('qa', 0)]
  detail.nodes = [{ node_id: 'g-qa', status: 'completed', metrics: { chunk_processing: processing('qa', 2, 3) } },
    { node_id: 'g-graph:triple', status: 'failed', error: 'validation failed' }]
  const outputs = finalResultOutputs(detail)
  assert.equal(outputs[0].hasWarning, false)
  assert.equal(outputs[1].status, '部分处理失败')
  assert.equal(outputs[1].diagnostics.length, 1)
  assert.equal(outputs[1].diagnostics[0].processing[0].failed_chunks, 3)
  assert.equal(outputs[2].status, '处理失败')
  detail.nodes[0].metrics.chunk_processing = processing('qa', 0, 5)
  assert.equal(finalResultOutputs(detail)[1].status, '处理失败')
})

test('historical explicit extraction errors warn without fabricated counts; stdout/stderr alone do not', () => {
  const detail = run(['qa'])
  detail.sink_previews = [preview('qa')]
  detail.nodes = [{ node_id: 'g-qa', logs: [{ stream: 'stderr', message: 'QA_OUTPUT_INVALID: 提问方向必须为 JSON 字符串数组' }] }]
  const output = finalResultOutputs(detail)[0]
  assert.equal(output.hasWarning, true)
  assert.deepEqual(output.diagnostics[0].processing, [])
  detail.nodes[0].logs[0].message = 'INFO Results saved to dataforge-memory'
  assert.equal(finalResultOutputs(detail)[0].hasWarning, false)
})
