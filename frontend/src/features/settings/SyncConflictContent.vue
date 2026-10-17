<script setup lang="ts">
import { computed } from 'vue'
import type { SyncConflictReview } from '@/contracts/sync'
import { t } from '@/i18n'
import SyncTextDiff from './SyncTextDiff.vue'
const props = defineProps<{ review: SyncConflictReview }>()
const sides = computed(() => [
  { name: t('当前本地', 'Current local'), value: props.review.local },
  { name: t('远端版本', 'Remote revision'), value: props.review.incoming },
  ...(props.review.base ? [{ name: t('共同基线', 'Common base'), value: props.review.base }] : []),
  ...props.review.related.map(item => ({ name: t(`其他受影响路径：${item.path}`, `Other affected path: ${item.path}`), value: item.content })),
])
</script>
<template>
  <div class="sync-conflict-content">
    <p>{{ review.base ? t('三方比较：共同基线已按文件身份、修订和完整摘要核对。', 'Three-way comparison: the common base is verified by file identity, revision and full hash.') : t('两方比较：可信共同基线缓存不可用。', 'Two-way comparison: a trusted common base is not available in the cache.') }}</p>
    <p v-if="review.related.some(item => item.content.exists)">{{ t('重命名会同时处理以下其他本地路径，请一并审核；采用远端或保留本地的选择会影响这些文件。', 'The rename also affects the other local paths below. Review them together; the choice affects those files as well.') }}</p>
    <div class="content-sides"><section v-for="side in sides" :key="side.name"><h4>{{ side.name }}</h4><p>{{ side.value.byte_size }} bytes · {{ side.value.exists ? t('文件存在', 'File exists') : t('文件不存在／已删除', 'File absent/deleted') }}</p>
      <details><summary>{{ t('内容预览与校验值', 'Content preview and hash') }}</summary><code>{{ side.value.hash || '∅' }}</code><pre v-if="side.value.text !== null">{{ side.value.text }}</pre><p v-else>{{ t('二进制或非 UTF-8 文件，没有正文预览。', 'Binary or non-UTF-8 file; no text preview.') }}</p></details>
      <p v-if="side.value.truncated" role="status">{{ t('只显示 UTF-8 前缀', 'UTF-8 prefix only') }} {{ side.value.preview_bytes }} / {{ side.value.byte_size }} bytes · {{ t('不是全文；摘要覆盖整个文件。', 'Not the full file; the hash covers all bytes.') }}</p>
    </section></div>
    <template v-if="review.local.text !== null && review.incoming.text !== null">
      <template v-if="review.base?.text != null"><SyncTextDiff :before="review.base.text" :after="review.local.text" :label="t('基线 → 本地', 'Base → local')" /><SyncTextDiff :before="review.base.text" :after="review.incoming.text" :label="t('基线 → 远端', 'Base → remote')" /></template>
      <SyncTextDiff v-else :before="review.local.text" :after="review.incoming.text" :label="t('本地 → 远端', 'Local → remote')" />
    </template>
    <template v-for="item in review.related" :key="item.path"><SyncTextDiff v-if="item.content.exists && item.content.text !== null && review.incoming.text !== null" :before="item.content.text" :after="review.incoming.text" :label="t(`${item.path} → 远端`, `${item.path} → remote`)" /></template>
  </div>
</template>
<style scoped>
.sync-conflict-content { display: grid; gap: var(--space-sm); min-width: 0; }
.content-sides { display: grid; grid-template-columns: repeat(auto-fit,minmax(min(100%,220px),1fr)); gap: var(--space-md); }
.content-sides section { min-width: 0; }
code { overflow-wrap: anywhere; }
pre { max-height: 240px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: var(--font-size-xs)/1.6 var(--font-ui-mono); }
</style>
