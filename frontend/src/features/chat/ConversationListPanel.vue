<script setup lang="ts">
import { useChatStore } from '@/stores/chat'

const chatStore = useChatStore()
</script>

<template>
  <div class="sidebar-panel">
    <button class="button-primary new-button" @click="chatStore.createNewConversation">＋ 新对话</button>
    <div class="sidebar-list conversation-list">
      <div v-for="conversation in chatStore.sortedConversations" :key="conversation.conversation_id"
        class="sidebar-list-item conversation" :class="{ active: chatStore.activeConversationId === conversation.conversation_id }"
        @click="chatStore.setActiveConversation(conversation.conversation_id)">
        <div><strong>{{ conversation.title }}</strong><p>{{ conversation.message_count }} 条消息</p></div>
        <button class="delete" title="删除会话" @click.stop="chatStore.deleteConversation(conversation.conversation_id)">×</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.new-button { width: 100%; margin-bottom: var(--space-md); }
.conversation-list { gap: var(--space-xs); }
.conversation { display: flex; align-items: center; justify-content: space-between; gap: var(--space-sm); }
.conversation strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--font-size-sm); }
.conversation p { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.delete { padding: var(--space-xs); color: var(--color-text-tertiary); }
</style>
