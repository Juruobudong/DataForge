<script setup>
import { ref } from 'vue'
import { useDialogFocus } from './composables/useDialogFocus'
defineProps({ pending: Boolean, error: String })
const emit = defineEmits(['save', 'discard', 'cancel'])
const panel = ref(null), trapFocus = useDialogFocus(panel)
</script>
<template>
  <div class="navigation-overlay" @keydown.esc="!pending && emit('cancel')"><section ref="panel" @keydown="trapFocus" role="dialog" aria-modal="true" aria-labelledby="unsaved-title"><h3 id="unsaved-title">当前流程有未保存修改</h3><p>查看子流程前，请选择如何处理修改。</p><p v-if="error" role="alert" class="error">{{ error }}</p><footer><button :disabled="pending" @click="emit('cancel')">取消</button><button :disabled="pending" @click="emit('discard')">放弃修改</button><button class="primary" :disabled="pending" @click="emit('save')">{{ pending ? '保存中…' : '保存后前往' }}</button></footer></section></div>
</template>
<style scoped>
.navigation-overlay{position:fixed;inset:0;z-index:1100;display:grid;place-items:center;background:#172b4d55}.navigation-overlay section{width:500px;padding:24px;border-radius:14px;background:#fff;box-shadow:0 16px 48px #172b4d33}.navigation-overlay h3{margin-top:0;font-size:20px}.navigation-overlay p{color:#65748b}.navigation-overlay footer{display:flex;gap:10px;justify-content:flex-end;margin-top:20px}
</style>
