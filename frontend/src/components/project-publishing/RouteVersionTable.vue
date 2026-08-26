<script setup>
import { statusLabel } from '../../constants/statusLabels'
defineProps({ versions: { type: Array, default: () => [] }, allowRollback: { type: Boolean, default: false } })
defineEmits(['preview','rollback'])
</script>

<template>
  <div class="table-wrap"><table><thead><tr><th>版本</th><th>环境</th><th>来源</th><th>状态</th><th>时间</th><th>操作</th></tr></thead><tbody><tr v-for="version in versions" :key="version.id"><td>v{{ version.version_no }}</td><td>{{ statusLabel(version.release_stage) }}</td><td>{{ version.origin }}</td><td><span class="badge" :class="version.status==='published'?'green':'amber'">{{ statusLabel(version.status) }}</span></td><td>{{ version.published_at || version.created_at || '—' }}</td><td><button @click="$emit('preview',version.version_no)">查看</button><button v-if="allowRollback&&version.status==='published'" @click="$emit('rollback',version.version_no)">回滚</button></td></tr><tr v-if="!versions.length"><td colspan="6" class="muted">当前环境还没有发布版本。</td></tr></tbody></table></div>
</template>
