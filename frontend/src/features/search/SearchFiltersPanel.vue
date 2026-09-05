<script setup lang="ts">
import { useSearchStore } from '@/stores/search'
import { computed } from 'vue'
import { t } from '@/i18n'

const searchStore = useSearchStore()
const modes = computed(() => [
  { value: 'hybrid' as const, label: t('混合检索', 'Hybrid search') },
  { value: 'fts' as const, label: t('全文检索', 'Full-text search') },
  { value: 'vector' as const, label: t('向量检索', 'Vector search') },
])
</script>

<template>
  <div class="sidebar-panel">
    <p class="subtle">{{ t('检索模式', 'Search mode') }}</p>
    <div class="sidebar-list mode-list">
      <button v-for="item in modes" :key="item.value" class="sidebar-list-item"
        :class="{ active: searchStore.mode === item.value }" @click="searchStore.setMode(item.value)">
        {{ item.label }}
      </button>
    </div>
    <p class="subtle section-title">{{ t('最近搜索', 'Recent searches') }}</p>
    <div class="sidebar-list">
      <button v-for="query in searchStore.recentQueries" :key="query" class="sidebar-list-item recent"
        @click="searchStore.doSearch({ query, mode: searchStore.mode })">{{ query }}</button>
    </div>
  </div>
</template>

<style scoped>
.mode-list { margin-top: var(--space-sm); }
.section-title { margin-top: var(--space-xl); }
.sidebar-list-item { width: 100%; text-align: left; }
.recent { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text-secondary); }
</style>
