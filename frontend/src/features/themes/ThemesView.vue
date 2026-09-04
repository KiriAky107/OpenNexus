<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import { useThemeStore } from '@/stores/theme'
import { mockCommunityThemes } from '@/services/themePackageService'
import type { ThemePackageInspection } from '@/contracts'

const themeStore = useThemeStore()

const activeTab = ref<'installed' | 'community'>('installed')
const showImportDialog = ref(false)
const previewThemeId = ref<string | null>(null)
const actionError = ref('')

const shikiPreview = `\`\`\`typescript
const notes = await search('本地优先')
\`\`\``

const codeThemeLabel = computed(() => themeStore.resolvedCodeBlockTheme === 'github-dark'
  ? 'Shiki · GitHub Dark'
  : 'Shiki · GitHub Light')

const communityThemes = computed(() => mockCommunityThemes)

function handleFileImport(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = async () => {
    const content = reader.result as string
    try {
      const result = await themeStore.inspectThemePackage(content)
      if (result.compatible) {
        previewThemeId.value = result.manifest.theme_id
      }
    } catch (error) {
      actionError.value = error instanceof Error ? error.message : '导入失败'
    }
  }
  reader.readAsText(file)
  input.value = ''
}

async function confirmInstall(inspection: ThemePackageInspection) {
  try {
    // Web Mock 模式：使用社区主题的 CSS 作为演示
    const cssText = generateThemeCss(inspection.manifest.theme_id, inspection.manifest.is_dark)
    await themeStore.installThemeFromInspection(inspection.manifest, cssText)
    showImportDialog.value = false
    previewThemeId.value = null
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '安装失败'
  }
}

async function installFromCommunity(themeId: string) {
  actionError.value = ''
  try {
    await themeStore.installCommunityTheme(themeId)
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '安装失败'
  }
}

function generateThemeCss(themeId: string, isDark: boolean): string {
  if (isDark) {
    return `[data-theme="${themeId}"] {
  --color-background-primary: #1a1b26;
  --color-background-secondary: #24283b;
  --color-background-tertiary: #2f334d;
  --color-background-hover: #2d2f45;
  --color-background-active: #3d4261;
  --color-surface-primary: #24283b;
  --color-surface-secondary: #1a1b26;
  --color-surface-elevated: #2f334d;
  --color-text-primary: #c0caf5;
  --color-text-secondary: #9aa5ce;
  --color-text-tertiary: #565f89;
  --color-text-link: #7aa2f7;
  --color-accent-primary: #7aa2f7;
  --color-accent-primary-hover: #89b4fa;
  --color-accent-soft: #1e2352;
  --color-border-default: #3b3f5c;
  --color-border-subtle: #2f334d;
  --color-border-focus: #7aa2f7;
  --color-success: #9ece6a;
  --color-success-soft: #1f2a1a;
  --color-warning: #e0af68;
  --color-warning-soft: #2d2418;
  --color-error: #f7768e;
  --color-error-soft: #2d1a1f;
  --color-info: #7aa2f7;
  --color-info-soft: #1a2030;
}`
  }
  return `[data-theme="${themeId}"] {
  --color-background-primary: #ffffff;
  --color-background-secondary: #f8fafc;
  --color-background-tertiary: #eef2f7;
  --color-background-hover: #f1f5f9;
  --color-background-active: #e2e8f0;
  --color-surface-primary: #ffffff;
  --color-surface-secondary: #fafbfc;
  --color-surface-elevated: #ffffff;
  --color-text-primary: #1e293b;
  --color-text-secondary: #64748b;
  --color-text-tertiary: #94a3b8;
  --color-text-link: #3b82f6;
  --color-accent-primary: #3b82f6;
  --color-accent-primary-hover: #2563eb;
  --color-accent-soft: #dbeafe;
  --color-border-default: #e2e8f0;
  --color-border-subtle: #f1f5f9;
  --color-border-focus: #3b82f6;
}`
}

function previewCommunity(themeId: string) {
  // 临时切换预览
  const current = themeStore.currentThemeId
  themeStore.applyTheme(themeId)
  setTimeout(() => themeStore.applyTheme(current), 1500)
}

onMounted(() => {
  themeStore.loadCustomThemes()
})
</script>

<template>
  <section class="feature-page">
    <header class="feature-header">
      <div>
        <h1>主题</h1>
        <p>浏览、导入和管理主题，打造你的知识工作流。</p>
      </div>
      <div class="inline-actions">
        <button class="button-secondary" @click="showImportDialog = true">导入主题</button>
        <button class="button-secondary" @click="themeStore.resetToDefault()">恢复默认</button>
      </div>
    </header>

    <div v-if="actionError || themeStore.importError" class="error-banner">
      {{ actionError || themeStore.importError }}
    </div>

    <div class="tabs">
      <button
        class="tab-btn"
        :class="{ active: activeTab === 'installed' }"
        @click="activeTab = 'installed'"
      >已安装</button>
      <button
        class="tab-btn"
        :class="{ active: activeTab === 'community' }"
        @click="activeTab = 'community'"
      >社区主题</button>
    </div>

    <div v-if="activeTab === 'installed'" class="feature-grid themes">
      <button
        v-for="theme in themeStore.allThemes"
        :key="theme.theme_id"
        class="item-card theme-card"
        :class="{ selected: themeStore.currentThemeId === theme.theme_id }"
        @click="themeStore.applyTheme(theme.theme_id)"
      >
        <div class="theme-preview" :class="theme.is_dark ? 'preview-dark' : (theme.theme_id === 'sepia' ? 'preview-sepia' : 'preview-light')">
          <span></span><span></span><span></span><div></div>
        </div>
        <div class="theme-info">
          <div>
            <strong>{{ theme.name }}</strong>
            <p class="subtle">{{ theme.description }}</p>
          </div>
          <span v-if="themeStore.currentThemeId === theme.theme_id" class="badge success">使用中</span>
        </div>
        <p class="subtle">
          v{{ theme.version }} · {{ theme.builtin ? '内置主题' : theme.author }}
          <span v-if="!theme.builtin"> · 自定义</span>
        </p>
        <div v-if="!theme.builtin" class="theme-actions" @click.stop>
          <button class="link-btn danger" @click="themeStore.uninstallTheme(theme.theme_id)">卸载</button>
        </div>
      </button>
    </div>

    <div v-else class="feature-grid themes">
      <article
        v-for="theme in communityThemes"
        :key="theme.theme_id"
        class="item-card theme-card"
      >
        <div class="theme-preview" :class="theme.is_dark ? 'preview-dark' : 'preview-light'">
          <span></span><span></span><span></span><div></div>
        </div>
        <div class="theme-info">
          <div>
            <strong>{{ theme.name }}</strong>
            <p class="subtle">{{ theme.description }}</p>
          </div>
          <span class="badge" :class="theme.is_dark ? 'info' : 'success'">{{ theme.is_dark ? '深色' : '浅色' }}</span>
        </div>
        <p class="subtle">v{{ theme.version }} · {{ theme.author }}</p>
        <div class="theme-tags">
          <span v-for="tag in theme.tags" :key="tag" class="tag">{{ tag }}</span>
        </div>
        <div class="theme-actions">
          <button
            v-if="themeStore.isThemeInstalled(theme.theme_id)"
            class="button-secondary small"
            @click="themeStore.applyTheme(theme.theme_id)"
          >启用</button>
          <template v-else>
            <button class="button-secondary small" @click="previewCommunity(theme.theme_id)">预览</button>
            <button class="button-primary small" @click="installFromCommunity(theme.theme_id)">安装</button>
          </template>
        </div>
      </article>
    </div>

    <div class="panel preference-panel">
      <h2 class="panel-title">编辑器外观</h2>
      <div class="form-grid">
        <div class="field">
          <label>字号：{{ themeStore.fontEditorSize }}px</label>
          <input v-model.number="themeStore.fontEditorSize" type="range" min="12" max="24" />
        </div>
        <div class="field">
          <label>行高：{{ themeStore.lineHeight }}</label>
          <input v-model.number="themeStore.lineHeight" type="range" min="1.2" max="2.2" step="0.1" />
        </div>
        <div class="field">
          <label>字体</label>
          <select v-model="themeStore.fontEditorFamily" class="select">
            <option value="system-ui">系统字体</option>
            <option value="serif">衬线字体</option>
            <option value="var(--font-ui-mono)">等宽字体</option>
          </select>
        </div>
        <div class="field">
          <label>代码块样式</label>
          <select v-model="themeStore.codeBlockTheme" class="select">
            <option value="auto">跟随主题</option>
            <option value="github-light">GitHub Light</option>
            <option value="github-dark">GitHub Dark</option>
          </select>
          <small>Markdown 渲染使用对应的 Shiki GitHub 主题</small>
        </div>
      </div>
      <div
        class="editor-preview"
        :style="{
          fontSize: `${themeStore.fontEditorSize}px`,
          lineHeight: themeStore.lineHeight,
          fontFamily: themeStore.fontEditorFamily,
        }"
      >
        <div class="preview-heading">
          <h3>主题预览</h3>
          <span class="badge info">{{ codeThemeLabel }}</span>
        </div>
        <p>知识的价值不只在于保存，更在于被重新发现和使用。</p>
        <MarkdownContent class="code-theme-preview" :source="shikiPreview" />
      </div>
    </div>

    <div v-if="showImportDialog" class="modal-backdrop" @click.self="showImportDialog = false">
      <div class="modal import-modal">
        <span class="badge info">主题导入</span>
        <h2>导入主题包</h2>
        <p class="subtle">支持 YAML Manifest + CSS 主题包。主题将在安全沙箱中验证后安装。</p>

        <div v-if="themeStore.pendingInspection?.compatible" class="inspection-result">
          <div class="inspect-head">
            <strong>{{ themeStore.pendingInspection.manifest.name }}</strong>
            <span class="badge success">验证通过</span>
          </div>
          <div class="inspect-meta">
            <span>作者：{{ themeStore.pendingInspection.manifest.author }}</span>
            <span>版本：{{ themeStore.pendingInspection.manifest.version }}</span>
            <span>{{ themeStore.pendingInspection.manifest.is_dark ? '深色主题' : '浅色主题' }}</span>
          </div>
          <p v-if="themeStore.pendingInspection.manifest.description" class="inspect-desc">
            {{ themeStore.pendingInspection.manifest.description }}
          </p>
          <div v-if="themeStore.pendingInspection.warnings.length" class="warnings">
            <p v-for="w in themeStore.pendingInspection.warnings" :key="w" class="warning-text">⚠ {{ w }}</p>
          </div>
        </div>

        <div v-else class="upload-area">
          <input type="file" accept=".yaml,.yml,.css,.zip" @change="handleFileImport" />
          <p>拖放主题包或点击选择文件</p>
          <p class="subtle">支持 .yaml / .yml / .css / .zip</p>
        </div>

        <div class="inline-actions">
          <button class="button-secondary" @click="showImportDialog = false">取消</button>
          <button
            v-if="themeStore.pendingInspection?.compatible"
            class="button-primary"
            @click="confirmInstall(themeStore.pendingInspection!)"
          >安装主题</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.themes { margin-bottom: var(--space-xl); }
.theme-card { display: grid; gap: var(--space-md); text-align: left; position: relative; }
.theme-preview {
  display: grid;
  grid-template-columns: 30px 1fr;
  grid-template-rows: repeat(3, 18px);
  gap: 6px;
  height: 120px;
  padding: var(--space-md);
  border-radius: var(--radius-md);
  background: #fff;
  border: 1px solid #ddd;
}
.theme-preview span { grid-column: 1; border-radius: 4px; background: #dfe3eb; }
.theme-preview div { grid-column: 2; grid-row: 1 / 4; border-radius: 6px; background: #f4f5f7; }
.preview-dark { background: #0d1117; border-color: #30363d; }
.preview-dark span { background: #30363d; }
.preview-dark div { background: #161b22; }
.preview-sepia { background: #fbf3df; border-color: #ddcfad; }
.preview-sepia span { background: #d8c69c; }
.preview-sepia div { background: #f4e8ca; }

.theme-info { display: flex; justify-content: space-between; gap: var(--space-md); align-items: flex-start; }
.theme-info strong { display: block; margin-bottom: 2px; }

.theme-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.tag {
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: var(--color-background-tertiary);
  color: var(--color-text-secondary);
  font-size: var(--font-size-xs);
}

.theme-actions { display: flex; gap: var(--space-sm); margin-top: 4px; }
.button-primary.small, .button-secondary.small {
  padding: 4px 12px;
  font-size: var(--font-size-sm);
}
.link-btn {
  background: none;
  border: none;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: var(--font-size-sm);
  padding: 0;
}
.link-btn.danger { color: var(--color-error); }
.link-btn:hover { text-decoration: underline; }

.tabs {
  display: flex;
  gap: var(--space-sm);
  margin-bottom: var(--space-lg);
  border-bottom: 1px solid var(--color-border-default);
}
.tab-btn {
  padding: var(--space-sm) var(--space-md);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: var(--font-size-md);
  margin-bottom: -1px;
  transition: all var(--motion-fast);
}
.tab-btn:hover { color: var(--color-text-primary); }
.tab-btn.active {
  color: var(--color-accent-primary);
  border-bottom-color: var(--color-accent-primary);
  font-weight: 500;
}

.preference-panel { display: grid; gap: var(--space-xl); }
.editor-preview {
  padding: var(--space-xl);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-background-secondary);
}
.editor-preview p { margin: var(--space-sm) 0; }
.preview-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-md);
}
.field small { color: var(--color-text-tertiary); }
.code-theme-preview { margin-top: var(--space-md); }

.import-modal {
  width: min(520px, 90vw);
  max-height: 80vh;
  overflow: auto;
}

.upload-area {
  padding: var(--space-2xl);
  border: 2px dashed var(--color-border-default);
  border-radius: var(--radius-md);
  text-align: center;
  margin: var(--space-lg) 0;
  transition: border-color var(--motion-fast);
}
.upload-area:hover { border-color: var(--color-accent-secondary); }
.upload-area input {
  display: block;
  margin: 0 auto var(--space-md);
}
.upload-area p { color: var(--color-text-secondary); }

.inspection-result {
  padding: var(--space-lg);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  margin: var(--space-lg) 0;
  background: var(--color-background-secondary);
}
.inspect-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-sm);
}
.inspect-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-md);
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-sm);
}
.inspect-desc {
  color: var(--color-text-primary);
  line-height: var(--line-height-relaxed);
}
.warnings {
  margin-top: var(--space-md);
  padding-top: var(--space-sm);
  border-top: 1px solid var(--color-border-default);
}
.warning-text {
  color: var(--color-warning);
  font-size: var(--font-size-sm);
}

.inline-actions { margin-top: var(--space-lg); justify-content: flex-end; gap: var(--space-sm); }
</style>
