import test from 'node:test'
import assert from 'node:assert/strict'

import {
  activationCanRun,
  frozenRoutesForStage,
  groupedAssetOptions,
  releaseCanFreeze,
  releaseSelectionSummary,
} from './institutionReleaseModel.js'

test('机构 Release 统计保留原始引用和去重数量', () => {
  assert.deepEqual(releaseSelectionSummary({ selection_summary: {
    project_required_refs: 16, manual_refs: 3, raw_refs: 19,
    duplicates_removed: 4, resolved_assets: 15,
  } }), {
    projectRequiredRefs: 16, manualRefs: 3, rawRefs: 19,
    duplicatesRemoved: 4, resolvedAssets: 15,
  })
})

test('机构 Release 只展示当前环境的 Frozen 项目版本', () => {
  const routes = [
    { id: 'test', status: 'frozen', release_stage: 'test' },
    { id: 'prod', status: 'frozen', release_stage: 'production' },
    { id: 'draft', status: 'draft', release_stage: 'test' },
  ]
  assert.deepEqual(frozenRoutesForStage(routes, 'production').map(item => item.id), ['prod'])
})

test('项目资产保持锁定并展示来源项目', () => {
  const groups = groupedAssetOptions({ collections: [{ collection_name: 'text', assets: [{
    asset_version_id: 'asset-1', required: true,
    required_by_projects: [{ project_id: 'p1', project_name: 'qa_agent' }],
  }] }] })
  assert.equal(groups[0].assets[0].locked, true)
  assert.deepEqual(groups[0].assets[0].projectNames, ['qa_agent'])
})

test('Release 和 Activation 都以服务端 blocked 为最终门禁', () => {
  assert.equal(releaseCanFreeze({ preflight: { blocked: 0 } }), true)
  assert.equal(releaseCanFreeze({ preflight: { blocked: 1 } }), false)
  assert.equal(activationCanRun({ ready: true, blocked: 0, summary: { ready_candidates: 3 } }), true)
  assert.equal(activationCanRun({ ready: false, blocked: 1, summary: { ready_candidates: 3 } }), false)
})
