<script setup>
import { computed, ref } from 'vue'
const props = defineProps({ catalog: { type: Array, default: () => [] }, subflows: { type: Array, default: () => [] }, outputTypes: { type: Array, default: () => [] } })
const emit = defineEmits(['drag-start', 'add-item', 'add-sink'])
const query = ref('')
const groups = computed(() => {
  const visible = props.catalog.filter(item => item.exposure === 'canvas' && item.enabled !== false && `${item.name} ${item.code} ${item.category} ${item.description || ''}`.toLowerCase().includes(query.value.toLowerCase()))
  return Object.entries(visible.reduce((result, item) => {
    const category = item.category || '其他'
    ;(result[category] ||= []).push(item)
    return result
  }, {}))
})
</script>
<template>
  <aside class="operator-palette">
    <div class="palette-title"><div><h3>算子库</h3><small>{{ catalog.length }} 个受控算子</small></div><span>＋</span></div>
    <label class="search"><span>⌕</span><input v-model="query" aria-label="搜索算子" placeholder="搜索名称或编码"></label>
    <div class="palette-scroll">
      <section v-for="([category, items]) in groups" :key="category"><h4>{{ category }}</h4><button v-for="item in items" :key="item.code" draggable="true" @dragstart="emit('drag-start', $event, item, 'operator')" @dblclick="emit('add-item', item, 'operator')"><span class="item-icon">◇</span><span><b>{{ item.name }}</b><small :title="item.description || item.code">{{ item.description || item.code }}</small></span><span class="grab">⋮⋮</span></button></section>
      <section v-if="subflows.length"><h4>可复用子图</h4><button v-for="item in subflows" :key="item.code" class="subflow-item" draggable="true" @dragstart="emit('drag-start', $event, item, 'subflow')" @dblclick="emit('add-item', item, 'subflow')"><span class="item-icon">◈</span><span><b>{{ item.name }}</b><small>{{ item.code }} · r{{ item.revision }}</small></span><span class="grab">⋮⋮</span></button></section>
      <section v-if="outputTypes.length"><h4>正式知识输出</h4><button v-for="item in outputTypes" :key="item" class="sink-item" @dblclick="emit('add-sink', item)"><span class="item-icon">✓</span><span><b>{{ item }}</b><small>Knowledge Sink</small></span><span class="grab">＋</span></button></section>
    </div>
    <p class="hint">拖入画布，或双击添加到视口中心</p>
  </aside>
</template>
<style scoped>
.operator-palette{display:flex;width:220px;height:720px;flex:0 0 220px;flex-direction:column;overflow:hidden;border:1px solid var(--border);border-radius:12px;background:#fff;box-shadow:var(--shadow)}.palette-title{display:flex;align-items:center;justify-content:space-between;padding:14px 13px 10px}.palette-title h3{margin:0;font-size:12px}.palette-title small{color:#8490a2;font-size:8px}.palette-title>span{color:#2f6fed;font-size:18px}.search{display:flex;align-items:center;gap:6px;margin:0 10px 8px;padding:0 8px;border:1px solid #dfe5ee;border-radius:8px;background:#f9fbfd}.search input{width:100%;min-width:0;border:0!important;background:transparent!important;outline:0!important;box-shadow:none!important}.search span{color:#8190a5}.palette-scroll{flex:1;overflow-y:auto;padding:2px 9px 10px}.palette-scroll section+section{margin-top:11px}.palette-scroll h4{margin:6px 5px;color:#78859a;font-size:8px;letter-spacing:.06em}.palette-scroll button{display:grid;width:100%;min-height:48px;grid-template-columns:28px minmax(0,1fr) 16px;gap:8px;align-items:center;margin:4px 0;padding:7px 8px;text-align:left}.palette-scroll button:hover{border-color:#c9d8f3;background:#f8fbff}.item-icon{display:grid;width:27px;height:27px;place-items:center;border-radius:7px;color:#2f6fed;background:#eaf1ff}.palette-scroll b,.palette-scroll small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.palette-scroll b{font-size:9px}.palette-scroll small{margin-top:3px;color:#8290a4;font-size:7px}.grab{color:#b1bac8}.subflow-item .item-icon{background:#e6efff}.sink-item .item-icon{color:#1d8c65;background:#eaf7f1}.hint{margin:0;padding:9px 10px;border-top:1px solid #edf0f4;color:#8792a4;background:#fafbfd;font-size:7.5px;text-align:center}
</style>
