import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const detail = fs.readFileSync(new URL('./KnowledgeLibraryDetailView.vue', import.meta.url), 'utf8')
const overview = fs.readFileSync(new URL('./KnowledgeBaseView.vue', import.meta.url), 'utf8')
const api = fs.readFileSync(new URL('../../api/platform.js', import.meta.url), 'utf8')

test('knowledge detail exposes review counts, batch actions and full vector publish gate', () => {
  assert.match(detail, /待审核/)
  assert.match(detail, /批量通过/)
  assert.match(detail, /批量不通过/)
  assert.match(detail, /全量生效入向量库/)
  assert.match(detail, /reviewSummary\?\.can_publish/)
  assert.match(detail, /expected_snapshot_digest/)
  assert.doesNotMatch(detail, /selected_items/)
})

test('QA and text drawers edit final knowledge while keeping evidence read-only', () => {
  assert.match(detail, /draftQuestion/)
  assert.match(detail, /draftAnswer/)
  assert.match(detail, /draftContent/)
  assert.match(detail, /reviewDrawer\('approved'\)/)
  assert.match(detail, /reviewDrawer\('rejected'\)/)
  assert.match(detail, /来源与 Evidence 不会被改写/)
  assert.match(detail, /sourcePreviewUrl/)
})

test('knowledge overview separates review and vector lifecycle states', () => {
  assert.match(overview, /<th>审核状态<\/th>/)
  for (const label of ['未入库', '构建中', 'Vector Ready', '有更新', '失败']) {
    assert.match(overview, new RegExp(label))
  }
  assert.match(overview, /不适用/)
})

test('platform API uses only the explicit review and vector publish endpoints', () => {
  assert.match(api, /\/api\/knowledge\/items\/\$\{itemId\}/)
  assert.match(api, /\/api\/knowledge-libraries\/\$\{libraryId\}\/review\/batch/)
  assert.match(api, /\/api\/knowledge-libraries\/\$\{libraryId\}\/review-summary/)
  assert.match(api, /\/api\/knowledge-libraries\/\$\{libraryId\}\/vector-publish/)
  assert.doesNotMatch(api, /queueVectorSync/)
})
