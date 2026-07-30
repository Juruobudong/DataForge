async function request(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json()
      detail = payload.message || payload.detail || detail
    } catch (_) {
      // Keep the HTTP status when the response is not JSON.
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return response.json()
}

export const api = {
  health: () => request('/api/health'),
  dashboard: () => request('/api/dashboard'),
  sources: () => request('/api/sources'),
  sourceVersions: (sourceId) => request(`/api/sources/${sourceId}/versions`),
  uploadSource: (formData) => request('/api/sources', { method: 'POST', body: formData }),
  studioStatus: () => request('/api/dataflow-studio/status'),
  dataflowPipelines: () => request('/api/dataflow-pipelines'),
  dataflowTasks: (pipelineId = '') => request(`/api/dataflow-tasks${pipelineId ? `?pipeline_id=${encodeURIComponent(pipelineId)}` : ''}`),
  sendToDataFlow: (versionId) => request(`/api/source-versions/${versionId}/send-to-dataflow`, { method: 'POST' }),
  pipelines: () => request('/api/pipelines'),
  knowledgeTypes: () => request('/api/knowledge-types'),
  createKnowledgeType: (payload) => request('/api/knowledge-types', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }),
  standardPipelines: (typeId = '') => request(`/api/standard-pipelines${typeId ? `?knowledge_type_id=${encodeURIComponent(typeId)}` : ''}`),
  publishStandardPipeline: (payload) => request('/api/standard-pipelines/publish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }),
  setDefaultPipeline: (pipelineId) => request(`/api/standard-pipelines/${pipelineId}/default`, { method: 'POST' }),
  knowledgeJobs: () => request('/api/knowledge-jobs'),
  knowledgeJob: (jobId) => request(`/api/knowledge-jobs/${jobId}`),
  startKnowledgeJob: (payload) => request('/api/knowledge-jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }),
  knowledgeBases: () => request('/api/knowledge-bases'),
  knowledgeBase: (baseId, { page = 1, pageSize = 50, query = '' } = {}) => request(`/api/knowledge-bases/${baseId}?page=${page}&page_size=${pageSize}&query=${encodeURIComponent(query)}`),
  knowledgeRecordLineage: (recordId) => request(`/api/knowledge-records/${recordId}/lineage`),
  runs: () => request('/api/runs'),
  run: (runId) => request(`/api/runs/${runId}`),
  startRun: (payload) => request('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }),
  assets: () => request('/api/assets'),
  assetVersions: (assetId) => request(`/api/assets/${assetId}/versions`),
  assetPreview: (versionId) => request(`/api/asset-versions/${versionId}/preview?limit=8`),
  lineage: (versionId) => request(`/api/asset-versions/${versionId}/lineage`),
  downloadUrl: (versionId) => `/api/asset-versions/${versionId}/download`
}
