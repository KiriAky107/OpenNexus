<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import logo from '../../src/assets/opennexus-logo.svg'
import { mobileCapabilities, mobileVault, type MobileVault } from './native'

const localeKey = 'opennexus.mobile.language'
function savedLanguage(): 'zh-CN' | 'en' {
  try {
    const value = localStorage.getItem(localeKey)
    if (value === 'en' || value === 'zh-CN') return value
  } catch { /* A restricted WebView can still use its system language. */ }
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
}
const language = ref(savedLanguage()), vault = ref<MobileVault | null>(null)
const loading = ref(false), error = ref(''), version = ref('')
const copy = computed(() => language.value === 'zh-CN' ? {
  title: '本机笔记', language: '界面语言', checking: '正在打开本机知识库…',
  ready: '本机知识库已打开', empty: '这里还没有笔记', count: '篇笔记',
  storage: '笔记保存在此应用的私有存储中。', retry: '重新读取',
  failed: '无法打开本机知识库', native: '请在 OpenNexus Android 应用中打开。',
} : {
  title: 'Notes on this device', language: 'Interface language', checking: 'Opening the local vault…',
  ready: 'Local vault opened', empty: 'No notes here yet', count: 'notes',
  storage: 'Notes are stored in this app’s private storage.', retry: 'Read again',
  failed: 'Could not open the local vault', native: 'Open this in the OpenNexus Android app.',
})
let revision = 0, disposed = false
async function load() {
  const request = ++revision
  loading.value = true; error.value = ''
  try {
    const capabilities = await mobileCapabilities()
    if (disposed || request !== revision) return
    version.value = capabilities.version
    const current = await mobileVault()
    if (disposed || request !== revision) return
    vault.value = current
  } catch (reason) {
    if (disposed || request !== revision) return
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (!disposed && request === revision) loading.value = false
  }
}
watch(language, value => {
  document.documentElement.lang = value
  try { localStorage.setItem(localeKey, value) } catch { /* Language remains usable for this session. */ }
}, { immediate: true })
onMounted(load)
onBeforeUnmount(() => { disposed = true; revision++ })
</script>

<template>
  <main class="mobile-shell">
    <header class="app-header">
      <div class="brand"><img :src="logo" alt="" /><strong>OpenNexus</strong></div>
      <label class="language"><span class="sr-only">{{ copy.language }}</span>
        <select v-model="language" :aria-label="copy.language"><option value="zh-CN">中文</option><option value="en">English</option></select>
      </label>
    </header>
    <section class="vault-card" :aria-busy="loading">
      <p class="eyebrow">{{ copy.title }}</p>
      <h1>{{ loading ? copy.checking : error ? copy.failed : copy.ready }}</h1>
      <p v-if="error" role="alert">{{ error === 'MOBILE_HOST_REQUIRED' ? copy.native : error }}</p>
      <template v-else-if="vault">
        <p class="note-count">{{ vault.note_count }} <span>{{ copy.count }}</span></p>
        <p v-if="vault.note_count === 0" class="muted">{{ copy.empty }}</p>
      </template>
      <button :disabled="loading" @click="load">{{ copy.retry }}</button>
    </section>
    <footer><p>{{ copy.storage }}</p><span v-if="version">OpenNexus {{ version }}</span></footer>
  </main>
</template>
