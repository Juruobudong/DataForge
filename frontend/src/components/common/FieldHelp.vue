<script setup>
import { ref } from 'vue'

defineProps({ text: { type: String, required: true }, label: { type: String, default: '查看说明' } })
const open = ref(false)
</script>

<template>
  <span class="field-help" @mouseenter="open=true" @mouseleave="open=false" @focusin="open=true" @focusout="open=false" @keydown.esc="open=false">
    <button type="button" class="field-help-trigger" :aria-label="label" :aria-expanded="open" @click="open=!open">i</button>
    <span v-if="open" class="field-help-popover" role="status">{{ text }}</span>
  </span>
</template>

<style scoped>
.field-help { position: relative; display: inline-flex; margin-left: 6px; vertical-align: middle; }
.field-help-trigger { width: 18px; height: 18px; padding: 0; border-radius: 50%; font-size: 12px; font-weight: 700; }
.field-help-popover { position: absolute; z-index: 20; top: 24px; left: 0; width: 280px; padding: 10px 12px; border: 1px solid #dbe3ee; border-radius: 8px; background: white; color: #344054; box-shadow: 0 10px 28px rgb(15 23 42 / 14%); font-size: 13px; line-height: 1.55; white-space: pre-line; }
</style>
