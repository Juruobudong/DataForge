import { nextTick, onBeforeUnmount, onMounted } from 'vue'

export function useDialogFocus(panel) {
  let previous
  const focusable = () => [...(panel.value?.querySelectorAll('button,input,textarea,select,[tabindex="0"]') || [])].filter(item => !item.disabled)
  onMounted(async () => { previous = document.activeElement; await nextTick(); focusable()[0]?.focus() })
  onBeforeUnmount(() => { if (previous?.isConnected) previous.focus() })
  return event => {
    if (event.key !== 'Tab') return
    const items = focusable(), first = items[0], last = items.at(-1)
    if (!first) { event.preventDefault(); return }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }
}
