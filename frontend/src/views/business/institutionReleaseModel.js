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
  return (response?.collections || []).map(collection => ({
    ...collection,
    assets: (collection.assets || []).map(asset => ({
      ...asset,
      locked: Boolean(asset.locked || asset.required),
      projectNames: (asset.required_by_projects || []).map(project => project.project_name || project),
    })),
  }))
}
