<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useWorkspaceStore } from '@/stores/workspace'
import { useThemeStore } from '@/stores/theme'
import { useSettingsStore } from '@/stores/settings'
import { ArrowRight, Document, Folder, FolderOpened, Moon, Plus, Sunny } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'

const router = useRouter()
const workspaceStore = useWorkspaceStore()
const themeStore = useThemeStore()
const settingsStore = useSettingsStore()

const isLoading = ref(false)
const showCreateDialog = ref(false)
const newVaultName = ref('')
const newVaultPath = ref('')
const aiCoreStatus = ref<'checking' | 'running' | 'stopped'>('checking')

onMounted(async () => {
  await Promise.all([workspaceStore.loadRecentVaults(), settingsStore.loadDiagnostics()])
  const lastVaultPath = localStorage.getItem('last-vault-path')
  if (settingsStore.restoreLastVault && lastVaultPath) {
    await openVault(lastVaultPath)
    return
  }
  setTimeout(() => {
    aiCoreStatus.value = settingsStore.aiCoreStatus === 'running' ? 'running' : 'stopped'
  }, 800)
})

async function openVault(path: string) {
  isLoading.value = true
  try {
    await workspaceStore.openVault(path)
    router.push('/workspace')
  } finally {
    isLoading.value = false
  }
}

async function openFolderPicker() {
  // In Tauri this would use the native dialog
  // For web dev, simulate
  const path = prompt('请输入 Vault 路径（开发模式）', '/Users/demo/Documents/MyVault')
  if (path) {
    await openVault(path)
  }
}

async function createVault() {
  if (!newVaultName.value || !newVaultPath.value) return
  isLoading.value = true
  try {
    await workspaceStore.createVault(newVaultPath.value, newVaultName.value)
    router.push('/workspace')
  } finally {
    isLoading.value = false
    showCreateDialog.value = false
  }
}
</script>

<template>
  <div class="vault-entry">
    <div class="bg-decoration" />
    <div class="entry-container">
      <div class="brand-section">
        <div class="logo"><AppIcon :icon="Document" :size="56" /></div>
        <h1 class="app-title">知笔知己</h1>
        <p class="app-subtitle">本地优先的 AI 笔记软件</p>
      </div>

      <div class="vault-card">
        <h2 class="card-title">选择知识库</h2>
        <p class="card-desc">选择一个本地 Vault 开始你的知识之旅</p>

        <div v-if="workspaceStore.recentVaults.length" class="recent-vaults">
          <div class="section-label">最近打开</div>
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
          <button class="btn btn-primary" @click="openFolderPicker" :disabled="isLoading">
            <AppIcon :icon="FolderOpened" /> 打开本地 Vault
          </button>
          <button class="btn btn-secondary" @click="showCreateDialog = true" :disabled="isLoading">
            <AppIcon :icon="Plus" /> 创建新 Vault
          </button>
        </div>

        <div class="ai-core-status">
          <span class="status-dot" :class="aiCoreStatus" />
          <span v-if="aiCoreStatus === 'checking'">正在检查 AI Core 状态...</span>
          <span v-else-if="aiCoreStatus === 'running'" class="status-running">AI Core 运行正常</span>
          <span v-else class="status-stopped">AI Core 未启动（编辑功能仍可用）</span>
        </div>
      </div>

      <div class="footer-info">
        <span>v0.1.0</span>
        <button class="theme-toggle" @click="themeStore.toggleTheme()">
          <AppIcon :icon="themeStore.isDark ? Sunny : Moon" :size="15" />
          {{ themeStore.isDark ? '浅色' : '深色' }}
        </button>
      </div>
    </div>

    <!-- Create Vault Dialog -->
    <div v-if="showCreateDialog" class="dialog-overlay" @click.self="showCreateDialog = false">
      <div class="dialog">
        <h3>创建新 Vault</h3>
        <div class="form-group">
          <label>Vault 名称</label>
          <input v-model="newVaultName" type="text" placeholder="我的知识库" />
        </div>
        <div class="form-group">
          <label>存储路径</label>
          <input v-model="newVaultPath" type="text" placeholder="/path/to/vault" />
        </div>
        <div class="dialog-actions">
          <button class="btn btn-secondary" @click="showCreateDialog = false">取消</button>
          <button class="btn btn-primary" @click="createVault" :disabled="!newVaultName || !newVaultPath">创建</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vault-entry {
  height: 100vh;
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
  opacity: 0.5;
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
}

.brand-section {
  text-align: center;
}

.logo {
  font-size: 64px;
  margin-bottom: 12px;
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
  box-shadow: var(--shadow-lg);
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
  transition: all var(--motion-fast);

  &:hover {
    background: var(--color-accent-soft);
    border-color: var(--color-accent-secondary);
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
}

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
  transition: all var(--motion-fast);
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

.dialog-overlay {
  position: fixed;
  inset: 0;
  background: var(--color-background-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal);
}

.dialog {
  background: var(--color-surface-primary);
  border-radius: var(--radius-lg);
  padding: var(--space-xl);
  width: 90%;
  max-width: 400px;
  box-shadow: var(--shadow-xl);
}

.dialog h3 {
  margin: 0 0 var(--space-lg) 0;
  font-size: 18px;
}

.form-group {
  margin-bottom: var(--space-md);

  label {
    display: block;
    font-size: 13px;
    color: var(--color-text-secondary);
    margin-bottom: var(--space-xs);
  }

  input {
    width: 100%;
    padding: 8px 12px;
    background: var(--color-background-secondary);
    border: 1px solid var(--color-border-default);
    border-radius: var(--radius-md);
    font-size: 14px;
    color: var(--color-text-primary);
    outline: none;
    transition: border-color var(--motion-fast);

    &:focus {
      border-color: var(--color-border-focus);
    }
  }
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-sm);
  margin-top: var(--space-lg);
}
</style>
