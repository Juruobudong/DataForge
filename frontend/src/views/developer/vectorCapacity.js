export function formatVectorCapacity(item) {
  if (item?.available) return `${item.entity_count} / ${item.capacity_limit}`
  return item?.reason || 'Milvus 未配置'
}
