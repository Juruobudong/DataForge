export const DOMAIN_TERMS = Object.freeze({
  project: { label: '项目', technical: 'Project' },
  deployment: { label: '发布目标', technical: 'Deployment' },
  projectDeployment: { label: '项目发布绑定', technical: 'ProjectDeployment' },
  projectTask: { label: '业务任务', technical: 'Project Task' },
  deploymentTask: { label: '运行任务', technical: 'Deployment Task' },
  knowledgeType: { label: '知识类型', technical: 'Knowledge Type' },
  indexProfile: { label: '索引配置', technical: 'Index Profile' },
  qaEmbedding: { label: 'QA 向量化方式', technical: 'QA Embedding' },
  authorization: { label: '知识范围', technical: 'Knowledge Authorization' },
  routing: { label: '发布配置', technical: 'Routing' },
  routingSnapshot: { label: '发布配置快照', technical: 'RoutingSnapshot' },
  routeVersion: { label: '发布版本', technical: 'RouteVersion' },
  milvusTarget: { label: 'Milvus 服务', technical: 'Milvus Target' },
})

export function domainTerm(key) {
  return DOMAIN_TERMS[key] || { label: key, technical: key }
}
