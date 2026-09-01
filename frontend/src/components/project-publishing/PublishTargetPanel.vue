<script setup>
import EnvironmentTabs from './EnvironmentTabs.vue'

defineProps({ target: { type: Object, default: null }, selectedStage: { type: String, required: true }, targetUri: { type: String, default: '' }, showEnvironment: { type: Boolean, default: true } })
defineEmits(['update:selectedStage'])
</script>

<template>
  <section class="panel stack">
    <div class="panel-head"><div><h3>Milvus 发布目标</h3><p>{{ targetUri || '当前环境尚未绑定 Milvus Target' }}</p></div><span v-if="target" class="badge blue">所有项目共用</span></div>
    <template v-if="target">
      <EnvironmentTabs v-if="showEnvironment" :model-value="selectedStage" :target-uri="targetUri" @update:model-value="$emit('update:selectedStage',$event)" />
      <p>Target Revision 已固定；修改 Registry 不会自动改变当前环境绑定。</p>
    </template>
    <slot />
  </section>
</template>
