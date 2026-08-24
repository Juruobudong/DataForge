export function qaEmbeddingMode(profile) {
  if (profile?.code === 'qa-question') return 'question'
  if (profile?.code === 'qa-full') return 'full'
  return null
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
  if (!deploymentTasks.some(task => task.enabled)) problems.push('请先配置并启用 Deployment Task')
  if (!authorizations.some(route => route.enabled && route.knowledge_library_ids?.length)) problems.push('请先完成知识授权')
  if (!String(targetUri || '').trim()) problems.push('当前阶段尚未配置 Milvus Target')
  return { ready: problems.length === 0, problems }
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
