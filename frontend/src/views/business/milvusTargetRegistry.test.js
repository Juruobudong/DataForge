import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const registry = fs.readFileSync(new URL('./MilvusTargetRegistryView.vue', import.meta.url), 'utf8')
const initialization = fs.readFileSync(new URL('./LocalInitializationView.vue', import.meta.url), 'utf8')
const publishing = fs.readFileSync(new URL('./ProjectAuthorizationView.vue', import.meta.url), 'utf8')
const api = fs.readFileSync(new URL('../../api/platform.js', import.meta.url), 'utf8')

test('central Milvus registry creates, verifies, edits and exposes only verified binding options', () => {
  assert.match(registry, /api\.createMilvusTarget/)
  assert.match(registry, /api\.patchMilvusTarget/)
  assert.match(registry, /正在连接验证/)
  assert.match(registry, /候选地址连接失败/)
  assert.match(api, /milvusTargets:.*\/api\/milvus-targets/)
  assert.match(publishing, /verifiedMilvusTargets/)
  assert.match(publishing, /milvus_target_id: target\.id/)
})

test('institution Milvus remains local and candidate save automatically verifies', () => {
  assert.match(publishing, /机构 Milvus 不在中心保存/)
  assert.doesNotMatch(publishing, /saveInstitutionTarget/)
  assert.match(initialization, /注册 Milvus 服务/)
  assert.match(initialization, /保存并验证/)
  assert.doesNotMatch(initialization, /verifyCandidate/)
  assert.match(initialization, /candidate\?\.status==='verification_failed'/)
})
