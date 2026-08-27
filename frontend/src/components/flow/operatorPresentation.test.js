import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const canvas = readFileSync(new URL('./DataForgeFlowCanvas.vue', import.meta.url), 'utf8')
const operatorNode = readFileSync(new URL('./nodes/OperatorNode.vue', import.meta.url), 'utf8')
const subflowNode = readFileSync(new URL('./nodes/SubflowNode.vue', import.meta.url), 'utf8')
const advancedEditor = readFileSync(new URL('./advanced/AdvancedFlowEditor.vue', import.meta.url), 'utf8')
const compiledPreview = readFileSync(new URL('./standard/CompiledDagPreview.vue', import.meta.url), 'utf8')
const templateList = readFileSync(new URL('../../views/developer/TemplateListView.vue', import.meta.url), 'utf8')
const subgraphView = readFileSync(new URL('../../views/developer/SubgraphView.vue', import.meta.url), 'utf8')
const debugView = readFileSync(new URL('../../views/developer/DataFlowDebugView.vue', import.meta.url), 'utf8')

test('operator nodes render Chinese names with English subtitles and optional technical codes', () => {
  assert.match(operatorNode, /data\.meta\.name/)
  assert.match(operatorNode, /operatorNodeSubtitle\(props\.data\.meta, props\.showTechnicalCode\)/)
  assert.match(operatorNode, /<small>\{\{ headerSubtitle \}\}<\/small>/)
  assert.match(operatorNode, /showTechnicalCode/)
  assert.match(canvas, /showTechnicalCode: \{ type: Boolean, default: false \}/)
  assert.match(canvas, /:show-technical-code="showTechnicalCode"/)
  assert.match(subflowNode, /subflowSubtitle/)
  assert.match(canvas, /<SubflowNode[^>]*:show-technical-code="showTechnicalCode"/)
})

test('only advanced orchestration enables node technical codes', () => {
  assert.match(advancedEditor, /<DataForgeFlowCanvas[^>]*:show-technical-code="!fragment"/)
  for (const source of [compiledPreview, templateList, subgraphView, debugView]) {
    assert.doesNotMatch(source, /<DataForgeFlowCanvas[^>]*\sshow-technical-code(?:\s|@|\/?>)/)
  }
})
