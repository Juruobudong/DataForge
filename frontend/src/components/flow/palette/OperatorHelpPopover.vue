<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { operatorPrimaryName, runtimeArtifactLabel } from '../flowModel.js'

const props = defineProps({ items: { type: Array, default: () => [] } })
const state = ref(null), element = ref(null), position = ref({})
let anchor = null, closeTimer
const title = computed(() => operatorPrimaryName(state.value?.item || {}))
const runtime = computed(() => state.value?.item?.runtime_requirements || {})
const provider = computed(() => ({ dataforge: 'DataForge 平台', dataflow: 'DataFlow', custom: '自定义算子' }[state.value?.item?.provider || runtime.value.provider] || '未声明'))
const summary = computed(() => state.value?.item?.summary || state.value?.item?.description || '用途未声明')
const scenarios = computed(() => [...new Set(state.value?.item?.scenarios || [])].filter(value => value !== summary.value && value !== state.value?.item?.description && value !== runtime.value.limitations))
function ports(direction) {
  const values = Object.values(state.value?.item?.[`${direction}_ports`] || {})
  return [...new Set(values.flatMap(port => port.accepted_types || (port.output_by_input ? Object.values(port.output_by_input) : [port.artifact_type])).filter(Boolean))].map(type => runtimeArtifactLabel(type)).join(' / ') || '未声明'
}
function close() { clearTimeout(closeTimer); state.value = null; anchor = null }
function cancelClose() { clearTimeout(closeTimer) }
function reposition() {
  if (!state.value) return
  if (!anchor?.isConnected) return close()
  const box = anchor.getBoundingClientRect(), width = Math.min(state.value.mode === 'detail' ? 370 : 280, window.innerWidth - 24)
  const height = element.value?.getBoundingClientRect().height || 60
  const left = box.right + width + 12 <= window.innerWidth ? box.right + 8 : Math.max(12, box.left - width - 8)
  position.value = { left: `${left}px`, top: `${Math.max(12, Math.min(box.top, window.innerHeight - height - 12))}px`, width: `${width}px`, maxHeight: `${window.innerHeight - 24}px` }
}
function show(event, item, mode) {
  cancelClose()
  if (state.value?.pinned) return
  anchor = event.currentTarget
  state.value = { item, mode, pinned: false }
  nextTick(reposition)
}
function leave() {
  cancelClose()
  if (state.value?.pinned) return
  closeTimer = setTimeout(() => {
    if (!element.value?.contains(document.activeElement) && !anchor?.contains(document.activeElement)) close()
  }, 140)
}
function toggle(event, item) {
  cancelClose()
  if (state.value?.pinned && state.value.item.code === item.code) return close()
  anchor = event.currentTarget; state.value = { item, mode: 'detail', pinned: true }
  nextTick(reposition)
}
function outside(event) {
  if (element.value?.contains(event.target) || event.target.closest?.('[data-operator-info]')) return
  close()
}
function keydown(event) { if (event.key === 'Escape' && state.value) { close(); event.stopPropagation() } }
watch(() => props.items, items => { if (state.value && !items.some(item => item.code === state.value.item.code)) close() })
onMounted(() => {
  document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', keydown, true)
  window.addEventListener('resize', reposition); document.addEventListener('scroll', reposition, true)
})
onBeforeUnmount(() => {
  cancelClose(); document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', keydown, true)
  window.removeEventListener('resize', reposition); document.removeEventListener('scroll', reposition, true)
})
defineExpose({ show, leave, toggle, close, isOpen: code => state.value?.mode === 'detail' && state.value?.item.code === code })
</script>

<template>
  <Teleport to="body">
    <section v-if="state" ref="element" class="operator-help-popover" :class="state.mode" :style="position" :role="state.mode === 'detail' ? 'dialog' : 'tooltip'" :aria-label="`${title}说明`" :aria-modal="state.mode === 'detail' ? 'false' : undefined" @mouseenter="cancelClose" @mouseleave="leave" @focusin="cancelClose" @focusout="leave" @pointerdown.stop @click.stop @dblclick.stop @keydown.stop @dragstart.stop.prevent>
      <p v-if="state.mode === 'summary'" class="summary-text">{{ summary }}</p>
      <template v-else>
        <header><div><strong>{{ title }}</strong><small>{{ state.item.name || state.item.code }} · v{{ state.item.version ?? '未声明' }} · {{ provider }}</small></div><button type="button" aria-label="关闭算子说明" @click="close">×</button></header>
        <h4>用途</h4><p>{{ summary }}</p>
        <p v-if="state.item.description && state.item.description !== summary && state.item.description !== runtime.limitations">{{ state.item.description }}</p>
        <template v-if="scenarios.length"><h4>适用场景</h4><ul><li v-for="item in scenarios" :key="item">{{ item }}</li></ul></template>
        <dl><dt>输入</dt><dd>{{ ports('input') }}</dd><dt>输出</dt><dd>{{ ports('output') }}</dd><dt>数据行为</dt><dd>{{ runtime.data_behavior || '未声明' }}</dd><dt>模型调用</dt><dd>{{ runtime.uses_llm === true ? '需要 LLM 模型服务' : runtime.uses_llm === false ? '不使用 LLM' : '未声明' }}</dd><dt>运行资源</dt><dd>{{ runtime.resources || '未声明' }}<span v-if="runtime.model"> · {{ runtime.model }}</span></dd><dt>依赖状态</dt><dd>{{ state.item.dependency_status?.status === 'ready' ? '已就绪' : state.item.dependency_status?.reason || '未声明' }}</dd></dl>
        <h4>重要限制</h4><p>{{ runtime.limitations || '未声明' }}</p>
        <small class="usage">{{ state.pinned ? '已固定 · Esc 或点击外部关闭' : '点击 i 可固定说明' }}</small>
      </template>
    </section>
  </Teleport>
</template>

<style scoped>
.operator-help-popover{position:fixed;z-index:1200;box-sizing:border-box;padding:12px 14px;border:1px solid #dbe3ee;border-radius:10px;background:#fff;color:#344054;box-shadow:0 10px 28px #0f172a26;font-size:13px;line-height:1.6;overflow:auto;overflow-wrap:anywhere;overscroll-behavior:contain}.operator-help-popover.summary{pointer-events:none}.summary-text{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;margin:0}.operator-help-popover header{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.operator-help-popover strong{font-size:15px}.operator-help-popover small{display:block;color:#718096;font-size:12px}.operator-help-popover button{width:26px;min-height:26px;padding:0;border:0;background:#f1f5f9;flex-shrink:0}.operator-help-popover h4{font-size:13px;margin:12px 0 4px;color:#44546a}.operator-help-popover p{margin:4px 0;white-space:pre-line}.operator-help-popover ul{margin:4px 0;padding-left:18px}.operator-help-popover dl{display:grid;grid-template-columns:62px minmax(0,1fr);gap:7px;margin:12px 0}.operator-help-popover dt{color:#718096}.operator-help-popover dd{margin:0}.usage{padding-top:8px;border-top:1px solid #edf0f4}
</style>
