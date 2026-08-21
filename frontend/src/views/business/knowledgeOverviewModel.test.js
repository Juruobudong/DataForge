import assert from 'node:assert/strict'
import test from 'node:test'
import { buildKnowledgeCards, filterKnowledgeLibraries, normalizeKnowledgeTypeFilter } from './knowledgeOverviewModel.js'

const libraries = [
  { id: 'text-1', name: '文本库', knowledge_type: 'text', knowledge_item_count: 35, collection_names: ['dataforge_text_knowledge'] },
  { id: 'triple-1', name: '三元组库', knowledge_type: 'graph', graph_mode: 'triple', knowledge_item_count: 354 },
  { id: 'semantic-1', name: '语义库', knowledge_type: 'graph', graph_mode: 'semantic', knowledge_item_count: 234 },
  { id: 'extension-1', name: '扩展库', knowledge_type: 'custom', knowledge_item_count: 9 },
]

test('fixed cards split graph modes and preserve explicit zero types', () => {
  const cards = buildKnowledgeCards(libraries)
  assert.deepEqual(cards.map(item => [item.key, item.libraryCount, item.itemCount]), [
    ['text', 1, 35], ['qa', 0, 0], ['graph:triple', 1, 354], ['graph:semantic', 1, 234],
  ])
})

test('fixed type filters do not hide extension libraries from the all view', () => {
  assert.equal(filterKnowledgeLibraries(libraries).length, 4)
  assert.deepEqual(filterKnowledgeLibraries(libraries, 'graph:triple').map(item => item.id), ['triple-1'])
  assert.deepEqual(filterKnowledgeLibraries(libraries, '', 'dataforge_text').map(item => item.id), ['text-1'])
  assert.equal(normalizeKnowledgeTypeFilter('custom'), '')
})
