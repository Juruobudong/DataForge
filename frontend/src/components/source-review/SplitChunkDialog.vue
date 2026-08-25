<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({ chunk: { type: Object, required: true }, busy: Boolean })
const emit = defineEmits(['close', 'submit'])

const text = ref('')
watch(() => props.chunk?.id, () => { text.value = props.chunk?.content || '' }, { immediate: true })

const parts = computed(() => text.value.split(/\n\s*\n/).map(value => value.trim()).filter(Boolean))
function submit() { if (parts.value.length < 2) return; emit('submit', parts.value) }
</script>

<template>
  <div class="overlay" role="dialog" aria-modal="true" aria-label="拆分 Chunk">
    <section class="dialog">
      <h3>拆分 Chunk #{{ chunk.chunk_index + 1 }}</h3>
      <p class="hint">用空行分隔要拆分的文档块，保存后每个分段成为一个独立 Chunk。</p>
      <textarea v-model="text" class="content" rows="18" placeholder="在需要拆分的位置插入空行"></textarea>
      <footer>
        <span class="count" :class="{ warn: parts.length < 2 }">当前 {{ parts.length }} 段</span>
        <button @click="emit('close')">取消</button>
        <button class="primary" :disabled="busy || parts.length < 2" @click="submit">确认拆分</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.overlay{position:fixed;inset:0;z-index:40;display:grid;place-items:center;background:#10182866}
.dialog{width:min(760px,calc(100vw - 48px));max-height:calc(100vh - 80px);display:flex;flex-direction:column;padding:22px;border-radius:14px;background:#fff;box-shadow:var(--shadow)}
h3{margin:0 0 4px}
.hint{margin:8px 0 12px;color:var(--muted);font-size:var(--font-assist)}
.content{width:100%;flex:1;min-height:320px;resize:vertical;line-height:1.6}
footer{display:flex;justify-content:flex-end;align-items:center;gap:8px;margin-top:16px}
.count{color:var(--muted);font-size:var(--font-assist);margin-right:auto}.count.warn{color:#b4552d}
</style>
