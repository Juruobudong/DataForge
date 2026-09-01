import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const router = readFileSync(new URL('../../router/index.js', import.meta.url), 'utf8')
const templates = readFileSync(new URL('./TemplateListView.vue', import.meta.url), 'utf8')
const vectorStorage = readFileSync(new URL('../business/VectorStorageView.vue', import.meta.url), 'utf8')
const outputTypes = readFileSync(new URL('../../components/governance/OutputTypeConfiguration.vue', import.meta.url), 'utf8')
const storageProfiles = readFileSync(new URL('../../components/governance/StorageProfileGovernance.vue', import.meta.url), 'utf8')
const layout = readFileSync(new URL('../../layouts/WorkspaceLayout.vue', import.meta.url), 'utf8')
const customize = readFileSync(new URL('../../components/MenuCustomizeDialog.vue', import.meta.url), 'utf8')

test('legacy knowledge type route converges into output type configuration', () => {
  assert.match(router, /knowledge-types'\s*,\s*redirect:\s*'\/developer\/flow-templates\?tab=output-types'/)
  assert.doesNotMatch(router, /import KnowledgeTypesView/)
  assert.match(templates, /输出类型配置/)
  assert.match(templates, /OutputTypeConfiguration/)
})

test('vector storage owns the stable Storage Profile deep link', () => {
  assert.match(vectorStorage, /route\.query\.tab === 'profiles'/)
  assert.match(vectorStorage, /Storage Profile（高级治理）/)
  assert.match(vectorStorage, /StorageProfileGovernance/)
  assert.match(storageProfiles, /reviseKnowledgeTypeStorageBindings/)
  assert.match(storageProfiles, /语义契约和已冻结 Quality Revision 原样克隆/)
})

test('output type configuration exposes semantics while runtime contracts stay read only', () => {
  assert.match(outputTypes, /业务 JSON Schema/)
  assert.match(outputTypes, /Canonical field/)
  assert.match(outputTypes, /Identity fields/)
  assert.match(outputTypes, /Quality Revision/)
  assert.doesNotMatch(outputTypes, /v-model="[^\"]*quality_profile_revision_id/)
  assert.doesNotMatch(outputTypes, /v-model="[^\"]*managed_collection/)
})

test('business customization is grouped and developer switching is task first', () => {
  assert.match(customize, /draftGroups/)
  assert.match(customize, /sourceItem\.groupKey !== target\.groupKey/)
  assert.match(layout, /'\/developer\/flow-templates'/)
  assert.doesNotMatch(layout, /'\/developer\/model-services'\)/)
})
