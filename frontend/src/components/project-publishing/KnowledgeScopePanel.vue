<script setup>
defineProps({ libraries: { type: Array, default: () => [] }, chosen: { type: Array, default: () => [] } })
defineEmits(['toggle', 'move'])
</script>

<template>
  <div class="stack">
    <p v-if="!libraries.length" class="muted">当前运行任务没有可用的 Ready 知识库。</p>
    <div v-for="library in libraries" :key="library.id" class="stat-card">
      <label><input type="checkbox" :checked="chosen.includes(library.id)" @change="$emit('toggle',library.id)"> {{ library.name }}</label>
      <template v-if="chosen.includes(library.id)">
        <b>优先级 {{ chosen.indexOf(library.id)+1 }}</b>
        <div class="actions"><button type="button" :disabled="chosen.indexOf(library.id)===0" @click="$emit('move',library.id,-1)">↑ 上移</button><button type="button" :disabled="chosen.indexOf(library.id)===chosen.length-1" @click="$emit('move',library.id,1)">↓ 下移</button></div>
      </template>
    </div>
  </div>
</template>
