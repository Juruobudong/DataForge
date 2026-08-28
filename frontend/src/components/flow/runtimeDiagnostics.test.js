import test from 'node:test'
import assert from 'node:assert/strict'
import { nodeFailureInfo } from './runtimeDiagnostics.js'

test('partial failure is explained from logs without counting repeated lines as chunks', () => {
  const reason = 'QA_OUTPUT_INVALID: 提问方向必须为 JSON 字符串数组'
  const node = { status: 'completed', error_detail: {}, metrics: { chunk_processing: [{ output_key: 'qa', successful_chunks: 3, failed_chunks: 2 }] },
    logs: [{ stream: 'stderr', message: `\u001b[31m${reason}\u001b[0m\n${reason}` }] }
  const original = structuredClone(node)
  const failure = nodeFailureInfo(node)
  assert.equal(failure.hasFailure, true)
  assert.equal(failure.failed, false)
  assert.deepEqual(failure.reasons, [reason])
  assert.match(failure.explanation, /第一阶段/)
  assert.match(failure.explanation, /未记录原始模型响应/)
  assert.deepEqual(node, original)
})

test('empty structured error does not mask real errors or fabricate causes', () => {
  assert.deepEqual(nodeFailureInfo({ error_detail: {}, error: '连接超时' }).reasons, ['连接超时'])
  const failure = nodeFailureInfo({ status: 'completed', metrics: { chunk_processing: [{ successful_chunks: 3, failed_chunks: 2 }] } })
  assert.equal(failure.hasFailure, true)
  assert.deepEqual(failure.reasons, [])
  assert.equal(failure.explanation, '')
  assert.equal(nodeFailureInfo({ status: 'completed', logs: [{ stream: 'stderr', message: 'INFO Results saved to dataforge-memory' }] }).hasFailure, false)
})
