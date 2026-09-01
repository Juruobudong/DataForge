import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const menus = readFileSync(new URL('../../constants/workspaceMenus.js', import.meta.url), 'utf8')
const layout = readFileSync(new URL('../../layouts/WorkspaceLayout.vue', import.meta.url), 'utf8')
const standard = readFileSync(new URL('../../components/flow/standard/StandardFlowEditor.vue', import.meta.url), 'utf8')
const preprocessing = readFileSync(new URL('./PipelineListView.vue', import.meta.url), 'utf8')
const debug = readFileSync(new URL('./DataFlowDebugView.vue', import.meta.url), 'utf8')
const templates = readFileSync(new URL('./TemplateListView.vue', import.meta.url), 'utf8')

test('developer navigation exposes three fixed always-visible groups and defaults to knowledge flows', () => {
  assert.match(menus, /流程开发/)
  assert.match(menus, /能力配置/)
  assert.match(menus, /文档预处理/)
  assert.match(menus, /开发者资源/)
  assert.match(menus, /算子组件/)
  assert.match(menus, /可复用子流程/)
  assert.match(layout, /v-if="item\.group"/)
  assert.match(layout, /'\/developer\/flow-templates'/)
  assert.doesNotMatch(layout, /<details[^>]*developer-resource/)
})

test('standard authoring is business configuration while advanced and runtime DAG stay separate', () => {
  assert.match(standard, /知识生成/)
  assert.match(standard, /图谱校验/)
  assert.match(standard, /不绑定具体 KnowledgeLibrary/)
  assert.doesNotMatch(standard, /CompiledDagPreview|DataForgeFlowCanvas/)
  assert.match(debug, /mode="runtime"/)
  assert.match(templates, /<FieldHelp label="高级编排转换说明"/)
  assert.match(templates, /首次保存或运行前保存时才创建独立的自定义高级流程，直接退出不会创建草稿。原标准流程不会被修改。/)
  assert.doesNotMatch(templates, /window\.confirm\('将基于当前标准配置展开完整执行 DAG/)
})

test('preprocessing uses ParsedDocument and Flow-owned review while debug keeps versioned samples', () => {
  assert.match(preprocessing, /ParsedDocument/)
  assert.match(preprocessing, /document-chunker/)
  assert.doesNotMatch(preprocessing, /previewSourcePreparation/)
  assert.match(debug, /reviewed-medical-v2/)
  assert.match(debug, /虚拟空库 Diff/)
})
