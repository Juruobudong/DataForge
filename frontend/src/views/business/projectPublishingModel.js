export function qaEmbeddingMode(profile) {
  if (profile?.code === 'qa-question') return 'question'
  if (profile?.code === 'qa-full') return 'full'
  return null
}

export function normalizeDefaultReleaseStage(value) {
  return value === 'production' ? 'production' : 'test'
}

export function compatibleProfilesForTask(task, knowledgeTypes, qaAgent = false) {
  if (!task?.knowledge_type) return []
  const type = knowledgeTypes.find(item => item.status === 'active' && item.code === task.knowledge_type)
  const unique = new Map()
  for (const profile of type?.index_profiles || []) {
    if (qaAgent && profile.code !== 'qa-question') continue
    unique.set(profile.id, profile)
  }
  return [...unique.values()]
}

export function routingPublishReadiness(deploymentTasks, authorizations, targetUri) {
  const problems = []
  if (!deploymentTasks.some(task => task.enabled)) problems.push('请先配置并启用检索通道')
  if (!authorizations.some(route => route.enabled && route.knowledge_library_ids?.length)) problems.push('请先完成知识范围配置')
  if (!String(targetUri || '').trim()) problems.push('当前环境尚未配置 Milvus 服务')
  return { ready: problems.length === 0, problems }
}

export function preferredDeployment(deployments, boundDeploymentId = null) {
  if (boundDeploymentId) return deployments.find(item => item.deployment_id === boundDeploymentId) || null
  return deployments.find(item => item.scope === 'central') || deployments[0] || null
}

export function movePriority(ids, id, offset) {
  const index = ids.indexOf(id), target = index + offset
  if (index < 0 || target < 0 || target >= ids.length) return [...ids]
  const next = [...ids]
  ;[next[index], next[target]] = [next[target], next[index]]
  return next
}

export function orgRoutesForTask(authorizations, deploymentTaskId) {
  return (authorizations || []).filter(route => route.project_release_task_id === deploymentTaskId)
}

export function newOrgScopeDefaults(deployment, existingRoutes = []) {
  const preferredCode = deployment?.scope === 'institution'
    ? String(deployment?.institution_code || '').trim()
    : 'general'
  const used = new Set((existingRoutes || []).map(route => route.org_code))
  return {
    orgCode: used.has(preferredCode) ? '' : preferredCode,
    orgName: deployment?.scope === 'institution' ? String(deployment?.institution_name || '').trim() : '',
  }
}

export function availableOrgCodePresets(value) {
  if (!Array.isArray(value)) return []
  return value.filter(item => String(item?.name || '').trim() && String(item?.org_code || '').trim())
    .map(item => ({ name: String(item.name).trim(), org_code: String(item.org_code).trim() }))
}

export function resolveOrgCodePreset(presets, presetCode, existingRoutes = []) {
  const preset = availableOrgCodePresets(presets).find(item => item.org_code === presetCode)
  if (!preset) return null
  return {
    preset,
    existingRoute: existingRoutes.find(route => route.org_code === preset.org_code) || null,
  }
}

export function routingValidationView(result) {
  const checks = Array.isArray(result?.checks) ? result.checks : []
  const target = result?.target_validation || {}
  return {
    available: checks.length > 0,
    checks,
    blocked: Number(result?.blocked || checks.filter(item => item.status === 'blocked').length),
    valid: result?.valid === true,
    deferred: target.mode === 'deferred_to_local',
    targetReason: target.reason || '',
  }
}
