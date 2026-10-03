<script setup lang="ts">
import { computed, watch, ref, nextTick } from 'vue'
import { useVaultLinksStore } from '@/stores/vaultLinks'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import type { IndexedReference } from '@/services/vaultLinkIndex'
import { t } from '@/i18n'
const workspace = useWorkspaceStore(), editor = useEditorStore(), links = useVaultLinksStore()
const emit = defineEmits<{ navigate: [] }>()
const page = ref(40), query = ref(''), tab = ref<'backlinks'|'broken'>('backlinks'), error = ref('')
const statuses = { missing: '缺失目标', ambiguous: '链接歧义', unsafe: '不安全路径', unsupported: '不支持的格式', resolved: '可用', external: '外部网址' }
const entries = computed(() => links.references.filter(ref => tab.value === 'backlinks' ? ref.status === 'resolved' && ref.target === workspace.activeFilePath : ['missing','ambiguous','unsafe','unsupported'].includes(ref.status)).filter(ref => `${ref.source} ${ref.raw}`.toLowerCase().includes(query.value.trim().toLowerCase())))
watch(() => [workspace.vaultId, JSON.stringify(workspace.fileTree)], () => { void links.refresh() }, { immediate: true })
watch(() => [tab.value, query.value, workspace.activeFilePath], () => { page.value = 40 })
async function locate(ref: IndexedReference) {
  try {
    const navigation = await editor.loadFile(ref.source)
    await nextTick()
    if (!navigation?.isCurrent()) return
    if (ref.nodeId) editor.selectCanvasNode(ref.nodeId)
    else { const occurrence = links.references.filter(item => item.source === ref.source && item.raw === ref.raw && (item.start ?? 0) < (ref.start ?? 0)).length; editor.locateReference(ref.start ?? 0, ref.raw.length, ref.raw, occurrence) }
    emit('navigate')
  } catch (cause) { error.value = String(cause) }
}
</script>
<template>
  <section class="vault-references">
    <p class="reference-target">{{ t('当前文件', 'Current file') }} · {{ workspace.activeFilePath }}</p>
    <div class="reference-controls inline-actions"><button class="button-secondary" :aria-pressed="tab === 'backlinks'" @click="tab = 'backlinks'">{{ t('当前文件的反向链接', 'Backlinks to this file') }}</button><button class="button-secondary" :aria-pressed="tab === 'broken'" @click="tab = 'broken'">{{ t('知识库失效链接', 'Broken vault links') }}</button><button class="button-secondary" :disabled="links.busy" @click="links.refresh()">{{ t('重建检查', 'Recheck') }}</button><input v-model="query" class="input" :aria-label="t('筛选引用来源', 'Filter reference sources')" /></div>
    <p v-if="links.busy" role="status">{{ t('检查引用…', 'Checking references…') }}</p><p v-if="error" role="alert">{{ error }}</p>
    <p v-for="item in links.errors" :key="item.path" class="error-banner">{{ item.path }}: {{ item.error }}</p>
    <div class="reference-list"><button v-for="(ref,index) in entries.slice(0,page)" :key="`${ref.source}:${index}`" class="reference-row button-secondary" @click="locate(ref)"><strong>{{ ref.source }}</strong><span>{{ statuses[ref.status] }} · {{ ref.raw }}</span><small>{{ ref.context }}</small></button></div>
    <p v-if="!links.busy && !entries.length">{{ t('没有匹配的引用', 'No matching references') }}</p><button v-if="page < entries.length" class="button-secondary" @click="page += 40">{{ t('更多引用', 'More references') }}</button>
  </section>
</template>
<style scoped>
.vault-references { min-width: 0; font-size: var(--font-size-sm); }
.reference-target { color: var(--color-text-secondary); overflow-wrap: anywhere; }
.reference-controls { padding-block: var(--space-md); flex-wrap: wrap; }
.reference-controls input { flex: 1; min-width: 140px; }
.reference-controls button[aria-pressed="true"] { color: var(--color-accent-primary); background: var(--color-accent-soft); border-color: var(--color-border-focus); }
.reference-list { display: grid; gap: var(--space-sm); }
.reference-row { display: grid; text-align: left; gap: 6px; padding: var(--space-md); overflow-wrap: anywhere; white-space: normal; line-height: 1.5; }
small { color: var(--color-text-secondary); }
</style>
