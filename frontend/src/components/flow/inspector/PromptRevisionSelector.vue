<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../../api/platform'
const props = defineProps({ modelValue: { type: String, default: '' }, knowledgeType: { type: String, default: '' }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const templates = ref([]), error = ref('')
const choices = computed(() => templates.value.flatMap(item => (item.revisions || []).map(revision => ({ ...revision, template_name: item.name }))))
onMounted(async () => { try { templates.value = await api.promptTemplates({ status: 'published', knowledge_type: props.knowledgeType }) } catch (e) { error.value = e.message } })
</script>
<template><label class="revision-selector">Prompt 模板<select :value="modelValue" :disabled="disabled" @change="emit('update:modelValue',$event.target.value)"><option v-for="item in choices" :key="item.id" :value="item.id">{{ item.template_name }} · r{{ item.revision }} · 已发布 · {{ (item.knowledge_types || []).join('/') }}</option></select><small v-if="error">{{ error }}</small></label></template>
<style scoped>.revision-selector{display:grid;gap:6px}.revision-selector small{color:#c94a4a}</style>
