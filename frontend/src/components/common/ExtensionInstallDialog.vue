<script setup lang="ts">
import { computed, ref } from 'vue'
import { FolderOpened } from '@element-plus/icons-vue'
import AppDialog from './AppDialog.vue'
import AppIcon from './AppIcon.vue'
import { t } from '@/i18n'

const props = defineProps<{ kind: 'Skill' | 'Plugin'; install: (path: string) => Promise<unknown> }>()
const emit = defineEmits<{ close: []; installed: [] }>()
const path = ref('')
const busy = ref(false)
const error = ref('')
const title = computed(() => t(`安装 ${props.kind}`, `Install ${props.kind}`))
const manifest = computed(() => `${props.kind.toLowerCase()}.yaml`)

async function submit() {
  if (busy.value || !path.value.trim()) return
  error.value = ''
  busy.value = true
  try {
    await props.install(path.value.trim())
    emit('installed')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('安装失败，请检查包目录后重试。', 'Installation failed. Check the package directory and retry.')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <AppDialog :label="title" :dismissible="!busy" @close="emit('close')">
    <form class="modal extension-install-modal" :aria-busy="busy" @submit.prevent="submit">
      <span class="badge info">{{ t('扩展安装', 'Extension installation') }}</span>
      <h2>{{ title }}</h2>
      <p class="muted">{{ t('从本地包目录安装，安装时会校验清单与依赖。', 'Install from a local package directory. The manifest and dependencies are checked during installation.') }}</p>
      <div class="package-source">
        <AppIcon :icon="FolderOpened" :size="30" />
        <strong>{{ t('本地包目录', 'Local package directory') }}</strong>
        <p class="muted">{{ t('选择包含以下清单的完整解压目录：', 'Use the extracted directory containing:') }} <code>{{ manifest }}</code></p>
        <label class="package-field">
          <span>{{ t('目录路径', 'Directory path') }}</span>
          <input v-model="path" class="input" autofocus required :disabled="busy" :placeholder="t('粘贴本地包目录的完整路径', 'Paste the full package directory path')" aria-describedby="extension-path-help" />
        </label>
        <p id="extension-path-help" class="subtle">{{ t('路径须位于 AI Core 所在电脑。ZIP 请先解压，再填写目录路径。', 'The directory must be on the AI Core computer. Extract ZIP packages before entering the directory path.') }}</p>
      </div>
      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
      <p v-if="busy" class="muted" role="status">{{ t('正在校验并安装，请稍候…', 'Validating and installing…') }}</p>
      <footer class="install-actions">
        <button type="button" class="button-secondary" :disabled="busy" @click="emit('close')">{{ t('取消', 'Cancel') }}</button>
        <button type="submit" class="button-primary" :disabled="busy || !path.trim()">{{ busy ? t('安装中…', 'Installing…') : title }}</button>
      </footer>
    </form>
  </AppDialog>
</template>

<style scoped>
.extension-install-modal { width: min(520px, 100%); }
h2 { margin: var(--space-sm) 0 var(--space-md); }
.package-source { display: grid; justify-items: center; gap: var(--space-md); margin: var(--space-lg) 0; padding: clamp(16px, 4vw, 28px); border: 2px dashed var(--color-border-default); border-radius: var(--radius-md); text-align: center; }
.package-source > .app-icon { color: var(--color-accent-primary); }
.package-field { display: grid; gap: var(--space-sm); width: 100%; min-width: 0; text-align: left; }
.package-field input { min-width: 0; }
.install-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--space-sm); margin-top: var(--space-lg); }
.error-banner { overflow-wrap: anywhere; }
</style>
