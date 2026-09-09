<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useWorkspaceStore } from '@/stores/workspace'
import { useThemeStore } from '@/stores/theme'
import { useSettingsStore } from '@/stores/settings'
import { ArrowRight, Document, Folder, FolderOpened, Moon, Sunny } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { t } from '@/i18n'
import { ApiErrorClass } from '@/services/apiClient'
import { isDesktop } from '@/services/platform/desktop'

const router = useRouter()
const workspaceStore = useWorkspaceStore()
const themeStore = useThemeStore()
const settingsStore = useSettingsStore()

const isLoading = ref(false)
const aiCoreStatus = ref<'checking' | 'running' | 'stopped'>('checking')
const openError = ref('')

onMounted(() => { void initializeVault() })

async function initializeVault() {
  openError.value = ''
  void settingsStore.loadDiagnostics().then(() => {
    aiCoreStatus.value = settingsStore.aiCoreStatus === 'running' ? 'running' : 'stopped'
  }).catch(() => { aiCoreStatus.value = 'stopped' })
  try { await workspaceStore.loadRecentVaults() }
  catch (reason) { openError.value = reason instanceof Error ? reason.message : String(reason); return }
  const lastVaultPath = localStorage.getItem('last-vault-path')
  if (!isDesktop() && settingsStore.restoreLastVault && lastVaultPath) {
    await openVault(lastVaultPath)
  }
}

async function openVault(path: string) {
  if (isLoading.value) return
  isLoading.value = true
  openError.value = ''
  try {
    await workspaceStore.openVault(path)
    await router.push('/workspace')
    void settingsStore.loadDiagnostics()
  } catch (reason) {
    openError.value = reason instanceof Error ? reason.message : String(reason)
    if (reason instanceof ApiErrorClass && reason.code === 'WORKSPACE_PATH_MISMATCH') localStorage.removeItem('last-vault-path')
  } finally {
    isLoading.value = false
  }
}

async function openFolderPicker() {
  if (isDesktop()) { await openVault(''); return }
  const configured = workspaceStore.recentVaults[0]
  if (configured) await openVault(configured.path)
}
</script>

<template>
  <div class="vault-entry">
    <div class="bg-decoration" />
    <div class="entry-container">
      <div class="brand-section">
        <div class="logo"><AppIcon :icon="Document" :size="56" /></div>
        <h1 class="app-title">OpenNexus</h1>
        <p class="app-subtitle">{{ t('本地优先的 AI 笔记软件', 'A local-first AI note-taking app') }}</p>
      </div>

        <div class="vault-card">
          <button class="btn" @click="router.push('/logs')">{{ t('查看运行日志', 'View operation logs') }}</button>
        <div v-if="openError" class="error-banner" role="alert">{{ openError }} <button class="btn" @click="initializeVault" :disabled="isLoading">{{ t('重试', 'Retry') }}</button></div>
        <p v-if="isLoading" role="status">{{ t('正在打开知识库…', 'Opening knowledge base…') }}</p>
        <h2 class="card-title">{{ t('选择知识库', 'Select Knowledge Base') }}</h2>
        <p class="card-desc">{{ isDesktop() ? t('桌面预览：选择本地目录；自动连接本机 AI Core。', 'Desktop preview: choose a local folder and connect to the local AI Core.') : t('Web 联调模式连接 AI Core 当前配置的 Vault', 'Web development mode connects to the Vault configured in AI Core') }}</p>

        <div v-if="workspaceStore.recentVaults.length" class="recent-vaults">
          <div class="section-label">{{ t('最近打开', 'Recently opened') }}</div>
          <div class="vault-list">
            <button
              v-for="vault in workspaceStore.recentVaults"
              :key="vault.path"
              class="vault-item"
              @click="openVault(vault.path)"
              :disabled="isLoading"
            >
              <AppIcon class="vault-icon" :icon="Folder" :size="20" />
              <div class="vault-info">
                <div class="vault-name">{{ vault.name }}</div>
                <div class="vault-path">{{ vault.path }}</div>
              </div>
              <AppIcon class="vault-arrow" :icon="ArrowRight" :size="16" />
            </button>
          </div>
        </div>

        <div class="actions">
          <button class="btn btn-primary" @click="openFolderPicker" :disabled="isLoading || (!isDesktop() && !workspaceStore.recentVaults.length)">
            <AppIcon :icon="FolderOpened" /> {{ isDesktop() ? t('选择本地 Vault', 'Choose local Vault') : t('打开后端 Vault', 'Open backend Vault') }}
          </button>
        </div>

        <div class="ai-core-status">
          <span class="status-dot" :class="aiCoreStatus" />
          <span v-if="aiCoreStatus === 'checking'">{{ t('正在检查 AI Core 状态...', 'Checking AI Core status...') }}</span>
          <span v-else-if="aiCoreStatus === 'running'" class="status-running">{{ t('AI Core 运行正常', 'AI Core is running') }}</span>
          <span v-else class="status-stopped">{{ t('AI Core 未启动（编辑功能仍可用）', 'AI Core is offline (editing remains available)') }}</span>
        </div>
      </div>

      <div class="footer-info">
        <span>v0.1.0</span>
        <button class="theme-toggle" @click="themeStore.toggleTheme()">
          <AppIcon :icon="themeStore.isDark ? Sunny : Moon" :size="15" />
          {{ themeStore.isDark ? t('浅色', 'Light') : t('深色', 'Dark') }}
        </button>
      </div>
    </div>

  </div>
</template>

<style scoped>
.vault-entry {
  height: 100%;
  min-height: 0;
  width: 100vw;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-primary);
  position: relative;
  overflow: hidden;
}

.bg-decoration {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 20% 30%, var(--color-accent-soft) 0%, transparent 50%),
    radial-gradient(circle at 80% 70%, var(--color-info-soft) 0%, transparent 50%);
  opacity: 0.62;
}

.entry-container {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 32px;
  max-width: 480px;
  width: 90%;
  animation: entry-in var(--motion-slow) both;
}

.brand-section {
  text-align: center;
}

.logo {
  display: inline-grid;
  place-items: center;
  width: 84px;
  height: 84px;
  margin-bottom: 14px;
  border: 1px solid color-mix(in srgb, var(--color-accent-primary) 18%, transparent);
  border-radius: 24px;
  background: var(--color-surface-primary);
  color: var(--color-accent-primary);
  box-shadow: var(--shadow-lg);
}

.app-title {
  font-size: 32px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin-bottom: 8px;
  background: linear-gradient(135deg, var(--color-accent-primary), var(--color-info));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.app-subtitle {
  font-size: 15px;
  color: var(--color-text-secondary);
}

.vault-card {
  width: 100%;
  background: var(--color-surface-primary);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-xl);
  padding: var(--space-2xl);
  box-shadow: var(--shadow-xl);
}

.card-title {
  font-size: 20px;
  font-weight: 600;
  margin-bottom: 4px;
  color: var(--color-text-primary);
}

.card-desc {
  font-size: 14px;
  color: var(--color-text-secondary);
  margin-bottom: var(--space-xl);
}

.section-label {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-bottom: var(--space-sm);
  font-weight: 500;
}

.vault-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
  margin-bottom: var(--space-xl);
}

.vault-item {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  width: 100%;
  padding: var(--space-md);
  background: var(--color-background-secondary);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: left;
  transition: background-color var(--motion-fast), border-color var(--motion-fast), box-shadow var(--motion-fast), transform var(--motion-fast);

  &:hover {
    background: var(--color-accent-soft);
    border-color: var(--color-accent-secondary);
    box-shadow: var(--shadow-sm);
    transform: translateY(-1px);
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
}

.vault-icon {
  font-size: 20px;
  flex-shrink: 0;
}

.vault-info {
  flex: 1;
  min-width: 0;
}

.vault-name {
  font-weight: 500;
  color: var(--color-text-primary);
  font-size: 14px;
}

.vault-path {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.vault-arrow {
  color: var(--color-text-tertiary);
  font-size: 20px;
  transition: color var(--motion-fast), transform var(--motion-fast);
}

.vault-item:hover .vault-arrow { color: var(--color-accent-primary); transform: translateX(3px); }

.actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  margin-bottom: var(--space-lg);
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-sm);
  padding: 10px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color var(--motion-fast), border-color var(--motion-fast), box-shadow var(--motion-fast), transform var(--motion-fast);
  border: 1px solid transparent;

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  &.btn-primary {
    background: var(--color-accent-primary);
    color: var(--color-text-inverse);

    &:hover:not(:disabled) {
      background: var(--color-accent-primary-hover);
      transform: translateY(-1px);
      box-shadow: 0 7px 18px color-mix(in srgb, var(--color-accent-primary) 25%, transparent);
    }
  }

  &.btn-secondary {
    background: var(--color-surface-secondary);
    color: var(--color-text-primary);
    border-color: var(--color-border-default);

    &:hover:not(:disabled) {
      background: var(--color-background-hover);
    }
  }
}

.ai-core-status {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-size: 13px;
  color: var(--color-text-secondary);
  padding-top: var(--space-md);
  border-top: 1px solid var(--color-border-subtle);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-text-tertiary);

  &.checking {
    animation: pulse 1.5s infinite;
    background: var(--color-warning);
  }

  &.running { background: var(--color-success); }
  &.stopped { background: var(--color-text-tertiary); }
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.status-running { color: var(--color-success); }
.status-stopped { color: var(--color-text-tertiary); }

.footer-info {
  display: flex;
  align-items: center;
  gap: var(--space-lg);
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  color: var(--color-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--radius-sm);

  &:hover {
    background: var(--color-background-hover);
  }
}

@keyframes entry-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
</style>
