import { buildCompatibilityMap } from './edgeCompatibility.js'

export function idleEdgeInteraction() {
  return {
    mode: 'idle', sourceNodeId: undefined, sourcePortId: undefined,
    targetNodeId: undefined, targetPortId: undefined, originalEdgeId: undefined,
    compatiblePorts: new Map(), snapTargetKey: undefined, hoveredPortKey: undefined,
    cancelled: false,
  }
}

export function beginEdgeInteraction(input) {
  const state = { ...idleEdgeInteraction(), ...input, cancelled: false }
  state.compatiblePorts = buildCompatibilityMap(state)
  return state
}

export function edgeNodeClasses(state, nodeId) {
  if (!state || state.mode === 'idle') return {}
  const entries = [...state.compatiblePorts.entries()].filter(([key]) => key.startsWith(`${nodeId}::`))
  const source = state.sourceNodeId === nodeId && state.mode !== 'reconnecting-source'
  const compatible = entries.some(([, result]) => result.allowed)
  return {
    'edge-source-node': source,
    'edge-compatible-node': !source && compatible,
    'edge-incompatible-node': !source && !compatible,
  }
}
