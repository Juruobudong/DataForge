<script setup>
import { nextTick, ref, watch } from 'vue'

const props = defineProps({ chunk: { type: Object, required: true }, selected: Boolean, editing: Boolean, editContent: String, busy: Boolean })
const emit = defineEmits(['select', 'focus', 'edit', 'update:editContent', 'save', 'cancel', 'split', 'remove', 'review', 'reopen'])

const textareaEl = ref(null)
watch(() => props.editing, (editing) => { if (editing) nextTick(() => textareaEl.value?.focus()) })

function editByClick(chunk) {
  if (chunk.review_status === 'approved') return
  emit('edit', chunk)
}
</script>

<template>
  <article class="chunk-card" :class="[chunk.review_status, { focused: selected }]" @click="$emit('focus', chunk)">
    <header><label @click.stop><input type="checkbox" :checked="selected" @change="$emit('select', chunk, $event.target.checked)"> Chunk #{{ chunk.chunk_index + 1 }}<span v-if="chunk.anchor?.page"> · 第{{ chunk.anchor.page }}页</span></label><span class="badge" :class="chunk.review_status === 'approved' ? 'green' : chunk.review_status === 'rejected' ? 'red' : 'amber'">{{ chunk.review_status }}</span></header>
    <textarea v-if="editing" ref="textareaEl" :value="editContent" rows="8" @input="$emit('update:editContent', $event.target.value)"></textarea>
    <p v-else :class="{ editable: chunk.review_status !== 'approved' }" @click="editByClick(chunk)">{{ chunk.content }}</p>
    <footer v-if="editing"><button class="primary" :disabled="busy" @click.stop="$emit('save', chunk)">保存修改</button><button @click.stop="$emit('cancel')">取消</button></footer>
    <footer v-else-if="chunk.review_status === 'approved'"><button :disabled="busy" @click.stop="$emit('reopen', chunk)">重新打开</button></footer>
    <footer v-else><button @click.stop="$emit('split', chunk)">拆分</button><button class="danger" @click.stop="$emit('remove', chunk)">删除</button><button @click.stop="$emit('review', chunk, 'rejected')">拒绝</button><button class="primary" @click.stop="$emit('review', chunk, 'approved')">通过</button></footer>
  </article>
</template>

<style scoped>
.chunk-card{margin:12px;padding:12px;border:1px solid #dce3ee;border-radius:10px;background:#fff;cursor:pointer}.chunk-card.focused{box-shadow:0 0 0 2px #9fc0ff}.chunk-card.approved{border-color:#9fd5bf}.chunk-card.rejected{border-color:#e6aaaa}header,footer{display:flex;align-items:center;justify-content:space-between;gap:8px}footer{justify-content:flex-end;flex-wrap:wrap;margin-top:10px}p{white-space:pre-wrap;line-height:1.7}p.editable{cursor:text;border-radius:6px;transition:background .12s}p.editable:hover{background:var(--blue-soft)}textarea{width:100%;margin-top:10px;resize:vertical;line-height:1.6}
</style>
