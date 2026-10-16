<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { mobileRequest } from './native'
import { mobileVaultPath, renderMobileMarkdown } from './mobileMarkdown'
const props = defineProps<{ content: string; path: string; label: string; imageUnavailable: string; imageLimit: string }>()
const emit = defineEmits<{ link: [href: string] }>()
const root = ref<HTMLElement>(), html = computed(() => renderMobileMarkdown(props.content))
let revision = 0, disposed = false
const urls = new Set<string>()
function clear() { for (const url of urls) URL.revokeObjectURL(url); urls.clear() }
function placeholder(node: HTMLElement, kind: 'unavailable' | 'limit') {
  node.dataset.imageStatus = kind
  node.textContent = kind === 'limit' ? props.imageLimit : `${props.imageUnavailable} ${node.getAttribute('aria-label') || ''}`
}
watch(() => [props.imageUnavailable, props.imageLimit], () => {
  root.value?.querySelectorAll<HTMLElement>('[data-image-status]').forEach(node => placeholder(node,node.dataset.imageStatus as 'unavailable' | 'limit'))
})
watch(() => [html.value, props.path], async () => {
  const owner = ++revision; clear(); await nextTick()
  if (disposed || owner !== revision || !root.value) return
  const nodes = [...root.value.querySelectorAll<HTMLElement>('[data-mobile-image]')]
  const cache = new Map<string, string>()
  let loaded = 0
  for (let i = 0; i < nodes.length; i++) {
    if (disposed || owner !== revision) return
    const node = nodes[i]!, alt = node.getAttribute('aria-label') || ''
    if (i >= 32) { placeholder(node,'limit'); continue }
    let href: string
    try { href = decodeURIComponent(node.dataset.mobileImage!) } catch { placeholder(node,'unavailable'); continue }
    const path = mobileVaultPath(props.path, href)
    if (!path) { placeholder(node,'unavailable'); continue }
    try {
      let url = cache.get(path)
      if (!url) {
        if (loaded >= 12 * 1024 * 1024) { placeholder(node,'limit'); continue }
        const result = await mobileRequest<{ content_base64: string }>({ kind: 'read_image', path })
        if (disposed || owner !== revision || !node.isConnected) return
        if (result.content_base64.length > 4 * Math.ceil(5 * 1024 * 1024 / 3)) throw Error('MOBILE_IMAGE_TOO_LARGE')
        const extension = path.split('.').at(-1)?.toLowerCase(), mime = extension === 'jpg' || extension === 'jpeg' ? 'image/jpeg' : `image/${extension}`
        const bytes = Uint8Array.from(atob(result.content_base64), c => c.charCodeAt(0))
        if (loaded + bytes.length > 12 * 1024 * 1024) { placeholder(node,'limit'); continue }
        loaded += bytes.length; url = URL.createObjectURL(new Blob([bytes], { type: mime })); urls.add(url); cache.set(path,url)
      }
      const image = document.createElement('img'); image.alt = alt; image.src = url; image.decoding = 'async'; image.loading = 'lazy'
      image.onerror = () => { if (!disposed && owner === revision) placeholder(node,'unavailable') }
      node.replaceChildren(image)
    } catch { if (!disposed && owner === revision) placeholder(node,'unavailable') }
  }
}, { immediate: true, flush: 'post' })
function click(event: MouseEvent) {
  const link = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[data-mobile-link]')
  if (!link || !root.value?.contains(link)) return
  event.preventDefault()
  try { emit('link', decodeURIComponent(link.dataset.mobileLink!)) } catch { /* An invalid original link stays inert. */ }
}
onBeforeUnmount(() => { disposed = true; revision++; clear() })
</script>

<template><article ref="root" class="mobile-preview" :aria-label="label" @click="click" v-html="html" /></template>
