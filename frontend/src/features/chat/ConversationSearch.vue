<script setup lang="ts">
import { ref, watch, computed, onBeforeUnmount } from 'vue'
import { searchConversation, type ChatSearchHit } from '@/services/chatService'
import type { ChatMessage } from '@/contracts'
import { useWorkspaceStore } from '@/stores/workspace'
import { toolSummary } from './toolSummary'
import { t } from '@/i18n'
const props = defineProps<{ conversationId: string | null; liveMessage?: ChatMessage }>()
const emit = defineEmits<{ locate: [hit: ChatSearchHit, query: string]; clear: [] }>()
const workspace = useWorkspaceStore()
const query = ref(''), results = ref<ChatSearchHit[]>([]), more = ref(false), busy = ref(false), error = ref('')
let generation = 0, timer: ReturnType<typeof setTimeout> | undefined, offset = 0
const live = computed<ChatSearchHit[]>(() => {
  const message = props.liveMessage, q = query.value.trim().toLowerCase()
  if (!message || !q) return []
  const entries = message.activity?.flatMap(e => e.type === 'text' ? [e.text] : []) || []
  const texts = entries.length ? entries : [message.content]
  return [...texts.map((text, entry_index) => ({ kind: 'text' as const, text, entry_index })),
    ...(message.tool_calls || []).map((call, entry_index) => ({ kind: 'tool' as const, text: toolSummary(call), tool_call_id: call.tool_call_id, entry_index }))]
    .filter(entry => entry.text.toLowerCase().includes(q)).map(entry => {
      const index = entry.text.toLowerCase().indexOf(q)
      return { ...entry, message_id: message.message_id, position: 0, role: message.role, snippet: entry.text.slice(Math.max(0, index - 45), index + q.length + 75), created_at: message.created_at }
    })
})
const hits = computed(() => [...results.value.filter(hit => hit.message_id !== props.liveMessage?.message_id), ...live.value])
async function search(append = false) {
  const version = generation, id = props.conversationId, q = query.value.trim()
  if (!id || !q || busy.value) return
  busy.value = true; error.value = ''
  try {
    const response = await searchConversation(id, q, append ? offset : 0)
    if (version !== generation) return
    results.value = append ? [...results.value, ...response.items] : response.items
    offset = (append ? offset : 0) + response.items.length; more.value = response.has_more
  } catch (cause) { if (version === generation) error.value = String(cause) }
  finally { if (version === generation) busy.value = false }
}
watch(query, () => {
  generation++; clearTimeout(timer); busy.value = false; results.value = []; more.value = false; error.value = ''
  if (query.value.trim()) timer = setTimeout(() => void search(), 200)
  else emit('clear')
})
watch(() => [props.conversationId, workspace.vaultId], () => { generation++; clearTimeout(timer); query.value = ''; results.value = []; more.value = false; busy.value = false; error.value = ''; emit('clear') })
watch(() => props.liveMessage, (next, previous) => { if (!next && previous && query.value.trim()) { generation++; busy.value = false; void search() } })
onBeforeUnmount(() => { generation++; clearTimeout(timer) })
</script>
<template>
  <section class="conversation-search" :aria-label="t('对话内搜索', 'Search conversation')">
    <div class="inline-actions"><input v-model="query" class="input" maxlength="200" :disabled="!conversationId" :aria-label="t('搜索当前会话', 'Search current conversation')" :placeholder="t('搜索正文或操作摘要', 'Search messages or operations')" @keydown.esc="query = ''" /><button v-if="query" class="button-secondary" @click="query = ''">{{ t('清空', 'Clear') }}</button></div>
    <p v-if="busy" role="status">{{ t('正在搜索…', 'Searching…') }}</p><p v-if="error" role="alert" class="error-banner">{{ error }}</p>
    <div v-if="query.trim()" class="search-results">
      <p v-if="!hits.length && !busy && !error" role="status">{{ t('没有找到匹配内容', 'No matches found') }}</p>
      <button v-for="hit in hits" :key="`${hit.message_id}:${hit.kind}:${hit.entry_index}`" class="search-hit button-secondary" @click="emit('locate', hit, query.trim())"><span>{{ hit.position ? t(`消息 ${hit.position}`, `Message ${hit.position}`) : t('当前生成内容', 'Live response') }} · {{ hit.kind === 'tool' ? t('操作', 'Operation') : hit.role === 'user' ? t('用户', 'User') : 'AI' }} · {{ new Date(hit.created_at).toLocaleString() }}</span><span>{{ hit.snippet }}</span></button>
      <button v-if="more" class="button-secondary" :disabled="busy" @click="search(true)">{{ t('更多结果', 'More results') }}</button>
    </div>
  </section>
</template>
<style scoped>
.conversation-search { padding: var(--space-sm) var(--space-xl); border-bottom: 1px solid var(--color-border-subtle); min-width: 0; }
.conversation-search input { flex: 1; min-width: 120px; }
.search-results { display: grid; gap: var(--space-xs); max-height: 200px; overflow: auto; }
.search-hit { display: grid; gap: 4px; width: 100%; text-align: left; white-space: normal; overflow-wrap: anywhere; }
.search-hit span:first-child { color: var(--color-text-secondary); font-size: var(--font-size-xs); }
</style>
