<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'

const props = defineProps({ items: { type: Array, required: true }, defaults: { type: Array, required: true } })
const emit = defineEmits(['close', 'save'])
const draft = ref([])
const panel = ref(null)
const closeButton = ref(null)
const draggedKey = ref('')

function copyItems(items) { return items.map(item => ({ ...item })) }
watch(() => props.items, value => { draft.value = copyItems(value) }, { immediate: true })
function move(index, offset) {
  const target = index + offset
  if (target < 0 || target >= draft.value.length) return
  const values = [...draft.value]
  const [item] = values.splice(index, 1)
  values.splice(target, 0, item)
  draft.value = values
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
  const source = draft.value.findIndex(item => item.key === draggedKey.value)
  const destination = draft.value.findIndex(item => item.key === target.key)
  if (source >= 0 && destination >= 0 && source !== destination) move(source, destination - source)
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
          <article v-for="(item,index) in draft" :key="item.key" class="menu-dialog-item" draggable="true" @dragstart="startDrag(item)" @dragover.prevent @drop="drop(item)">
            <span class="drag-handle" aria-hidden="true">☰</span>
            <div><b>{{ item.label }}</b><small>{{ item.caption }}</small></div>
            <div class="menu-move-actions"><button :disabled="index===0" :aria-label="`上移${item.label}`" @click="move(index,-1)">↑</button><button :disabled="index===draft.length-1" :aria-label="`下移${item.label}`" @click="move(index,1)">↓</button></div>
            <label><input type="checkbox" :checked="!item.hidden" :disabled="item.required" @change="toggle(item)"> {{ item.required ? '固定显示' : '显示' }}</label>
          </article>
        </div>
        <footer><button @click="restore">恢复默认</button><div><button @click="$emit('close')">取消</button><button class="primary" @click="save">完成</button></div></footer>
      </section>
    </div>
  </Teleport>
</template>
