<script setup lang="ts">
import { Lightning } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import { onMounted, ref } from 'vue'
import { useSkillStore } from '@/stores/skill'

const skillStore = useSkillStore()
const actionError = ref('')
onMounted(() => { void skillStore.loadSkills() })

async function install() {
  const path = prompt('请输入 Skill Package 路径')?.trim()
  if (!path) return
  try { await skillStore.installSkill(path) } catch (error) { actionError.value = error instanceof Error ? error.message : '安装失败' }
}
async function toggle(skillId: string, enabled: boolean) {
  try { enabled ? await skillStore.disableSkill(skillId) : await skillStore.enableSkill(skillId) } catch (error) { actionError.value = error instanceof Error ? error.message : '状态更新失败' }
}
async function uninstall(skillId: string, name: string) {
  if (!confirm(`确定卸载 Skill“${name}”吗？`)) return
  try { await skillStore.uninstallSkill(skillId) } catch (error) { actionError.value = error instanceof Error ? error.message : '卸载失败' }
}
</script>

<template>
  <section class="feature-page">
    <header class="feature-header"><div><h1>Skill 管理</h1><p>查看工作流使用的 Tool、权限、检索配置和模型要求。</p></div><button class="button-primary" @click="install">安装 Skill</button></header>
    <div v-if="skillStore.error || actionError" class="error-banner">{{ skillStore.error || actionError }}</div>
    <div v-if="skillStore.selectedSkill" class="panel detail-panel">
      <div class="detail-head"><div><span class="badge" :class="{ success: skillStore.selectedSkill.status === 'ready', error: skillStore.selectedSkill.status === 'error', warning: skillStore.selectedSkill.status.includes('missing') }">{{ skillStore.selectedSkill.status }}</span><h2>{{ skillStore.selectedSkill.icon }} {{ skillStore.selectedSkill.name }}</h2><p class="muted">v{{ skillStore.selectedSkill.version }} · {{ skillStore.selectedSkill.author || '未知作者' }}</p></div><div class="inline-actions"><button class="button-secondary" @click="toggle(skillStore.selectedSkill.skill_id, skillStore.selectedSkill.enabled)">{{ skillStore.selectedSkill.enabled ? '停用' : '启用' }}</button><button class="button-danger" @click="uninstall(skillStore.selectedSkill.skill_id, skillStore.selectedSkill.name)">卸载</button></div></div>
      <p class="description">{{ skillStore.selectedSkill.description }}</p>
      <div class="detail-grid"><div><h3>工具</h3><div class="tag-list"><span v-for="tool in skillStore.selectedSkill.tools" :key="tool" class="badge info">{{ tool }}</span></div></div><div><h3>权限</h3><div class="tag-list"><span v-for="permission in skillStore.selectedSkill.permissions" :key="permission" class="badge warning">{{ permission }}</span></div></div><div><h3>检索配置</h3><pre>{{ JSON.stringify(skillStore.selectedSkill.retrieval_config, null, 2) }}</pre></div><div><h3>模型能力</h3><div class="tag-list"><span v-for="cap in skillStore.selectedSkill.model_requirements?.capabilities" :key="cap" class="badge">{{ cap }}</span></div></div></div>
      <div v-if="skillStore.selectedSkill.missing_dependencies?.length" class="error-banner dependencies">缺失依赖：{{ skillStore.selectedSkill.missing_dependencies.join('、') }}</div>
    </div>
    <div v-else-if="!skillStore.skills.length" class="empty-state"><div><strong>{{ skillStore.isLoading ? '正在加载…' : skillStore.error ? '加载失败' : '尚未安装' }}</strong><button class="button-secondary" @click="skillStore.loadSkills">重新加载</button></div></div>
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
