<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { hostInvoke } from '@/services/platform/desktop'
import { useWorkspaceStore } from '@/stores/workspace'
import AppDialog from '@/components/common/AppDialog.vue'
const props = defineProps<{ refreshKey: number }>()
const workspace = useWorkspaceStore()
interface Package { package_key: string; source: string; namespace: string; package_id: string; version: string; state: string }
interface Preview { fingerprint: string; dependencies: { packages: Array<{ package_key: string; namespace: string; package_id: string; version: string; permissions: string[] }> }; changes: Array<{ target: { configuration: unknown }; expected_revision: string | null }> }
const packages = ref<Package[]>([]), page = ref(0), busy = ref(false), error = ref('')
const selected = ref<Package | null>(null), configuration = ref('{}'), preview = ref<Preview | null>(null)
let generation = 0
const errors: Record<string, string> = {
  VAULT_CHANGED: '笔记库已切换，请重新预览。', VAULT_NOT_OPEN: '请先打开笔记库。',
  EXTENSION_DEPENDENCY_MISSING: '依赖尚未暂存，请先从同一来源获取依赖包。',
  EXTENSION_SOURCE_UNTRUSTED: '请先检查并确认该来源的公钥。',
  EXTENSION_CONFIG_INVALID: '配置不符合包的声明，请检查配置内容。',
  EXTENSION_CONFIG_SECRET: '配置包含秘密字段，请勿将凭据填入包配置。',
  EXTENSION_KEY_REVOKED: '签名键已撤销，不能继续安装。',
  EXTENSION_RELEASE_WITHDRAWN: '此版本已撤回，不能继续安装。',
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
function choose(item: Package) { selected.value = item; configuration.value = '{}'; preview.value = null; error.value = '' }
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
function close() { ++generation; selected.value = null; preview.value = null; busy.value = false }
watch(() => workspace.vaultId, close)
watch(configuration, () => { preview.value = null })
watch(() => props.refreshKey, () => { page.value = 0; close(); void refresh() })
onMounted(refresh)
onBeforeUnmount(() => ++generation)
</script>

<template>
  <section class="desktop-packages" aria-label="桌面已暂存包">
    <h2>桌面已暂存包</h2>
    <p>暂存包保存在桌面安装库中，重启后仍可查看。暂存不代表已经安装或获得运行权限。</p>
    <button class="btn" :disabled="busy" @click="refresh">刷新暂存列表</button>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="busy" role="status">正在检查…</p>
    <p v-if="!busy && !packages.length">本页没有暂存包。</p>
    <ul><li v-for="item in packages" :key="item.package_key">
      <strong>{{ item.namespace }}/{{ item.package_id }} · {{ item.version }}</strong>
      <p>{{ item.source }}</p><button class="btn" :disabled="busy" @click="choose(item)">查看安装预览</button>
    </li></ul>
    <button class="btn" :disabled="busy || page === 0" @click="page--; refresh()">上一页</button>
    <span>第 {{ page + 1 }} 页</span>
    <button class="btn" :disabled="busy || packages.length < 20" @click="page++; refresh()">下一页</button>
    <AppDialog v-if="selected" label="桌面安装预览" @close="close">
      <h2>{{ selected.package_id }} · {{ selected.version }}</h2>
      <p v-if="!workspace.vaultId">请先打开要使用此包的笔记库。</p>
      <label>包配置（JSON）<textarea v-model="configuration" :disabled="busy" rows="6" spellcheck="false" /></label>
      <p>未声明配置的包请保留空对象，不要填写密码或令牌。</p>
      <button class="btn" :disabled="busy || !workspace.vaultId" @click="inspect">检查依赖、权限与配置</button>
      <p v-if="error" role="alert">{{ error }}</p>
      <div v-if="preview">
        <h3>按安装顺序排列的包</h3>
        <ul><li v-for="item in preview.dependencies.packages" :key="item.package_key">
          {{ item.namespace }}/{{ item.package_id }} · {{ item.version }}
          <p>请求权限：{{ item.permissions.join('、') || '无' }}</p>
        </li></ul>
        <details><summary>检查配置</summary><pre>{{ JSON.stringify(preview.changes.map(change => change.target.configuration), null, 2) }}</pre></details>
        <p>依赖和配置检查完成。安装执行暂未开放，此预览不会启用包。</p>
      </div>
    </AppDialog>
  </section>
</template>

<style scoped>
.desktop-packages { margin-block: var(--space-xl); }
li { margin-block: var(--space-md); overflow-wrap: anywhere; }
label { display: grid; gap: var(--space-xs); }
textarea { width: 100%; color: var(--color-text-primary); background: var(--color-background-secondary); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
