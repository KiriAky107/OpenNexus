<script setup lang="ts">
import ExtensionRestoreNotice from '@/components/common/ExtensionRestoreNotice.vue'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
const { actionDialog, resolveAction, askConfirm } = useActionDialog()
import { Lightning } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import ExtensionInstallDialog from '@/components/common/ExtensionInstallDialog.vue'
import { onMounted, ref } from 'vue'
import { useSkillStore } from '@/stores/skill'
import { t } from '@/i18n'
import UserSkillEditor from './UserSkillEditor.vue'

const skillStore = useSkillStore()
const actionError = ref('')
const showInstall = ref(false)
const restoreNoticeKey = ref(0)
onMounted(() => { void skillStore.loadSkills() })

function installed() {
  showInstall.value = false
  actionError.value = ''
  restoreNoticeKey.value += 1
}


async function toggle(skillId: string, enabled: boolean) {
  try { enabled ? await skillStore.disableSkill(skillId) : await skillStore.enableSkill(skillId) } catch (error) { actionError.value = error instanceof Error ? error.message : t('状态更新失败', 'Status update failed') }
}
async function uninstall(skillId: string, name: string) {
  if (!(await askConfirm(`${t('确定卸载 Skill', 'Uninstall Skill')} “${name}”?`))) return
  try { await skillStore.uninstallSkill(skillId) } catch (error) { actionError.value = error instanceof Error ? error.message : t('卸载失败', 'Uninstall failed') }
}
</script>

<template>
  <section class="feature-page">
    <ExtensionRestoreNotice :key="restoreNoticeKey" kind="skill" />
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    <ExtensionInstallDialog v-if="showInstall" kind="Skill" :install="skillStore.installSkill" @close="showInstall = false" @installed="installed" />
    <header class="feature-header"><div><h1>{{ t('Skill 管理', 'Skill Management') }}</h1><p>{{ t('查看工作流使用的 Tool、权限、检索配置和模型要求。', 'Review the tools, permissions, retrieval settings, and model requirements used by workflows.') }}</p></div><button class="button-primary" @click="showInstall = true">{{ t('安装 Skill', 'Install Skill') }}</button></header>
    <UserSkillEditor />
    <div v-if="skillStore.error || actionError" class="error-banner">{{ skillStore.error || actionError }}</div>
    <div v-if="skillStore.selectedSkill" class="panel detail-panel">
      <div class="detail-head"><div><span class="badge" :class="{ success: skillStore.selectedSkill.status === 'ready', error: skillStore.selectedSkill.status === 'error', warning: skillStore.selectedSkill.status.includes('missing') }">{{ skillStore.selectedSkill.status }}</span><h2>{{ skillStore.selectedSkill.icon }} {{ skillStore.selectedSkill.name }}</h2><p class="muted">v{{ skillStore.selectedSkill.version }} · {{ skillStore.selectedSkill.author || t('未知作者', 'Unknown author') }}</p></div><div class="inline-actions"><button class="button-secondary" @click="toggle(skillStore.selectedSkill.skill_id, skillStore.selectedSkill.enabled)">{{ skillStore.selectedSkill.enabled ? t('停用', 'Disable') : t('启用', 'Enable') }}</button><button class="button-danger" @click="uninstall(skillStore.selectedSkill.skill_id, skillStore.selectedSkill.name)">{{ t('卸载', 'Uninstall') }}</button></div></div>
      <p class="description">{{ skillStore.selectedSkill.description }}</p>
      <div class="detail-grid"><div><h3>{{ t('工具', 'Tools') }}</h3><div class="tag-list"><span v-for="tool in skillStore.selectedSkill.tools" :key="tool" class="badge info">{{ tool }}</span></div></div><div><h3>{{ t('权限', 'Permissions') }}</h3><div class="tag-list"><span v-for="permission in skillStore.selectedSkill.permissions" :key="permission" class="badge warning">{{ permission }}</span></div></div><div><h3>{{ t('检索配置', 'Retrieval Settings') }}</h3><pre>{{ JSON.stringify(skillStore.selectedSkill.retrieval_config, null, 2) }}</pre></div><div><h3>{{ t('模型能力', 'Model Capabilities') }}</h3><div class="tag-list"><span v-for="cap in skillStore.selectedSkill.model_requirements?.capabilities" :key="cap" class="badge">{{ cap }}</span></div></div></div>
      <div v-if="skillStore.selectedSkill.missing_dependencies?.length" class="error-banner dependencies">{{ t('缺失依赖：', 'Missing dependencies: ') }}{{ skillStore.selectedSkill.missing_dependencies.join(', ') }}</div>
    </div>
    <div v-else-if="!skillStore.skills.length" class="empty-state"><div><strong>{{ skillStore.isLoading ? t('正在加载…', 'Loading…') : skillStore.error ? t('加载失败', 'Load failed') : t('尚未安装', 'Not installed') }}</strong><button class="button-secondary" @click="skillStore.loadSkills">{{ t('重新加载', 'Reload') }}</button></div></div>
    <div v-else class="feature-grid"><article v-for="skill in skillStore.skills" :key="skill.skill_id" class="item-card extension-card" @click="skillStore.selectSkill(skill.skill_id)"><div class="extension-title"><AppIcon :icon="Lightning" :size="22" /><div><strong>{{ skill.name }}</strong><p>v{{ skill.version }}</p></div><span class="badge" :class="{ success: skill.status === 'ready', warning: skill.status === 'dependency_missing' }">{{ skill.status }}</span></div><p class="muted">{{ skill.description }}</p><div class="tag-list"><span v-for="permission in skill.permissions.slice(0, 3)" :key="permission" class="badge">{{ permission }}</span></div></article></div>
  </section>
</template>

<style scoped>
.detail-head, .extension-title { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-md); }
.detail-head h2 { margin-top: var(--space-sm); }
.description { margin: var(--space-xl) 0; line-height: var(--line-height-relaxed); }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-xl); }
.detail-grid h3 { margin-bottom: var(--space-sm); }
pre { padding: var(--space-md); border-radius: var(--radius-md); background: var(--color-background-secondary); }
.dependencies { margin: var(--space-xl) 0 0; }
.extension-card { cursor: pointer; }
.extension-card > p { margin: var(--space-md) 0; }
.extension-title { align-items: center; }
.extension-title .icon { font-size: 28px; }
.extension-title p { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
</style>
