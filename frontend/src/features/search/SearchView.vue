<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { SearchResult } from '@/contracts'
import { useEditorStore } from '@/stores/editor'
import { useSearchStore } from '@/stores/search'
import { useWorkspaceStore } from '@/stores/workspace'
import { t } from '@/i18n'

const searchStore = useSearchStore()
onMounted(() => { void searchStore.loadHistory() })
const workspaceStore = useWorkspaceStore()
const editorStore = useEditorStore()
const router = useRouter()
const folder = ref('')
const tag = ref('')

function submitSearch() {
  void searchStore.doSearch({
    query: searchStore.query,
    mode: searchStore.mode,
    folder: folder.value || undefined,
    tag: tag.value || undefined,
  })
}

async function openResult(result: SearchResult) {
  await editorStore.loadFile(result.file_path)
  workspaceStore.openFile(result.file_path)
  editorStore.highlightBlock(result.block_id)
  await router.push('/workspace')
}
</script>

<template>
  <section class="feature-page search-page">
    <header class="feature-header">
      <div><h1>{{ t('搜索知识库', 'Search Knowledge Base') }}</h1><p>{{ t('在当前 Vault 中进行全文、向量或混合检索。', 'Run full-text, vector, or hybrid search in the current Vault.') }}</p></div>
    </header>
    <form class="search-form panel" @submit.prevent="submitSearch">
      <input v-model="searchStore.query" class="input search-input" :placeholder="t('搜索笔记内容、标题或标签', 'Search note content, titles, or tags')" autofocus />
      <button class="button-primary" :disabled="!searchStore.query.trim() || searchStore.isSearching">
        {{ searchStore.isSearching ? t('搜索中…', 'Searching…') : t('搜索', 'Search') }}
      </button>
      <div class="form-grid advanced">
        <div class="field"><label>{{ t('文件夹范围', 'Folder scope') }}</label><input v-model="folder" class="input" :placeholder="t('例如 /数据结构', 'For example /Data Structures')" /></div>
        <div class="field"><label>{{ t('标签', 'Tag') }}</label><input v-model="tag" class="input" :placeholder="t('例如 算法', 'For example algorithms')" /></div>
      </div>
    </form>
    <div v-if="searchStore.error" class="error-banner">{{ searchStore.error }}</div>
    <div v-if="searchStore.historyError" class="notice-banner">{{ searchStore.historyError }}</div>
    <div v-if="searchStore.recentQueries.length" class="search-history">
      <span class="subtle">{{ t('最近搜索（保存在应用数据中）', 'Recent searches (stored in application data)') }}</span>
      <button v-for="item in searchStore.recentQueries" :key="item" class="button-secondary" @click="searchStore.query = item; submitSearch()">{{ item }}</button>
      <button class="button-secondary" @click="searchStore.clearHistory">{{ t('清空记录', 'Clear history') }}</button>
    </div>
    <div v-if="searchStore.vectorUnavailable" class="notice-banner">{{ t('向量索引不可用，已保留全文检索能力。', 'Vector search is unavailable; full-text search remains active.') }}</div>
    <div v-if="searchStore.results.length" class="results-header">
      <span>{{ t('找到', 'Found') }} {{ searchStore.total }} {{ t('条结果', 'results') }}</span><span class="badge info">{{ searchStore.mode }}</span>
    </div>
    <div v-if="searchStore.results.length" class="result-list">
      <article v-for="result in searchStore.results" :key="`${result.note_id}:${result.block_id}`"
        class="item-card result-card" @click="openResult(result)">
        <div class="result-title"><strong>{{ result.note_title }}</strong><span class="badge">{{ result.match_type }}</span></div>
        <p class="subtle">{{ result.file_path }} · {{ result.heading_path }}</p>
        <p class="snippet">{{ result.snippet }}</p>
        <div class="result-meta"><span>{{ t('相关度', 'Relevance') }} {{ Math.round(result.score * 100) }}%</span><span>{{ t('点击定位原文 →', 'Open source →') }}</span></div>
      </article>
    </div>
    <div v-else-if="!searchStore.isSearching" class="empty-state">
      <div><strong>{{ searchStore.query ? t('没有找到匹配内容', 'No matching content') : t('从你的知识库开始搜索', 'Start searching your knowledge base') }}</strong><p>{{ t('可切换检索模式或缩小文件夹、标签范围。', 'Try another search mode or narrow the folder and tag scope.') }}</p></div>
    </div>
  </section>
</template>

<style scoped>
.search-page > * { width: min(100%, 1040px); margin-inline: auto; }
.search-form { display: grid; grid-template-columns: 1fr auto; gap: var(--space-md); margin-bottom: var(--space-lg); }
.search-input { height: 44px; font-size: var(--font-size-lg); }
.search-history { display: flex; flex-wrap: wrap; gap: var(--space-sm); margin-bottom: var(--space-md); }
.advanced { grid-column: 1 / -1; }
.results-header, .result-title, .result-meta { display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); }
.results-header { margin: var(--space-xl) 0 var(--space-md); color: var(--color-text-secondary); }
.result-list { display: grid; gap: var(--space-md); }
.result-card { position: relative; cursor: pointer; overflow: hidden; }
.result-card::before { content: ''; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--color-accent-primary); opacity: 0; transform: scaleY(.45); transition: opacity var(--motion-fast), transform var(--motion-fast); }
.result-card:hover::before { opacity: 1; transform: scaleY(1); }
.snippet { margin: var(--space-md) 0; line-height: var(--line-height-relaxed); }
.result-meta { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
@media (max-width: 700px) { .search-form { grid-template-columns: 1fr; } .advanced { grid-column: auto; } }
</style>
