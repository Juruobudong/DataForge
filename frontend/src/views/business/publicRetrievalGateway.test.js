import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const knowledgeList = fs.readFileSync(new URL('./KnowledgeBaseView.vue', import.meta.url), 'utf8')
const knowledgeDetail = fs.readFileSync(new URL('./KnowledgeLibraryDetailView.vue', import.meta.url), 'utf8')
const publishing = fs.readFileSync(new URL('./ProjectAuthorizationView.vue', import.meta.url), 'utf8')

test('ordinary knowledge list hides physical vector storage columns', () => {
  assert.doesNotMatch(knowledgeList, /<th>Collection<\/th>/)
  assert.doesNotMatch(knowledgeList, /<th>Partition<\/th>/)
  assert.match(knowledgeDetail, /class="library-technical-details"/)
  assert.match(knowledgeDetail, /<summary>技术详情<\/summary>/)
  assert.match(knowledgeDetail, /library\.collection_names/)
  assert.match(knowledgeDetail, /library\.partition_name/)
})

test('project publishing passes logical public retrieval codes into the shared console', () => {
  assert.match(publishing, /:project-code="selectedProject\?\.code"/)
  assert.match(publishing, /:deployment-code="selectedDeployment\?\.code"/)
  assert.match(publishing, /project\.name \}\} · \{\{ project\.code/)
  assert.match(publishing, /deployment\.name \}\} · \{\{ deployment\.code/)
})
