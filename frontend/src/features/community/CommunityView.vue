<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import type { CommunityKey, CommunityRelease, CommunitySource, PackageKind } from '@/contracts/community'
import { cachedCatalog, discoverSource, fetchCatalog, installRelease, loadSources, saveSources } from '@/services/communityService'
import { isDesktop } from '@/services/platform/desktop'
import { reviewTrust, confirmTrust, type TrustReview } from '@/services/extensionTrustService'
import AppDialog from '@/components/common/AppDialog.vue'

const kinds: { id: PackageKind | ''; label: string }[] = [
  { id: '', label: '全部' }, { id: 'theme', label: '主题' }, { id: 'skill', label: 'Skill' },
  { id: 'plugin', label: 'Plugin' }, { id: 'mcp', label: 'MCP 配置' }, { id: 'persona', label: '人设' },
  { id: 'template', label: '笔记模板' }, { id: 'model', label: '模型方案' },
]
const sources = ref(loadSources()), selectedSource = ref(sources.value[0]?.id ?? '')
const url = ref(''), query = ref(''), kind = ref<PackageKind | ''>('')
const items = ref<CommunityRelease[]>([]), detail = ref<CommunityRelease | null>(null)
const candidateKeys = ref<CommunityKey[]>([]), candidateUrl = ref('')
const candidateSourceId = ref(''), candidateEnabled = ref(true), trustReviews = ref<TrustReview[]>([])
const busy = ref(false), error = ref(''), notice = ref(''), offline = ref(false)
let controller: AbortController | undefined
let version = 0
const candidates = ref<{ key: string; value: string }[]>([])
function refreshCandidates() {
  candidates.value = Object.keys(localStorage).filter(key => key.startsWith('community-candidate:')).map(key => ({ key, value: localStorage.getItem(key) ?? '' }))
}
refreshCandidates()
function removeCandidate(key: string) { localStorage.removeItem(key); refreshCandidates() }
function cancel() { version++; controller?.abort(); busy.value = false }
onBeforeUnmount(cancel)
function source(): CommunitySource {
  const value = sources.value.find(item => item.id === selectedSource.value)
  if (!value) throw new Error('请先添加并选择一个来源')
  return value
}
async function run(action: (signal: AbortSignal, current: () => boolean) => Promise<void>) {
  cancel(); const request = ++version
  controller = new AbortController(); busy.value = true; error.value = ''; notice.value = ''
  try { await action(controller.signal, () => request === version) }
  catch (reason) { if (request === version) error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { if (request === version) busy.value = false }
}
function inspectSource() {
  const snapshot = url.value.trim()
  void run(async (signal, current) => {
    const discovered = await discoverSource(snapshot, signal)
    const reviews = isDesktop() ? await reviewTrust(snapshot, discovered.source_id, discovered.keys, true) : []
    if (current()) { candidateKeys.value = discovered.keys; candidateSourceId.value = discovered.source_id; candidateEnabled.value = true; trustReviews.value = reviews; candidateUrl.value = snapshot }
  })
}
function trustSource() {
  const snapshot = { url: candidateUrl.value, keys: candidateKeys.value, sourceId: candidateSourceId.value, enabled: candidateEnabled.value, reviews: trustReviews.value }
  void run(async (_signal, current) => {
    if (isDesktop()) await confirmTrust(snapshot.reviews)
    const existing = sources.value.find(item => item.url === snapshot.url)
    const value: CommunitySource = { id: existing?.id ?? crypto.randomUUID(), source_id: snapshot.sourceId, url: snapshot.url, enabled: snapshot.enabled, keys: snapshot.keys, fetchedAt: new Date().toISOString() }
    sources.value = [...sources.value.filter(item => item.id !== value.id), value]
    saveSources(sources.value)
    if (current()) { selectedSource.value = value.id; candidateUrl.value = ''; candidateKeys.value = []; trustReviews.value = []; notice.value = snapshot.enabled ? '来源公钥已固定。可以搜索目录。' : '来源已停用。'; items.value = [] }
  })
}
function search() {
  void run(async (signal, current) => {
    const selected = source()
    try {
      const result = await fetchCatalog(selected, query.value, kind.value, signal)
      if (current()) { items.value = result.items; offline.value = false; notice.value = result.total > result.items.length ? `展示前 ${result.items.length} 项，请缩小搜索范围。` : '' }
    } catch (reason) {
      const cached = cachedCatalog(selected)
      if (current() && cached) { items.value = cached.items; offline.value = true }
      throw reason
    }
  })
}
function install() {
  if (!detail.value) return
  const selected = detail.value, selectedRegistry = source()
  void run(async (signal, current) => {
    const result = await installRelease(selectedRegistry, selected, signal)
    if (current()) { notice.value = result; refreshCandidates() }
  })
}
function toggleSource() {
  const selected = source()
  if (!isDesktop()) { selected.enabled = !selected.enabled; saveSources(sources.value); items.value = []; cancel(); return }
  if (!selected.source_id) { error.value = '请先重新检查此来源并确认公钥，将旧来源设置迁入桌面信任库。'; return }
  void run(async (_signal, current) => {
    const reviews = await reviewTrust(selected.url, selected.source_id!, selected.keys, !selected.enabled)
    if (current()) { candidateKeys.value = selected.keys; candidateSourceId.value = selected.source_id!; candidateEnabled.value = !selected.enabled; trustReviews.value = reviews; candidateUrl.value = selected.url }
  })
}
</script>

<template>
  <main class="community-page feature-page">
    <header class="feature-header">
      <div><h1>社区目录</h1><p>连接您选择的来源。安装后仍需独立启用和授权；关闭社区不影响本地编辑。</p></div>
    </header>
    <section class="community-panel panel" aria-label="社区来源与搜索">
    <div class="community-controls">
      <label>来源地址 <input v-model="url" placeholder="https://community.example.org" :disabled="busy" /></label>
      <button class="btn" :disabled="busy || !url.trim()" @click="inspectSource">检查来源与公钥</button>
      <label>已添加来源 <select v-model="selectedSource" @change="search"><option value="">请选择</option><option v-for="item in sources" :key="item.id" :value="item.id">{{ item.url }}{{ item.enabled ? '' : '（已停用）' }}</option></select></label>
      <button class="btn" :disabled="!selectedSource || busy" @click="toggleSource">启用 / 停用来源</button>
    </div>
    <div class="community-controls">
      <label>关键词 <input v-model="query" @keydown.enter="search" /></label>
      <label>类别 <select v-model="kind"><option v-for="item in kinds" :key="item.id" :value="item.id">{{ item.label }}</option></select></label>
      <button class="btn btn-primary" :disabled="busy || !selectedSource" @click="search">搜索 / 刷新</button>
      <button v-if="busy" class="btn" @click="cancel">取消</button>
    </div>
    <p v-if="busy" role="status">正在处理…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="offline">当前为离线缓存，仅供浏览；安装需要重新核对撤回和签名状态。</p>
    <p v-if="!busy && !items.length">尚无发行记录。添加来源后搜索目录。</p>
    <div class="community-grid">
      <button v-for="item in items" :key="item.release_id" class="community-card" @click="detail = item">
        <strong>{{ item.name }}</strong><span>{{ item.type }} · {{ item.version }}</span>
        <span>{{ item.description }}</span><span>{{ item.namespace }}/{{ item.package_id }} · {{ item.license }}</span>
        <span v-if="item.withdrawn">已撤回</span>
      </button>
    </div>
    </section>
    <section v-if="candidates.length">
      <h2>已保存的声明式候选</h2><p>这些候选尚未应用到人设、MCP 或模型运行配置。</p>
      <details v-for="item in candidates" :key="item.key"><summary>{{ item.key.replace('community-candidate:', '') }}</summary><pre>{{ item.value }}</pre><button class="btn" @click="removeCandidate(item.key)">删除候选</button></details>
    </section>
    <AppDialog v-if="candidateUrl" label="核对来源公钥" @close="candidateUrl = ''; trustReviews = []">
      <p v-if="error" role="alert">{{ error }}</p><p>{{ candidateUrl }}</p><p>请与来源维护者公布的公钥核对。确认后固定这些公钥；密钥改变时不会自动信任。</p>
      <p>来源标识：{{ candidateSourceId }} · {{ candidateEnabled ? '启用' : '停用' }}</p>
      <pre>{{ JSON.stringify(candidateKeys, null, 2) }}</pre>
      <div v-for="review in trustReviews" :key="review.review_id">
        <p>{{ review.proposed.namespace }} / {{ review.proposed.key_id }}：{{ review.previous ? '更新已有信任设置' : '首次确认' }}</p>
        <details v-if="review.previous"><summary>原有公钥与状态</summary><pre>{{ JSON.stringify(review.previous, null, 2) }}</pre></details>
        <p>确认摘要：{{ review.fingerprint }}</p>
      </div>
      <p v-if="trustReviews.length">确认在两分钟内有效。过期或设置已改变时，请关闭对话框并重新检查来源。</p>
      <button class="btn btn-primary" :disabled="busy || !candidateKeys.length" @click="trustSource">确认来源设置</button>
    </AppDialog>
    <AppDialog v-if="detail" label="发行详情与安装" @close="detail = null">
      <template v-if="detail">
        <h2>{{ detail.name }} {{ detail.version }}</h2><p>{{ detail.description }}</p>
        <dl><dt>作者 / 来源</dt><dd>{{ detail.author_id }} / {{ detail.namespace }}</dd><dt>许可证</dt><dd>{{ detail.license }}</dd><dt>大小 / 摘要</dt><dd>{{ detail.size }} 字节<br />{{ detail.sha256 }}</dd><dt>兼容平台</dt><dd>{{ detail.platforms.join(', ') }} / {{ detail.architectures.join(', ') }}</dd><dt>权限</dt><dd>{{ detail.permissions.join(', ') || '无' }}</dd><dt>依赖</dt><dd>{{ JSON.stringify(detail.dependencies) }}</dd></dl>
        <pre>{{ detail.changelog }}</pre>
        <p>安装不会自动启用包或其依赖。人设、模板、MCP 与模型方案仅保存为可检查的候选。</p>
        <button class="btn btn-primary" :disabled="busy || detail.withdrawn || offline" @click="install">校验并安装</button>
      </template>
    </AppDialog>
  </main>
</template>

<style scoped>
.community-page { padding: var(--space-xl); overflow: auto; min-width: 0; }
.feature-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-xl); }
.feature-header h1, .feature-header p { margin: 0; }
.feature-header p { margin-top: var(--space-xs); color: var(--color-text-secondary); }
.community-panel { padding: var(--space-xl); border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-surface-primary); }
.community-controls { display: flex; flex-wrap: wrap; align-items: end; gap: var(--space-md); margin-block: var(--space-lg); }
label { display: grid; gap: var(--space-xs); }
input, select { color: var(--color-text-primary); background: var(--color-background-secondary); border: 1px solid var(--color-border-subtle); padding: var(--space-sm); }
.community-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: var(--space-md); }
.community-card { display: grid; gap: var(--space-sm); text-align: left; padding: var(--space-lg); color: var(--color-text-primary); background: var(--color-background-secondary); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); overflow-wrap: anywhere; }
pre, dd { white-space: pre-wrap; overflow-wrap: anywhere; max-width: 100%; }
</style>
