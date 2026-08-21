export const fixedKnowledgeCards = [
  { key: 'text', family: 'text', mode: null, icon: '文', name: '文本知识' },
  { key: 'qa', family: 'qa', mode: null, icon: '问', name: '问答知识' },
  { key: 'graph:triple', family: 'graph', mode: 'triple', icon: '△', name: '三元组图谱' },
  { key: 'graph:semantic', family: 'graph', mode: 'semantic', icon: '⬡', name: '语义图谱' },
]

export function normalizeKnowledgeTypeFilter(value) {
  return fixedKnowledgeCards.some(card => card.key === value) ? value : ''
}

function matchesType(library, card) {
  return library.knowledge_type === card.family && (card.mode === null || library.graph_mode === card.mode)
}

export function buildKnowledgeCards(libraries = []) {
  return fixedKnowledgeCards.map(card => {
    const values = libraries.filter(library => matchesType(library, card))
    return {
      ...card,
      libraryCount: values.length,
      itemCount: values.reduce((total, library) => total + (library.knowledge_item_count || 0), 0),
    }
  })
}

export function filterKnowledgeLibraries(libraries = [], type = '', keyword = '') {
  const card = fixedKnowledgeCards.find(item => item.key === normalizeKnowledgeTypeFilter(type))
  const needle = keyword.trim().toLocaleLowerCase()
  return libraries.filter(library => {
    if (card && !matchesType(library, card)) return false
    if (!needle) return true
    return [library.name, library.code, library.id, library.display_type, library.knowledge_type,
      ...(library.source_document_libraries || []).map(item => item.name), ...(library.collection_names || [])]
      .filter(Boolean).some(value => String(value).toLocaleLowerCase().includes(needle))
  })
}
