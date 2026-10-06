<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { CommunityRelease, CommunitySource } from '@/contracts/community'
import { hostInvoke } from '@/services/platform/desktop'
import { useWorkspaceStore } from '@/stores/workspace'
import { findCompatibleUpdate, loadSources } from '@/services/communityService'
import { normalizedCommunitySource } from '@/services/communityCatalogCache'
import AppDialog from '@/components/common/AppDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{ refreshKey: number; sources?: CommunitySource[] }>()
const workspace = useWorkspaceStore()
type SignedRelease = Omit<CommunityRelease, 'release_id' | 'withdrawn' | 'download_path'>
interface Installed { slot: string; package_key: string; source: string; release: SignedRelease; revision: string; pending_operation: string | null; configuration: unknown; rollback_operation_id: string | null }
interface Page { items: Installed[]; total: number; offset: number; limit: number; app_version: string; platform: string; architecture: string }
interface Preview { fingerprint: string; dependencies: { packages: Array<{ package_key: string; namespace: string; package_id: string; version: string; permissions: string[] }> }; changes: Array<{ target: { package_key: string; configuration: unknown } }> }
const result = ref<Page | null>(null), offset = ref(0), busy = ref(false), error = ref('')
const selected = ref<Installed | null>(null), update = ref<CommunityRelease | null>(null), rollback = ref<Preview | null>(null), notice = ref('')
let generation = 0, controller: AbortController | undefined
function cancel() { ++generation; controller?.abort(); busy.value = false }
function clear() { cancel(); selected.value = null; update.value = null; rollback.value = null; error.value = ''; notice.value = '' }
function checkedPage(value: Page, requested: number): Page {
  if (!value || !Array.isArray(value.items) || value.items.length > 20 || !Number.isSafeInteger(value.total) || value.total < 0 || value.offset !== requested || value.limit !== 20 || value.items.length > Math.max(0, value.total - requested) || ![value.app_version, value.platform, value.architecture].every(field => typeof field === 'string' && field.length > 0)) throw new Error(t('安装状态响应无效，请刷新后重试。', 'Invalid installation state. Refresh and try again.'))
  const slots = new Set<string>()
  for (const item of value.items) {
    if (!item || ![item.slot, item.package_key, item.source, item.revision, item.release?.name, item.release?.version].every(field => typeof field === 'string' && field.length > 0) || slots.has(item.slot) || !Array.isArray(item.release.permissions) || !item.release.permissions.every(permission => typeof permission === 'string') || ![item.pending_operation, item.rollback_operation_id].every(field => field === null || typeof field === 'string')) throw new Error(t('安装状态响应无效，请刷新后重试。', 'Invalid installation state. Refresh and try again.'))
    slots.add(item.slot)
  }
  return value
}
async function refresh(target = offset.value) {
  clear(); result.value = null; offset.value = target
  const vaultId = workspace.vaultId, current = ++generation
  if (!vaultId) return
  busy.value = true
  try {
    const page = await hostInvoke<Page>('extension_installed', { vaultId, offset: target, limit: 20 })
    if (current === generation && workspace.vaultId === vaultId) {
      const valid = checkedPage(page, target)
      if (target > 0 && target >= valid.total) { void refresh(Math.max(0, Math.ceil(valid.total / 20) - 1) * 20); return }
      result.value = valid
    }
  } catch (reason) { if (current === generation) error.value = String(reason) }
  finally { if (current === generation) busy.value = false }
}
async function inspect(item: Installed, action: 'update' | 'rollback') {
  clear(); selected.value = item; busy.value = true
  const vaultId = workspace.vaultId, current = ++generation, revision = item.revision
  controller = new AbortController()
  try {
    const snapshot = result.value
    if (!vaultId || !snapshot || item.pending_operation) throw new Error(t('安装尚未完成，请先刷新状态。', 'Installation is incomplete. Refresh its status first.'))
    if (action === 'rollback') {
      if (!item.rollback_operation_id) return
      const value = await hostInvoke<Preview>('extension_rollback_preview', { installedOperationId: item.rollback_operation_id, vaultId })
      if (current === generation && workspace.vaultId === vaultId && selected.value?.revision === revision) rollback.value = value
    } else {
      const source = (props.sources ?? loadSources()).find(source => {
        try { return normalizedCommunitySource(source.url) === item.source } catch { return false }
      })
      if (!source) throw new Error(t('请先在社区来源中重新检查并确认此来源。', 'Review and confirm this source in the catalog first.'))
      const candidate = await findCompatibleUpdate(source, item.release, snapshot, controller.signal)
      if (current === generation && workspace.vaultId === vaultId && selected.value?.revision === revision) {
        update.value = candidate
        notice.value = candidate ? '' : t('此来源没有与当前应用、平台和架构兼容的新版本。', 'This source has no newer release compatible with this app, platform and architecture.')
      }
    }
  } catch (reason) { if (current === generation) error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { if (current === generation) busy.value = false }
}
watch(() => workspace.vaultId, () => { void refresh(0) }, { flush: 'sync' })
watch(() => props.refreshKey, () => { void refresh(0) })
watch(() => JSON.stringify(props.sources), () => { void refresh() }, { flush: 'sync' })
onMounted(() => refresh(0)); onBeforeUnmount(cancel)
</script>

<template>
  <section class="installed-packages panel" :aria-label="t('当前知识库的已安装包', 'Installed packages for this vault')">
    <header><div><h2>{{ t('已安装与更新', 'Installed packages and updates') }}</h2><p>{{ t('按当前知识库显示安装状态。查询更新与预览回滚不会启动扩展。', 'Installation state belongs to the current vault. Update checks and rollback previews do not start extensions.') }}</p></div><button class="button-secondary" :disabled="busy || !workspace.vaultId" @click="refresh()">{{ t('刷新安装状态', 'Refresh installations') }}</button></header>
    <p v-if="!workspace.vaultId">{{ t('请先打开知识库。', 'Open a vault first.') }}</p>
    <p v-if="busy" role="status">{{ t('正在核对…', 'Checking…') }}</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="result && !result.items.length && !busy">{{ t('本页没有已安装包。', 'There are no installed packages on this page.') }}</p>
    <ul v-if="result" class="installed-list"><li v-for="item in result.items" :key="item.slot" class="item-card">
      <div><strong>{{ item.release.name }} · {{ item.release.version }}</strong><p>{{ item.source }}</p><p>{{ item.pending_operation ? t('安装等待健康检查，尚未完成', 'Installation awaits health checks and is incomplete') : t('已安装；运行状态另行确认', 'Installed; runtime state is checked separately') }}</p></div>
      <div class="actions"><button class="button-secondary" :disabled="busy || !!item.pending_operation" @click="inspect(item, 'update')">{{ t('查询兼容更新', 'Check compatible updates') }}</button><button v-if="item.rollback_operation_id" class="button-secondary" :disabled="busy || !!item.pending_operation" @click="inspect(item, 'rollback')">{{ t('预览回滚', 'Preview rollback') }}</button></div>
    </li></ul>
    <nav v-if="result && result.total > 0" :aria-label="t('已安装包分页', 'Installed package pages')"><button class="button-secondary" :disabled="busy || offset === 0" @click="refresh(offset - 20)">{{ t('上一页', 'Previous') }}</button><span>{{ t(`第 ${Math.floor(offset / 20) + 1} / ${Math.ceil(result.total / 20)} 页 · ${result.total} 项`, `Page ${Math.floor(offset / 20) + 1} / ${Math.ceil(result.total / 20)} · ${result.total} packages`) }}</span><button class="button-secondary" :disabled="busy || offset + 20 >= result.total" @click="refresh(offset + 20)">{{ t('下一页', 'Next') }}</button></nav>
    <AppDialog v-if="selected" :label="t('更新与回滚预览', 'Update and rollback preview')" @close="clear"><section class="modal-card package-review">
      <h2>{{ selected.release.name }} · {{ selected.release.version }}</h2><p v-if="busy" role="status">{{ t('正在核对…', 'Checking…') }}</p><p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <template v-if="update"><h3>{{ selected.release.version }} → {{ update.version }}</h3><p>{{ update.description }}</p><h4>{{ t('变更说明', 'Changelog') }}</h4><pre>{{ update.changelog }}</pre><h4>{{ t('新增权限', 'Added permissions') }}</h4><p>{{ update.permissions.filter(permission => !selected!.release.permissions.includes(permission)).join('、') || t('无', 'None') }}</p><h4>{{ t('移除权限', 'Removed permissions') }}</h4><p>{{ selected.release.permissions.filter(permission => !update!.permissions.includes(permission)).join('、') || t('无', 'None') }}</p></template>
      <template v-if="rollback"><h3>{{ t('拟恢复的版本', 'Versions to restore') }}</h3><ul><li v-for="item in rollback.dependencies.packages" :key="item.package_key">{{ item.namespace }}/{{ item.package_id }} · {{ item.version }}<p>{{ t('权限：', 'Permissions: ') }}{{ item.permissions.join('、') || t('无', 'None') }}</p></li></ul><details><summary>{{ t('拟恢复的配置', 'Configurations to restore') }}</summary><pre>{{ JSON.stringify(rollback.changes.map(change => change.target.configuration), null, 2) }}</pre></details></template>
      <button class="button-secondary" @click="clear">{{ t('关闭', 'Close') }}</button>
    </section></AppDialog>
  </section>
</template>

<style scoped>
.installed-packages { display: grid; gap: var(--space-md); padding: var(--space-xl); margin-block: var(--space-xl); border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-surface-primary); }
header, .item-card { display: flex; gap: var(--space-md); align-items: flex-start; justify-content: space-between; }
h2, p { margin: 0; } p { margin-top: var(--space-xs); color: var(--color-text-secondary); overflow-wrap: anywhere; }
.installed-list { display: grid; gap: var(--space-md); list-style: none; padding: 0; margin: 0; }
.item-card { padding: var(--space-lg); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); background: var(--color-background-secondary); }
.actions, nav { display: flex; gap: var(--space-sm); flex-wrap: wrap; align-items: center; }
.package-review { width: min(640px, calc(100vw - 32px)); max-height: 85vh; overflow: auto; padding: var(--space-xl); background: var(--color-surface-primary); border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 700px) { header, .item-card { flex-direction: column; align-items: stretch; } }
</style>
