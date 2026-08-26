<script setup>
import { statusLabel } from '../../constants/statusLabels'
defineProps({ modelValue: { type: String, required: true }, targetUri: { type: String, default: '' } })
defineEmits(['update:modelValue'])
</script>

<template>
  <div class="stack">
    <div class="tabs environment-tabs" role="tablist" aria-label="发布环境">
      <button v-for="stage in ['test','production']" :key="stage" type="button" role="tab" :aria-selected="modelValue===stage" :class="{active:modelValue===stage}" @click="$emit('update:modelValue',stage)">{{ statusLabel(stage) }}</button>
    </div>
    <p><b>{{ statusLabel(modelValue) }}</b> · <code>{{ targetUri || '尚未配置 Milvus 服务' }}</code></p>
  </div>
</template>
