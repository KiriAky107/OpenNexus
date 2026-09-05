<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { lockDialogScroll } from './dialogScroll'
const props = withDefaults(defineProps<{ label: string; dismissible?: boolean }>(), { dismissible: true })
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
let restoreScroll: (() => void) | undefined
let previousFocus: HTMLElement | null = null
function dismiss() { if (props.dismissible) emit('close') }
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dismiss() }
  if (event.key === 'Tab' && dialog.value) {
    const items = Array.from(dialog.value.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled):not([type="hidden"]), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex]'))
      .filter(element => element.tabIndex >= 0 && element.getClientRects().length > 0)
    const first = items[0]
    const last = items.at(-1)
    if (!first) { event.preventDefault(); dialog.value.focus(); return }
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.value)) {
      event.preventDefault(); last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus()
    }
  }
}
onMounted(() => {
  previousFocus = document.activeElement as HTMLElement | null
  if (!dialog.value) return
  restoreScroll = lockDialogScroll(dialog.value)
  dialog.value.showModal()
  const first = dialog.value.querySelector<HTMLElement>('[autofocus], input:not(:disabled):not([type="hidden"]), textarea:not(:disabled), select:not(:disabled), button:not(:disabled)')
  ;(first ?? dialog.value).focus()
})
onBeforeUnmount(() => {
  dialog.value?.close()
  restoreScroll?.()
  if (previousFocus?.isConnected) previousFocus.focus()
})
</script>

<template>
  <dialog ref="dialog" class="app-dialog" :aria-label="label" tabindex="-1" @cancel.prevent="dismiss" @keydown="keydown" @click.self="dismiss">
    <slot />
  </dialog>
</template>

<style scoped>
.app-dialog { position: fixed; inset: 0; width: 100%; height: 100%; max-width: none; max-height: none; margin: 0; border: 0; padding: clamp(12px, 3vw, 24px); background: transparent; color: var(--color-text-primary); overflow: hidden; overscroll-behavior: contain; }
.app-dialog[open] { display: grid; place-items: center; }
.app-dialog::backdrop { background: var(--color-background-overlay); }
.app-dialog :deep(> .modal), .app-dialog :deep(> .modal-card) { min-width: 0; max-width: 100%; max-height: 100%; overflow: auto; overscroll-behavior: contain; }
</style>
