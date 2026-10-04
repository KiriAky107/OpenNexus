<script setup lang="ts">
import { computed } from 'vue'
import type { ChatMessage } from '@/contracts'
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import ToolActivity from './ToolActivity.vue'
import { t } from '@/i18n'
const props = defineProps<{ message: ChatMessage; citationNumbers?: number[]; streaming?: boolean }>()
const emit = defineEmits<{ citation: [number: number] }>()
const entries = computed<NonNullable<ChatMessage['activity']>>(() => {
  const activity = props.message.activity || []
  const result: NonNullable<ChatMessage['activity']> = []
  // Older messages may have only aggregate fields. Their exact event order is unavailable.
  if (props.message.thinking && !activity.some(item => item.type === 'thinking')) result.push({ type: 'thinking', text: props.message.thinking })
  result.push(...activity)
  for (const call of props.message.tool_calls || []) {
    if (!activity.some(item => item.type === 'tool' && item.tool_call_id === call.tool_call_id)) result.push({ type: 'tool', tool_call_id: call.tool_call_id })
  }
  if (props.message.content && !activity.some(item => item.type === 'text')) result.push({ type: 'text', text: props.message.content })
  return result
})
const legacyTextOrder = computed(() => Boolean(props.message.content
  && props.message.tool_calls?.length
  && !props.message.activity?.some(item => item.type === 'text')))
const thinkingLabel = computed(() => t('正在思考…', 'Thinking…'))
const aliases = computed(() => Object.fromEntries((props.message.citations || []).filter(item => item.citation_id).map(item => [item.citation_id!, props.message.citations!.indexOf(item) + 1])))
</script>
<template>
  <div class="message-activity" :aria-label="t('回复与执行记录', 'Response and execution record')">
    <template v-for="(entry, index) in entries" :key="`${entry.type}-${entry.sequence ?? index}`">
      <template v-if="entry.type === 'text'">
        <p v-if="legacyTextOrder" class="legacy-order-note" role="note">{{ t('这条旧对话未记录正文片段的发生顺序；下方保留完整原回复，无法准确插入到各次工具调用之间。', 'This older conversation did not record when each reply segment appeared. The complete reply is preserved below, but cannot be accurately placed between tool calls.') }}</p>
        <MarkdownContent class="message-content" :source="entry.text" :streaming="streaming" :citation-aliases="aliases" :citation-numbers="citationNumbers" @citation="emit('citation', $event)" />
      </template>
      <details v-else-if="entry.type === 'thinking'" class="thinking ui-disclosure" :data-disclosure-key="`thinking:${entry.sequence ?? index}`">
        <summary>{{ t('思考过程', 'Reasoning') }}</summary>
        <p>{{ entry.text }}</p>
      </details>
      <template v-else-if="entry.type === 'tool'"><ToolActivity v-if="message.tool_calls?.find(call => call.tool_call_id === entry.tool_call_id)" :call="message.tool_calls.find(call => call.tool_call_id === entry.tool_call_id)!" /></template>
    </template>
    <details v-if="streaming && !entries.some(item => item.type === 'thinking' || item.type === 'text')" class="thinking ui-disclosure">
      <summary><span class="thinking-typewriter" :aria-label="thinkingLabel" :style="{ '--typing-steps': Array.from(thinkingLabel).length }">{{ thinkingLabel }}</span></summary>
    </details>
  </div>
</template>
<style scoped>
.thinking { margin-block: var(--space-sm); color: var(--color-text-secondary); }
.thinking p { margin-top: var(--space-sm); white-space: pre-wrap; }
.legacy-order-note { margin-block: var(--space-sm); color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.thinking-typewriter { display: inline-block; white-space: nowrap; padding-inline-end: 3px; border-inline-end: 2px solid var(--color-accent-primary); animation: thinking-type 2s steps(var(--typing-steps), end) infinite; }
@keyframes thinking-type { 0% { clip-path: inset(0 100% 0 0); } 65%, 100% { clip-path: inset(0 0 0 0); } }
@media (prefers-reduced-motion: reduce) { .thinking-typewriter { animation: none; border-inline-end: 0; } }
</style>
