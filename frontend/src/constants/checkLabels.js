export const CHECK_LABELS = Object.freeze({
  'ROUTING.CONFIG_COMPLETE': '业务配置',
  'RETRIEVAL.CONFIGURATION': '检索参数与重排服务',
  'INDEX_PROFILE.PUBLISHED': '索引配置',
  'KNOWLEDGE_LIBRARY.READY': '知识资产',
  'ASSET_VERSION.READY': '资产版本',
  'COLLECTION.FOUND': 'Collection',
  'PARTITION.FOUND': 'Partition',
  'MILVUS.REACHABLE': 'Milvus 实际连接',
})

export const checkLabel = code => CHECK_LABELS[code] || code
