import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const listView = readFileSync(new URL('./TemplateListView.vue', import.meta.url), 'utf8')
const subgraphView = readFileSync(new URL('./SubgraphView.vue', import.meta.url), 'utf8')
const canvas = readFileSync(new URL('../../components/flow/DataForgeFlowCanvas.vue', import.meta.url), 'utf8')

test('可复用子图入口使用明确的完整 DAG 文案', () => {
  assert.match(listView, />查看完整 DAG</)
  assert.doesNotMatch(listView, /打开完整画布/)
  assert.match(subgraphView, /当前可复用子图 revision 的完整 DAG/)
})

test('内置子图各入口使用中文主标题和英文副标题', () => {
  assert.match(listView, /subflowPrimaryName\(item\)/)
  assert.match(listView, /subflowSubtitle\(item\)/)
  assert.match(listView, /\.subflow-title b,\.subflow-title small,\.subflow-title p \{ display:block; \}/)
  assert.match(subgraphView, /const detailName = computed/)
  assert.match(subgraphView, /class="subgraph-english-name"/)
  assert.match(subgraphView, /subflowPrimaryName\(item\)/)
})

test('完整子图页面区分加载、依赖失败、空数据和重试状态', () => {
  assert.match(subgraphView, /Promise\.allSettled/)
  assert.match(subgraphView, /算子目录加载失败/)
  assert.match(subgraphView, /子图目录加载失败/)
  assert.match(subgraphView, /当前 Revision 没有节点/)
  assert.match(subgraphView, />重新加载</)
  assert.match(subgraphView, /request !== loadRequest/)
})

test('共享画布等待 Vue Flow 节点初始化后完成 fit 请求', () => {
  assert.match(canvas, /@nodes-initialized="nodesInitialized"/)
  assert.match(canvas, /fitRequested = true/)
  assert.match(canvas, /nodesReady\.value/)
  assert.match(canvas, /requestAnimationFrame\(\(\) => fitView/)
})

test('共享画布将模板传入的纯数字高度规范化为 px', () => {
  assert.match(canvas, /const canvasHeight = computed/)
  assert.match(canvas, /\^\\d\+\(\?:\\\.\\d\+\)\?\$/)
  assert.match(canvas, /:style="\{ height: canvasHeight \}"/)
})
