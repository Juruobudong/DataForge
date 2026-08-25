<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../../api/platform'

const SERVICE_STATUS_LABELS = { pending_configuration: '待配置', not_checked: '未检查', healthy: '正常' }
function serviceStatus(item) {
  if (!item?.is_enabled) return { label: '已停用' }
  return { label: SERVICE_STATUS_LABELS[item?.last_check_status] || '异常' }
}

const props = defineProps({ modelValue: { type: String, default: '' }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const servings = ref([]), error = ref('')
const choices = computed(() => servings.value.filter(item => item.is_enabled || item.serving_code === props.modelValue))
onMounted(async () => { try { servings.value = await api.modelServings() } catch (e) { error.value = e.message } })
</script>

<template>
  <label class="serving-selector">模型服务
    <select :value="modelValue" :disabled="disabled" @change="emit('update:modelValue', $event.target.value)">
      <option value="">系统默认{{ servings.find(item => item.is_default) ? ` · 当前：${servings.find(item => item.is_default).name}` : '' }}</option>
      <option v-for="item in choices" :key="item.id" :value="item.serving_code" :disabled="!item.is_enabled">{{ item.name }} · {{ serviceStatus(item).label }}</option>
    </select>
    <small v-if="error">{{ error }}</small>
  </label>
</template>

<style scoped>
.serving-selector{display:grid;gap:6px}.serving-selector small{color:#c94a4a}
</style>
