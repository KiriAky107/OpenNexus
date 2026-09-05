<script setup lang="ts">
import { computed, ref } from 'vue'
import { FolderOpened } from '@element-plus/icons-vue'
import AppDialog from './AppDialog.vue'
import AppIcon from './AppIcon.vue'
import { t } from '@/i18n'

const props = defineProps<{ kind: 'Skill' | 'Plugin'; install: (source: string | File) => Promise<unknown> }>()
const emit = defineEmits<{ close: []; installed: [] }>()
const path = ref('')
const mode = ref<'path' | 'zip'>('zip')
const fileInput = ref<HTMLInputElement>()
const file = ref<File | null>(null)
const busy = ref(false)
const error = ref('')
const title = computed(() => t(`安装 ${props.kind}`, `Install ${props.kind}`))
const manifest = computed(() => `${props.kind.toLowerCase()}.yaml`)
const ready = computed(() => mode.value === 'zip' ? Boolean(file.value) : Boolean(path.value.trim()))

function chooseFile(event: Event) {
  const input = event.target as HTMLInputElement
  file.value = null
  error.value = ''
  const selected = input.files?.[0]
  input.value = ''
  if (!selected) return
  if (!selected.name.toLowerCase().endsWith('.zip') || !selected.size || selected.size > 10 * 1024 * 1024) {
    error.value = t('请选择非空 ZIP 文件，大小不超过 10 MiB。', 'Choose a nonempty ZIP file up to 10 MiB.')
    return
  }
  file.value = selected
}

async function submit() {
  if (busy.value || !ready.value) return
  error.value = ''
  busy.value = true
  try {
    await props.install(mode.value === 'zip' ? file.value! : path.value.trim())
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
      <p class="muted">{{ t('导入 ZIP 或使用本地包目录，安装时会校验清单与依赖。', 'Import a ZIP or use a local directory. The manifest and dependencies are checked during installation.') }}</p>
      <div class="source-tabs" :aria-label="t('安装来源', 'Installation source')">
        <button v-for="item in (['zip', 'path'] as const)" :key="item" type="button" class="button-secondary" :aria-pressed="mode === item" :disabled="busy" @click="mode = item; error = ''">{{ item === 'zip' ? t('ZIP 文件', 'ZIP file') : t('本地目录', 'Local directory') }}</button>
      </div>
      <div v-if="mode === 'zip'" class="package-source">
        <AppIcon :icon="FolderOpened" :size="30" />
        <input ref="fileInput" class="zip-input" type="file" accept=".zip,application/zip" :disabled="busy" :aria-label="t('选择 ZIP 扩展包', 'Choose a ZIP extension package')" @change="chooseFile" />
        <button type="button" class="button-secondary" :disabled="busy" @click="fileInput?.click()">{{ file ? t('重新选择 ZIP', 'Choose another ZIP') : t('选择 ZIP 文件', 'Choose ZIP file') }}</button>
        <strong v-if="file" class="package-name">{{ file.name }} · {{ (file.size / 1024).toFixed(1) }} KiB</strong>
        <p class="muted">{{ t('根目录或唯一顶层文件夹中须包含', 'The root or single top-level folder must contain') }} <code>{{ manifest }}</code></p>
        <p class="subtle">{{ t('ZIP 最大 10 MiB，解压后最大 50 MiB，最多 2048 个条目。', 'Up to 10 MiB compressed, 50 MiB extracted, and 2048 entries.') }}</p>
      </div>
      <div v-else class="package-source">
        <AppIcon :icon="FolderOpened" :size="30" />
        <strong>{{ t('本地包目录', 'Local package directory') }}</strong>
        <p class="muted">{{ t('选择包含以下清单的完整解压目录：', 'Use the extracted directory containing:') }} <code>{{ manifest }}</code></p>
        <label class="package-field">
          <span>{{ t('目录路径', 'Directory path') }}</span>
          <input v-model="path" class="input" autofocus required :disabled="busy" :placeholder="t('粘贴本地包目录的完整路径', 'Paste the full package directory path')" aria-describedby="extension-path-help" />
        </label>
        <p id="extension-path-help" class="subtle">{{ t('路径须位于 AI Core 所在电脑。', 'The directory must be on the AI Core computer.') }}</p>
      </div>
      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
      <p v-if="busy" class="muted" role="status">{{ t('正在校验并安装，请稍候…', 'Validating and installing…') }}</p>
      <footer class="install-actions">
        <button type="button" class="button-secondary" :disabled="busy" @click="emit('close')">{{ t('取消', 'Cancel') }}</button>
        <button type="submit" class="button-primary" :disabled="busy || !ready">{{ busy ? t('安装中…', 'Installing…') : title }}</button>
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
.source-tabs { display: flex; gap: var(--space-sm); margin-top: var(--space-lg); }
.source-tabs [aria-pressed="true"] { border-color: var(--color-accent-primary); color: var(--color-accent-primary); background: var(--color-accent-soft); }
.zip-input { display: none; }
.package-name { overflow-wrap: anywhere; max-width: 100%; }
</style>
