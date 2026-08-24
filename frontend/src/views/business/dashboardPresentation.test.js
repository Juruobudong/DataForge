import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { publicationRows, runtimeCards } from './dashboardPresentation.js'

test('runtime cards keep operational and asset metrics separate', () => {
  const cards = runtimeCards({
    instance_mode: 'central',
    runtime: {
      documents: { library_count: 2, file_count: 17 },
      tasks: { active_count: 3, alert_count: 1 },
      vector: { ready_count: 3, library_count: 3 },
      packages: { label: '待导出发布包', pending_count: 2 },
    },
  })
  assert.deepEqual(cards.map(item => item.value), [
    '2 个文档库 · 17 个文件', '3 个任务 · 1 个告警', '3 / 3 个知识库', '2 个',
  ])
  assert.equal(cards[1].tone, 'red')
})

test('local publication presentation exposes waiting and conflict states', () => {
  const cards = runtimeCards({ instance_mode: 'local', runtime: { packages: { pending_count: 3 } } })
  assert.equal(cards[3].label, '待处理导入任务')
  const rows = publicationRows({ mode: 'import', status_counts: { waiting: 2, conflict: 1, failed: 1 } })
  assert.deepEqual(rows.filter(item => item.count).map(item => [item.label, item.count]), [
    ['等待恢复', 2], ['待处理冲突', 1], ['失败', 1],
  ])
})

test('dashboard places system components directly after knowledge assets', () => {
  const template = readFileSync(new URL('./DashboardView.vue', import.meta.url), 'utf8')
  const runtimeIndex = template.indexOf('<h3>运行概览</h3>')
  const assetsIndex = template.indexOf('<h3>知识资产概览</h3>')
  const componentsIndex = template.indexOf('<h3>系统组件</h3>')
  const lowerPanelIndex = template.indexOf('class="dashboard-lower-grid"')
  assert.ok(runtimeIndex < assetsIndex && assetsIndex < componentsIndex && componentsIndex < lowerPanelIndex)
})
