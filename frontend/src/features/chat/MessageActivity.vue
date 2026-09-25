<script setup lang="ts">
import { computed } from 'vue'
import type { ChatMessage } from '@/contracts'
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import ToolActivity from './ToolActivity.vue'
import { t } from '@/i18n'
const props = defineProps<{ message: ChatMessage; citationNumbers?: number[] }>()
const emit = defineEmits<{ citation: [number: number] }>()
const entries = computed(() => props.message.activity?.some(item => item.type === 'text') ? props.message.activity.filter(item => item.type !== 'thinking') : [
  ...(props.message.tool_calls || []).map(call => ({ type: 'tool' as const, tool_call_id: call.tool_call_id })),
  ...(props.message.content ? [{ type: 'text' as const, text: props.message.content }] : []),
])
const aliases = computed(() => Object.fromEntries((props.message.citations || []).filter(item => item.citation_id).map(item => [item.citation_id!, props.message.citations!.indexOf(item) + 1])))
</script>
<template>
  <div class="message-activity" :aria-label="t('回复与执行记录', 'Response and execution record')">
    <template v-for="(entry, index) in entries" :key="'sequence' in entry ? entry.sequence ?? index : index">
      <MarkdownContent v-if="entry.type === 'text'" class="message-content" :source="entry.text" :citation-aliases="aliases" :citation-numbers="citationNumbers" @citation="emit('citation', $event)" />
      <template v-else-if="entry.type === 'tool'"><ToolActivity v-if="message.tool_calls?.find(call => call.tool_call_id === entry.tool_call_id)" :call="message.tool_calls.find(call => call.tool_call_id === entry.tool_call_id)!" /></template>
    </template>
  </div>
</template>
