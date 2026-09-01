<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'

const props = defineProps({ items: { type: Array, required: true }, defaults: { type: Array, required: true } })
const emit = defineEmits(['close', 'save'])
const draft = ref([])
const panel = ref(null)
const closeButton = ref(null)
const draggedKey = ref('')

function copyItems(items) { return items.map(item => ({ ...item })) }
watch(() => props.items, value => { draft.value = copyItems(value) }, { immediate: true })
const draftGroups = computed(() => {
  const order = [...new Set(props.defaults.map(item => item.groupKey || 'default'))]
  return order.map(key => ({
    key,
    label: props.defaults.find(item => (item.groupKey || 'default') === key)?.groupLabel || '菜单',
    items: draft.value.filter(item => (item.groupKey || 'default') === key),
  })).filter(group => group.items.length)
})
function move(item, offset) {
  const groupItems = draft.value.filter(value => value.groupKey === item.groupKey)
  const groupIndex = groupItems.findIndex(value => value.key === item.key)
  const targetItem = groupItems[groupIndex + offset]
  if (!targetItem) return
  const index = draft.value.findIndex(value => value.key === item.key)
  const target = draft.value.findIndex(value => value.key === targetItem.key)
  const values = [...draft.value]
  ;[values[index], values[target]] = [values[target], values[index]]
  draft.value = values
}
function canMove(item, offset) {
  const groupItems = draft.value.filter(value => value.groupKey === item.groupKey)
  const index = groupItems.findIndex(value => value.key === item.key)
  return index + offset >= 0 && index + offset < groupItems.length
}
function toggle(item) { if (!item.required) item.hidden = !item.hidden }
function restore() { draft.value = copyItems(props.defaults).map(item => ({ ...item, hidden: false })) }
function save() {
  emit('save', {
    order: draft.value.map(item => item.key),
    hidden: draft.value.filter(item => item.hidden && !item.required).map(item => item.key),
  })
}
function startDrag(item) { draggedKey.value = item.key }
function drop(target) {
  const sourceItem = draft.value.find(item => item.key === draggedKey.value)
  if (!sourceItem || sourceItem.groupKey !== target.groupKey) { draggedKey.value = ''; return }
  const groupItems = draft.value.filter(item => item.groupKey === target.groupKey)
  const source = groupItems.findIndex(item => item.key === sourceItem.key)
  const destination = groupItems.findIndex(item => item.key === target.key)
  if (source >= 0 && destination >= 0 && source !== destination) {
    const values = [...draft.value]
    const sourceIndex = values.findIndex(item => item.key === sourceItem.key)
    const [moved] = values.splice(sourceIndex, 1)
    const targetIndex = values.findIndex(item => item.key === target.key)
    values.splice(destination > source ? targetIndex + 1 : targetIndex, 0, moved)
    draft.value = values
  }
  draggedKey.value = ''
}
function onKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); emit('close'); return }
  if (event.key !== 'Tab') return
  const focusable = [...(panel.value?.querySelectorAll('button, input, [tabindex]:not([tabindex="-1"])') || [])]
    .filter(element => !element.disabled)
  if (!focusable.length) return
  const first = focusable[0], last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
onMounted(() => nextTick(() => closeButton.value?.focus()))
</script>

<template>
  <Teleport to="body">
    <div class="menu-dialog-backdrop" @click.self="$emit('close')">
      <section ref="panel" class="menu-dialog" role="dialog" aria-modal="true" aria-labelledby="menu-dialog-title" @keydown="onKeydown">
        <header><div><small>业务工作区</small><h2 id="menu-dialog-title">自定义业务菜单</h2></div><button ref="closeButton" aria-label="关闭自定义菜单" @click="$emit('close')">关闭</button></header>
        <div class="menu-dialog-list">
          <section v-for="group in draftGroups" :key="group.key" class="menu-dialog-group">
            <h3>{{ group.label }}</h3>
            <article v-for="item in group.items" :key="item.key" class="menu-dialog-item" draggable="true" @dragstart="startDrag(item)" @dragover.prevent @drop="drop(item)">
              <span class="drag-handle" aria-hidden="true">☰</span>
              <div><b>{{ item.label }}</b><small>{{ item.caption }}</small></div>
              <div class="menu-move-actions"><button :disabled="!canMove(item,-1)" :aria-label="`在${group.label}中上移${item.label}`" @click="move(item,-1)">↑</button><button :disabled="!canMove(item,1)" :aria-label="`在${group.label}中下移${item.label}`" @click="move(item,1)">↓</button></div>
              <label><input type="checkbox" :checked="!item.hidden" :disabled="item.required" @change="toggle(item)"> {{ item.required ? '固定显示' : '显示' }}</label>
            </article>
          </section>
        </div>
        <footer><button @click="restore">恢复默认</button><div><button @click="$emit('close')">取消</button><button class="primary" @click="save">完成</button></div></footer>
      </section>
    </div>
  </Teleport>
</template>
