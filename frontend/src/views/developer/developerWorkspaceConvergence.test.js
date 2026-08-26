import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const menus = readFileSync(new URL('../../constants/workspaceMenus.js', import.meta.url), 'utf8')
const layout = readFileSync(new URL('../../layouts/WorkspaceLayout.vue', import.meta.url), 'utf8')
const standard = readFileSync(new URL('../../components/flow/standard/StandardFlowEditor.vue', import.meta.url), 'utf8')
const preprocessing = readFileSync(new URL('./PipelineListView.vue', import.meta.url), 'utf8')
const debug = readFileSync(new URL('./DataFlowDebugView.vue', import.meta.url), 'utf8')
const templates = readFileSync(new URL('./TemplateListView.vue', import.meta.url), 'utf8')

test('developer navigation exposes the fixed main flow and an always-visible resource group', () => {
  assert.match(menus, /文档预处理/)
  assert.match(menus, /开发者资源/)
  assert.match(menus, /算子组件/)
  assert.match(menus, /可复用子流程/)
  assert.match(layout, /v-if="item\.group"/)
  assert.doesNotMatch(layout, /<details[^>]*developer-resource/)
})

test('standard authoring is business configuration while advanced and runtime DAG stay separate', () => {
  assert.match(standard, /知识生成/)
  assert.match(standard, /质量治理/)
  assert.match(standard, /不绑定具体 KnowledgeLibrary/)
  assert.doesNotMatch(standard, /CompiledDagPreview|DataForgeFlowCanvas/)
  assert.match(debug, /mode="runtime"/)
  assert.match(templates, /<FieldHelp label="高级编排转换说明"/)
  assert.match(templates, /基于当前标准配置展开完整执行 DAG，并创建一个新的自定义高级流程。原标准流程不会被修改。/)
  assert.doesNotMatch(templates, /window\.confirm\('将基于当前标准配置展开完整执行 DAG/)
})

test('preprocessing and debug default to versioned builtin samples', () => {
  assert.match(preprocessing, /preprocessing-document-v1/)
  assert.match(preprocessing, /previewSourcePreparation/)
  assert.match(debug, /reviewed-medical-v1/)
  assert.match(debug, /虚拟空库 Diff/)
})
