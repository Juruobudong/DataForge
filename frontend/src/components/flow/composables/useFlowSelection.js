import { computed } from 'vue'

export function useFlowSelection(nodes, edges) {
  const selectedNodes = computed(() => nodes.value.filter(node => node.selected))
  const selectedEdges = computed(() => edges.value.filter(edge => edge.selected))
  function clear() {
    nodes.value.forEach(node => { node.selected = false })
    edges.value.forEach(edge => { edge.selected = false })
  }
  return { selectedNodes, selectedEdges, clear }
}
