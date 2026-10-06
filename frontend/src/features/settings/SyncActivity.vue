<script setup lang="ts">
import { computed } from 'vue'
import type { SyncActivity } from '@/contracts/sync'
import { t } from '@/i18n'
import { syncBytes, syncError, syncTime } from './syncPresentation'
const props = defineProps<{ activity?: SyncActivity | null; lastSuccess?: number | null; error?: string | null }>()
const phase = computed(() => {
  switch (props.activity?.phase) {
    case 'handshake': return t('正在核对服务协议', 'Checking service protocol')
    case 'discover': return t('正在扫描本地修改', 'Scanning local changes')
    case 'pull': return t('正在读取远端修订', 'Reading remote revisions')
    case 'push': return t('正在上传本地修订', 'Uploading local revisions')
    case 'complete': return t('本轮已完成', 'Cycle completed')
    case 'failed': return t('本轮未完成', 'Cycle did not complete')
    case 'cancelled': return t('本轮已停止', 'Cycle stopped')
    default: return t('尚未开始新一轮', 'A new cycle has not started')
  }
})
const transfer = computed(() => props.activity?.transfer)
const byteLabel = computed(() => transfer.value?.phase === 'receiving' ? t('已接收，尚待完整校验', 'Received; full verification pending') : transfer.value?.phase === 'sending' ? t('服务端已确认', 'Confirmed by the service') : transfer.value?.phase === 'cached' ? (transfer.value.direction === 'upload' ? t('服务端报告对象已存在，无需重传', 'Service reports the object is available; no retransmission needed') : t('本地完整缓存已校验，无需重传', 'Verified local cache; no retransmission needed')) : transfer.value?.total_bytes === 0 ? t('正在处理修订', 'Processing the revision') : t('对象已校验，正在处理修订', 'Object verified; processing the revision'))
</script>
<template>
  <section class="sync-activity" :aria-label="t('同步详情', 'Sync details')">
    <p role="status">{{ phase }}</p>
    <p>{{ t('最近一轮成功', 'Last successful cycle') }} · {{ syncTime(lastSuccess) }}</p>
    <p v-if="activity">{{ t('本轮提交上传修订', 'Upload revisions committed this cycle') }} {{ activity.uploaded_revisions }} · {{ t('读取远端修订', 'Remote revisions read') }} {{ activity.received_revisions }}</p>
    <template v-if="transfer">
      <p class="path">{{ transfer.direction === 'upload' ? t('上传', 'Upload') : t('下载', 'Download') }} · {{ transfer.path }}</p>
      <progress :aria-label="byteLabel" :max="Math.max(1, transfer.total_bytes)" :value="transfer.bytes_done" />
      <p>{{ byteLabel }} · {{ syncBytes(transfer.bytes_done) }} / {{ syncBytes(transfer.total_bytes) }}</p>
      <p>{{ transfer.direction === 'upload' ? t('本对象本轮确认上传', 'Upload bytes confirmed for this object this cycle') : t('本对象本轮接收', 'Bytes received for this object this cycle') }} {{ syncBytes(transfer.transferred_bytes) }}<span v-if="transfer.resumed_bytes"> · {{ t('恢复已确认偏移', 'Resumed confirmed offset') }} {{ syncBytes(transfer.resumed_bytes) }}</span></p>
    </template>
    <p v-if="error" role="alert">{{ t('最近记录的同步错误', 'Last recorded sync error') }} · {{ syncError(error) }}</p>
    <p class="subtle">{{ t('一轮成功不表示队列已清空。下载接收进度不等于校验成功，待审核冲突单独处理。', 'A successful cycle does not mean the queue is empty. Receiving download bytes does not mean verification succeeded; review conflicts separately.') }}</p>
  </section>
</template>
<style scoped>
.sync-activity { min-width: 0; padding: var(--space-md); background: var(--color-background-secondary); border-radius: var(--radius-md); }
.path { overflow-wrap: anywhere; }
progress { width: 100%; accent-color: var(--color-accent-primary); }
</style>
