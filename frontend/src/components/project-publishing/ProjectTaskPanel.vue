<script setup>
defineProps({ count: { type: Number, default: 0 } })
</script>
<template><section class="panel"><div class="panel-head"><div><h3>任务配置</h3><p>先定义业务任务，再配置发布目标中的运行任务。</p></div><span class="badge blue">{{ count }} 个运行任务</span></div><slot /></section></template>
