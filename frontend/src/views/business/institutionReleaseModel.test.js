import test from 'node:test'
import assert from 'node:assert/strict'

import {
  activationCanRun,
  frozenRoutesForStage,
  groupedAssetOptions,
  institutionReleaseTarget,
  releaseAssetVersionConflicts,
  releaseCanFreeze,
  releasePreflightExpectedText,
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
    asset_version_id: 'asset-1', knowledge_library_id: 'library-1', required: true,
    required_by_projects: [{ project_id: 'p1', project_name: 'qa_agent' }],
  }, {
    asset_version_id: 'asset-2', knowledge_library_id: 'library-1', asset_version_no: 2,
  }] }] })
  assert.equal(groups[0].assets[0].locked, true)
  assert.deepEqual(groups[0].assets[0].projectNames, ['qa_agent'])
  assert.equal(groups[0].assets[1].selectionBlocked, true)
  assert.equal(groups[0].assets[1].conflictWith.asset_version_id, 'asset-1')
})

test('机构 Release 把冲突 AssetVersion 映射为可识别资产并中文化期望值', () => {
  const plan = {
    asset_versions: [
      { asset_version_id: 'v5', knowledge_library_name: '问答知识', asset_version_no: 5,
        index_profile_code: 'qa-full', collection_name: 'dataforge_qa_full', partition_name: 'kl_qa__v5', selected_manually: true },
      { asset_version_id: 'v6', knowledge_library_name: '问答知识', asset_version_no: 6,
        index_profile_code: 'qa-question', collection_name: 'dataforge_qa_question', partition_name: 'kl_qa__v6', locked: true },
    ],
    preflight: { checks: [{
      code: 'RELEASE.LIBRARY.ASSET_VERSION_CONFLICT', status: 'blocked',
      subject: { knowledge_library_id: 'library-qa' }, expected: 'one AssetVersion', observed: ['v5', 'v6'],
    }] },
  }
  const [conflict] = releaseAssetVersionConflicts(plan)
  assert.deepEqual(conflict.assets.map(asset => [asset.asset_version_id, asset.index_profile_code]), [
    ['v5', 'qa-full'], ['v6', 'qa-question'],
  ])
  assert.equal(releasePreflightExpectedText(conflict.check), '同一知识库只能引用一个 AssetVersion')
})

test('Release 和 Activation 都以服务端 blocked 为最终门禁', () => {
  assert.equal(releaseCanFreeze({ preflight: { blocked: 0 } }), true)
  assert.equal(releaseCanFreeze({ preflight: { blocked: 1 } }), false)
  assert.equal(activationCanRun({ ready: true, blocked: 0, summary: { ready_candidates: 3 } }), true)
  assert.equal(activationCanRun({ ready: false, blocked: 1, summary: { ready_candidates: 3 } }), false)
})

test('机构发布请求同时携带内部 Deployment ID 与 institution_code', () => {
  assert.deepEqual(institutionReleaseTarget({ id: 'deployment-a', institution_code: 'INST-A' }), {
    target_deployment_id: 'deployment-a', target_institution_code: 'INST-A',
  })
})
