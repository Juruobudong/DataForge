import assert from 'node:assert/strict'
import test from 'node:test'

import {
  countDifference, formatInventoryCount, knowledgeTypeLabel, routingReferenceSummary,
  sortCollections, sortPartitions, vectorStatusLabel,
} from './vectorStorageModel.js'

test('向量库存状态和知识类型使用统一中文标签', () => {
  assert.equal(vectorStatusLabel('GC_ELIGIBLE'), '可清理')
  assert.equal(vectorStatusLabel('INCONSISTENT'), '数据异常')
  assert.equal(knowledgeTypeLabel('graph:semantic'), '语义图谱')
})

test('actual expected 差异和空值格式明确', () => {
  assert.equal(countDifference({ actual_count: 90, expected_count: 100 }), -10)
  assert.equal(countDifference({ actual_count: null, expected_count: 100 }), null)
  assert.equal(formatInventoryCount(null), '—')
})

test('Collection 受管优先且名称稳定，Partition 按库和版本排序', () => {
  assert.deepEqual(sortCollections([
    { collection_name: 'z', managed: false }, { collection_name: 'b', managed: true }, { collection_name: 'a', managed: true },
  ]).map(item => item.collection_name), ['a', 'b', 'z'])
  assert.deepEqual(sortPartitions([
    { partition_name: 'p1', knowledge_library_name: '甲', asset_version_no: 1 },
    { partition_name: 'p3', knowledge_library_name: '甲', asset_version_no: 3 },
    { partition_name: 'p2', knowledge_library_name: '乙', asset_version_no: 2 },
  ]).map(item => item.partition_name), ['p3', 'p1', 'p2'])
})

test('Routing 引用按项目去重汇总', () => {
  assert.deepEqual(routingReferenceSummary({ routing_refs: [
    { project_name: '问答智能体' }, { project_name: '问答智能体' }, { project_code: 'triage' },
  ] }), { count: 3, projects: ['问答智能体', 'triage'] })
})
