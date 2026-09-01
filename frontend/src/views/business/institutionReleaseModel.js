export function releaseSelectionSummary(plan) {
  const summary = plan?.selection_summary || {}
  return {
    projectRequiredRefs: Number(summary.project_required_refs || 0),
    manualRefs: Number(summary.manual_refs || 0),
    rawRefs: Number(summary.raw_refs || 0),
    duplicatesRemoved: Number(summary.duplicates_removed || 0),
    resolvedAssets: Number(summary.resolved_assets || 0),
  }
}

export function releaseCanFreeze(plan) {
  return Boolean(plan) && Number(plan?.preflight?.blocked || 0) === 0
}

export function activationCanRun(preflight) {
  return Boolean(preflight?.ready) && Number(preflight?.blocked || 0) === 0 &&
    Number(preflight?.summary?.ready_candidates || 0) > 0
}

export function groupedAssetOptions(response) {
  const collections = response?.collections || []
  const requiredByLibrary = new Map()
  for (const collection of collections) {
    for (const asset of collection.assets || []) {
      if (asset.locked || asset.required) requiredByLibrary.set(asset.knowledge_library_id, asset)
    }
  }
  return collections.map(collection => ({
    ...collection,
    assets: (collection.assets || []).map(asset => ({
      ...asset,
      locked: Boolean(asset.locked || asset.required),
      projectNames: (asset.required_by_projects || []).map(project => project.project_name || project),
      conflictWith: !asset.locked && requiredByLibrary.get(asset.knowledge_library_id)
        && requiredByLibrary.get(asset.knowledge_library_id).asset_version_id !== asset.asset_version_id
        ? requiredByLibrary.get(asset.knowledge_library_id) : null,
      selectionBlocked: !asset.locked && !asset.selected_manually && requiredByLibrary.get(asset.knowledge_library_id)
        && requiredByLibrary.get(asset.knowledge_library_id).asset_version_id !== asset.asset_version_id,
    })),
  }))
}

export function releaseAssetVersionConflicts(plan) {
  const assetsById = new Map((plan?.asset_versions || []).map(asset => [asset.asset_version_id, asset]))
  return (plan?.preflight?.checks || []).filter(check => check.code === 'RELEASE.LIBRARY.ASSET_VERSION_CONFLICT'
    && check.status === 'blocked').map(check => ({
    check,
    assets: (Array.isArray(check.observed) ? check.observed : []).map(id => assetsById.get(id) || {
      asset_version_id: id, knowledge_library_name: check.subject?.knowledge_library_id || '未知知识库',
    }),
  }))
}

export function releasePreflightExpectedText(check) {
  if (check?.code === 'RELEASE.LIBRARY.ASSET_VERSION_CONFLICT') return '同一知识库只能引用一个 AssetVersion'
  return JSON.stringify(check?.expected)
}

export function frozenRoutesForStage(routes, releaseStage) {
  return (routes || []).filter(route => route.status === 'frozen' && route.release_stage === releaseStage)
}

export function institutionReleaseTarget(deployment) {
  return {
    target_deployment_id: deployment?.id || '',
    target_institution_code: deployment?.institution_code || '',
  }
}
