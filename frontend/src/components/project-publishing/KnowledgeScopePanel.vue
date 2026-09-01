<script setup>
import { computed, ref } from 'vue'

const props = defineProps({ libraries: { type: Array, default: () => [] }, chosen: { type: Array, default: () => [] } })
const emit = defineEmits(['toggle', 'move', 'reorder'])
const draggedId = ref(''), dropTargetId = ref(''), dropAfter = ref(false)

const selectedLibraries = computed(() => props.chosen
  .map(id => props.libraries.find(library => library.id === id))
  .filter(Boolean))
const availableLibraries = computed(() => props.libraries.filter(library => !props.chosen.includes(library.id)))

function resetDrag() { draggedId.value = ''; dropTargetId.value = ''; dropAfter.value = false }
function insertionAfter(event) {
  const bounds = event.currentTarget?.getBoundingClientRect?.()
  return Boolean(bounds && event.clientY >= bounds.top + bounds.height / 2)
}
function startDrag(event, id) {
  draggedId.value = id
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', id)
  }
}
function showDropTarget(event, targetId) {
  event.preventDefault()
  if (!draggedId.value || draggedId.value === targetId) { dropTargetId.value = ''; dropAfter.value = false; return }
  dropTargetId.value = targetId; dropAfter.value = insertionAfter(event)
}
function clearDropTarget(event, targetId) {
  if (event.currentTarget?.contains(event.relatedTarget)) return
  if (dropTargetId.value === targetId) { dropTargetId.value = ''; dropAfter.value = false }
}
function drop(event, targetId) {
  event.preventDefault()
  if (draggedId.value && draggedId.value !== targetId) {
    emit('reorder', { id: draggedId.value, targetId, after: insertionAfter(event) })
  }
  resetDrag()
}
</script>

<template>
  <div class="stack">
    <p v-if="!libraries.length" class="muted">当前检索通道没有可用的 Ready 知识库。</p>
    <section v-if="selectedLibraries.length" class="scope-group">
      <header><b>已授权知识库</b><small>拖拽调整优先级；保存后生效。</small></header>
      <article v-for="library in selectedLibraries" :key="library.id" class="stat-card priority-card" :class="{
        dragging: draggedId === library.id,
        'drop-before': dropTargetId === library.id && !dropAfter,
        'drop-after': dropTargetId === library.id && dropAfter,
      }" :data-priority-library="library.id" draggable="true" @dragstart="startDrag($event, library.id)" @dragover="showDropTarget($event, library.id)" @dragleave="clearDropTarget($event, library.id)" @drop="drop($event, library.id)" @dragend="resetDrag">
        <span class="drag-handle" aria-hidden="true">⠿</span>
        <div class="priority-main"><label><input type="checkbox" checked @change="emit('toggle',library.id)"> {{ library.name }}</label><b>优先级 {{ chosen.indexOf(library.id)+1 }}</b></div>
        <div class="actions"><button type="button" :disabled="chosen.indexOf(library.id)===0" @click="emit('move',library.id,-1)">↑ 上移</button><button type="button" :disabled="chosen.indexOf(library.id)===chosen.length-1" @click="emit('move',library.id,1)">↓ 下移</button></div>
      </article>
    </section>
    <section v-if="availableLibraries.length" class="scope-group">
      <header><b>可选知识库</b><small>勾选后加入授权并显示在上方。</small></header>
      <div v-for="library in availableLibraries" :key="library.id" class="stat-card" :data-available-library="library.id">
        <label><input type="checkbox" @change="emit('toggle',library.id)"> {{ library.name }}</label>
      </div>
    </section>
  </div>
</template>

<style scoped>
.scope-group{display:grid;gap:8px}.scope-group>header{display:flex;justify-content:space-between;gap:12px;align-items:baseline}.scope-group small{color:var(--muted)}.priority-card{position:relative;display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:10px;cursor:grab}.priority-card.dragging{opacity:.48;cursor:grabbing}.drag-handle{color:var(--muted);font-size:20px;line-height:1}.priority-main{display:flex;align-items:center;justify-content:space-between;gap:12px}.priority-card.drop-before::before,.priority-card.drop-after::after{position:absolute;right:8px;left:8px;height:3px;border-radius:3px;background:var(--blue);content:""}.priority-card.drop-before::before{top:-3px}.priority-card.drop-after::after{bottom:-3px}@media(max-width:680px){.scope-group>header,.priority-main{align-items:flex-start;flex-direction:column}.priority-card{grid-template-columns:auto minmax(0,1fr)}.priority-card .actions{grid-column:2}}
</style>
