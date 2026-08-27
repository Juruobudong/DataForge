import { computed, ref } from 'vue'
import { cloneGraph } from '../flowModel.js'

export function useFlowHistory(nodes, edges, limit = 40, extra = null) {
  const past = ref([]), future = ref([])
  const canUndo = computed(() => past.value.length > 0)
  const canRedo = computed(() => future.value.length > 0)
  const snapshot = () => ({ ...cloneGraph(nodes.value, edges.value), ...(extra ? { extra: JSON.parse(JSON.stringify(extra.value)) } : {}) })
  function remember() {
    past.value.push(snapshot())
    if (past.value.length > limit) past.value.shift()
    future.value = []
  }
  function restore(value) { nodes.value = value.nodes; edges.value = value.edges; if (extra) extra.value = value.extra }
  function undo() { if (!past.value.length) return; future.value.push(snapshot()); restore(past.value.pop()) }
  function redo() { if (!future.value.length) return; past.value.push(snapshot()); restore(future.value.pop()) }
  function clear() { past.value = []; future.value = [] }
  return { canUndo, canRedo, remember, undo, redo, clear }
}
