<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { hostInvoke } from '@/services/platform/desktop'
import { useWorkspaceStore } from '@/stores/workspace'
import AppDialog from '@/components/common/AppDialog.vue'
import InstalledPackages from './InstalledPackages.vue'
import type { CommunitySource } from '@/contracts/community'
const props = defineProps<{ refreshKey: number; sources?: CommunitySource[] }>()
const workspace = useWorkspaceStore()
interface Package { package_key: string; source: string; namespace: string; package_id: string; version: string; state: string }
interface Preview { fingerprint: string; dependencies: { packages: Array<{ package_key: string; namespace: string; package_id: string; kind: string; version: string; permissions: string[] }> }; changes: Array<{ target: { slot: string; package_key: string; configuration: unknown }; expected_revision: string | null }> }
const packages = ref<Package[]>([]), page = ref(0), busy = ref(false), error = ref('')
const selected = ref<Package | null>(null), configuration = ref('{}'), preview = ref<Preview | null>(null)
const completed = ref('')
const installedRefresh = ref(0)
let generation = 0
let activeRequest: { id: string; generation: number } | undefined
const errors: Record<string, string> = {
  VAULT_CHANGED: '笔记库已切换，请重新预览。', VAULT_NOT_OPEN: '请先打开笔记库。',
  EXTENSION_DEPENDENCY_MISSING: '依赖尚未暂存，请先从同一来源获取依赖包。',
  EXTENSION_SOURCE_UNTRUSTED: '请先检查并确认该来源的公钥。',
  EXTENSION_CONFIG_INVALID: '配置不符合包的声明，请检查配置内容。',
  EXTENSION_CONFIG_SECRET: '配置包含秘密字段，请勿将凭据填入包配置。',
  EXTENSION_KEY_REVOKED: '签名键已撤销，不能继续安装。',
  EXTENSION_RELEASE_WITHDRAWN: '此版本已撤回，不能继续安装。',
  EXTENSION_GROUP_NOT_HEALTHY: '部分运行依赖未通过健康检查，请刷新安装状态后重新预览。',
  EXTENSION_INSTALL_CLEANUP_REQUIRED: '扩展停止或清理尚未确认，安装仍未完成。请刷新状态，不要重复安装。',
  EXTENSION_UPDATE_ROLLED_BACK_RUNTIME_STOPPED: '版本已恢复，但原扩展未能重新运行，请核对来源和运行状态。',
}
function message(reason: unknown) {
  const code = reason instanceof Error ? reason.message : String(reason)
  return errors[code] ?? `检查失败：${code}`
}
async function refresh() {
  const current = ++generation; busy.value = true; error.value = ''
  try {
    const result = await hostInvoke<Package[]>('extension_staged', { offset: page.value * 20, limit: 20 })
    if (generation === current) packages.value = result
  } catch (reason) { if (generation === current) error.value = message(reason) }
  finally { if (generation === current) busy.value = false }
}
function choose(item: Package) { selected.value = item; configuration.value = '{}'; preview.value = null; error.value = ''; completed.value = '' }
async function inspect() {
  if (!selected.value || !workspace.vaultId) return
  const vaultId = workspace.vaultId, rootKey = selected.value.package_key, current = ++generation
  busy.value = true; error.value = ''; preview.value = null
  try {
    const parsed = JSON.parse(configuration.value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('配置必须是 JSON 对象。')
    const result = await hostInvoke<Preview>('extension_install_preview', { request: { root_key: rootKey, vault_id: vaultId, configurations: { [rootKey]: parsed } } })
    if (generation === current && workspace.vaultId === vaultId && selected.value?.package_key === rootKey) preview.value = result
  } catch (reason) { if (generation === current) error.value = message(reason) }
  finally { if (generation === current) busy.value = false }
}
async function install() {
  if (!selected.value || !preview.value || !workspace.vaultId) return
  const vaultId = workspace.vaultId, rootKey = selected.value.package_key, reviewed = preview.value
  const current = ++generation; busy.value = true; error.value = ''; completed.value = ''
  let requestId: string | undefined
  const operationId = crypto.randomUUID()
  try {
    const parsed = JSON.parse(configuration.value)
    requestId = await hostInvoke<string>('extension_stage_prepare')
    if (current !== generation || workspace.vaultId !== vaultId) {
      await hostInvoke('extension_stage_cancel', { requestId }); return
    }
    activeRequest = { id: requestId, generation: current }
    const receipt = await hostInvoke<{ operation_id: string; state: string }>('extension_install_confirm', { request: {
      request_id: requestId, operation_id: operationId, fingerprint: reviewed.fingerprint,
      root_key: rootKey, vault_id: vaultId, configurations: { [rootKey]: parsed },
    } })
    if (receipt?.operation_id !== operationId || receipt.state !== 'complete') throw new Error('EXTENSION_GROUP_NOT_HEALTHY')
    if (workspace.vaultId === vaultId) installedRefresh.value++
    if (generation === current && workspace.vaultId === vaultId) {
      completed.value = '安装已完成；请在已安装列表中查看运行状态或选择目标应用。'
      preview.value = null
      await refresh()
    }
  } catch (reason) {
    if (workspace.vaultId === vaultId) installedRefresh.value++
    if (generation === current) { preview.value = null; error.value = message(reason) }
  }
  finally {
    if (activeRequest?.id === requestId) activeRequest = undefined
    if (requestId) void hostInvoke('extension_stage_cancel', { requestId }).catch(() => undefined)
    if (generation === current) busy.value = false
  }
}
function cancelRequest() { if (activeRequest) void hostInvoke('extension_stage_cancel', { requestId: activeRequest.id }).catch(() => undefined); activeRequest = undefined }
function close() { cancelRequest(); ++generation; selected.value = null; preview.value = null; busy.value = false }
watch(() => workspace.vaultId, close)
watch(configuration, () => { preview.value = null })
watch(() => props.refreshKey, () => { page.value = 0; close(); void refresh() })
onMounted(refresh)
onBeforeUnmount(() => { cancelRequest(); ++generation })
</script>

<template>
  <InstalledPackages :refresh-key="refreshKey + installedRefresh" :sources="sources" />
  <section class="desktop-packages panel" aria-label="桌面已暂存包">
    <header class="section-heading">
      <div>
        <h2>桌面已暂存包</h2>
        <p>暂存包保存在桌面安装库中，重启后仍可查看。暂存不代表已经安装或获得运行权限。</p>
      </div>
      <button class="btn" :disabled="busy" @click="refresh">刷新暂存列表</button>
    </header>
    <p v-if="error" class="notice-banner error-message" role="alert">{{ error }}</p>
    <p v-if="busy" class="notice-banner" role="status">正在检查…</p>
    <div v-if="!busy && !packages.length" class="empty-state">
      <strong>本页没有暂存包</strong>
      <span>从社区目录校验并暂存包后，可在这里查看安装预览。</span>
    </div>
    <ul v-if="packages.length" class="package-list">
      <li v-for="item in packages" :key="item.package_key" class="item-card">
        <div><strong>{{ item.namespace }}/{{ item.package_id }} · {{ item.version }}</strong><p>{{ item.source }}</p></div>
        <button class="btn" :disabled="busy" @click="choose(item)">查看安装预览</button>
      </li>
    </ul>
    <nav class="pagination" aria-label="暂存包分页">
      <button class="btn" :disabled="busy || page === 0" @click="page--; refresh()">上一页</button>
      <span>第 {{ page + 1 }} 页</span>
      <button class="btn" :disabled="busy || packages.length < 20" @click="page++; refresh()">下一页</button>
    </nav>
    <AppDialog v-if="selected" label="桌面安装预览" @close="close">
      <h2>{{ selected.package_id }} · {{ selected.version }}</h2>
      <p v-if="!workspace.vaultId">请先打开要使用此包的笔记库。</p>
      <label>包配置（JSON）<textarea v-model="configuration" :disabled="busy" rows="6" spellcheck="false" /></label>
      <p>未声明配置的包请保留空对象，不要填写密码或令牌。</p>
      <button class="btn" :disabled="busy || !workspace.vaultId" @click="inspect">检查依赖、权限与配置</button>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="completed" class="notice-banner" role="status">{{ completed }}</p>
      <div v-if="preview">
        <h3>按安装顺序排列的包</h3>
        <ul><li v-for="item in preview.dependencies.packages" :key="item.package_key">
          {{ item.namespace }}/{{ item.package_id }} · {{ item.version }}
          <p>请求权限：{{ item.permissions.join('、') || '无' }}</p>
        </li></ul>
        <details><summary>检查配置</summary><pre>{{ JSON.stringify(preview.changes.map(change => change.target.configuration), null, 2) }}</pre></details>
        <p>确认后将按以上摘要安装。运行型包的全部依赖将在原生沙箱中检查；声明式配置安装后仍需另行选择目标应用。</p>
        <button class="btn primary" :disabled="busy" @click="install">确认安装并启用</button>
      </div>
    </AppDialog>
  </section>
</template>

<style scoped>
.desktop-packages { display: grid; gap: var(--space-lg); margin-block: var(--space-xl); padding: var(--space-xl); border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-surface-primary); }
.section-heading { display: flex; align-items: start; justify-content: space-between; gap: var(--space-lg); }
.section-heading h2, .section-heading p, .item-card p { margin: 0; }
.section-heading p, .item-card p, .empty-state span { margin-top: var(--space-xs); color: var(--color-text-secondary); }
.package-list { display: grid; gap: var(--space-md); margin: 0; padding: 0; list-style: none; }
.item-card { display: flex; align-items: center; justify-content: space-between; gap: var(--space-lg); padding: var(--space-lg); color: var(--color-text-primary); background: var(--color-background-secondary); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); overflow-wrap: anywhere; }
.empty-state { min-height: 120px; }
.pagination { display: flex; align-items: center; gap: var(--space-sm); color: var(--color-text-secondary); }
label { display: grid; gap: var(--space-xs); }
textarea { width: 100%; color: var(--color-text-primary); background: var(--color-background-secondary); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 700px) { .section-heading, .item-card { align-items: stretch; flex-direction: column; } }
</style>
