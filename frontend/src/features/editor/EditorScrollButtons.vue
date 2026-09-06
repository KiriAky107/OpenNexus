<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Top, Bottom } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { t } from '@/i18n'

const props = defineProps<{ container: HTMLElement | null; content: string }>()
const hasContent = computed(() => !!props.content.trim())
const canUp = ref(false), canDown = ref(false)
let viewport: HTMLElement | null = null
let frame = 0
let mutation: MutationObserver | undefined
let resize: ResizeObserver | undefined
function update() {
  frame = 0
  const next = props.container?.querySelector<HTMLElement>('.milkdown-host, textarea.source') ?? null
  if (next !== viewport) {
    viewport?.removeEventListener('scroll', schedule)
    resize?.disconnect()
    viewport = next
    viewport?.addEventListener('scroll', schedule, { passive: true })
  }
  if (viewport) {
    resize?.observe(viewport)
    for (const child of viewport.children) resize?.observe(child)
    const document = viewport.querySelector('.ProseMirror')
    if (document) resize?.observe(document)
  }
  const max = viewport ? viewport.scrollHeight - viewport.clientHeight : 0
  const visible = hasContent.value && max > 2
  canUp.value = visible && viewport!.scrollTop > 2
  canDown.value = visible && viewport!.scrollTop < max - 2
}
function schedule() { if (!frame) frame = requestAnimationFrame(update) }
function scroll(to: 'top' | 'bottom') {
  viewport?.scrollTo({ top: to === 'top' ? 0 : viewport.scrollHeight,
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
}
watch(() => props.container, container => {
  mutation?.disconnect()
  resize?.disconnect()
  mutation = new MutationObserver(schedule)
  resize = new ResizeObserver(schedule)
  if (container) mutation.observe(container, { childList: true, subtree: true, characterData: true, attributes: true })
  schedule()
}, { immediate: true, flush: 'post' })
watch(() => props.content, schedule)
onBeforeUnmount(() => {
  cancelAnimationFrame(frame)
  mutation?.disconnect()
  resize?.disconnect()
  viewport?.removeEventListener('scroll', schedule)
})
</script>

<template>
  <div v-if="canUp || canDown" class="editor-scroll-buttons" :aria-label="t('文档滚动', 'Document scrolling')">
    <button v-if="canUp" type="button" class="button-secondary" :title="t('滑动到顶部', 'Scroll to top')" :aria-label="t('滑动到顶部', 'Scroll to top')" @click="scroll('top')"><AppIcon :icon="Top" :size="20" /></button>
    <button v-if="canDown" type="button" class="button-secondary" :title="t('滑动到底部', 'Scroll to bottom')" :aria-label="t('滑动到底部', 'Scroll to bottom')" @click="scroll('bottom')"><AppIcon :icon="Bottom" :size="20" /></button>
  </div>
</template>

