<script setup>
const props = defineProps({
  modelValue: { type: String, required: true },
  disabled: { type: Boolean, default: false },
  standardDisabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const modes = [
  { key: 'standard', name: '标准配置', disabledKey: 'standardDisabled' },
  { key: 'advanced', name: '高级编排', disabledKey: 'disabled' },
]
</script>

<template>
  <div class="flow-mode-switch" role="tablist">
    <button
      v-for="mode in modes"
      :key="mode.key"
      type="button"
      role="tab"
      :class="{ active: modelValue === mode.key }"
      :disabled="props[mode.disabledKey]"
      :aria-selected="modelValue === mode.key"
      @click="emit('update:modelValue', mode.key)"
    >
      {{ mode.name }}
    </button>
  </div>
</template>

<style scoped>
.flow-mode-switch {
  display: inline-flex;
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--border, #dfe5ed);
  border-radius: 9px;
  background: #eef2f7;
}
.flow-mode-switch button {
  border: 0;
  border-radius: 7px;
  padding: 5px 13px;
  background: transparent;
  color: #66758a;
  font-weight: 700;
  cursor: pointer;
}
.flow-mode-switch button.active {
  background: #ffffff;
  color: #2f6fed;
  box-shadow: 0 1px 2px rgba(17, 24, 39, 0.08);
}
.flow-mode-switch button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
</style>
