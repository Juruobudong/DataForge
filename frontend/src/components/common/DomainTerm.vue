<script setup>
import { computed } from 'vue'
import { domainTerm } from '../../constants/domainTerms'
import FieldHelp from './FieldHelp.vue'

const props = defineProps({ term: { type: String, required: true }, help: { type: String, default: '' } })
const value = computed(() => domainTerm(props.term))
const text = computed(() => [props.help, `技术对象：${value.value.technical}`].filter(Boolean).join('\n'))
</script>

<template><span>{{ value.label }}<FieldHelp :text="text" :label="`${value.label}说明`" /></span></template>
