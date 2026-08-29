<script setup lang="ts">
import type { FileNode } from '@/contracts'

defineProps<{ node: FileNode; activePath: string | null }>()
const emit = defineEmits<{
  open: [node: FileNode]
  contextMenu: [event: MouseEvent, node: FileNode]
}>()
</script>

<template>
  <div>
    <div class="tree-node" :class="{ active: node.path === activePath }"
      @click="emit('open', node)" @contextmenu="emit('contextMenu', $event, node)">
      <span>{{ node.type === 'folder' ? (node.is_open ? '📂' : '📁') : '📄' }}</span>
      <span class="name">{{ node.name }}</span>
      <span v-if="node.is_dirty">●</span>
    </div>
    <div v-if="node.type === 'folder' && node.is_open" class="children">
      <FileTreeNode v-for="child in node.children ?? []" :key="child.id" :node="child" :active-path="activePath"
        @open="emit('open', $event)" @context-menu="(event, target) => emit('contextMenu', event, target)" />
    </div>
  </div>
</template>

<style scoped>
.tree-node { display: flex; align-items: center; gap: var(--space-xs); min-height: 28px; padding: 0 var(--space-sm); border-radius: var(--radius-sm); cursor: pointer; }
.tree-node:hover, .tree-node.active { background: var(--color-background-secondary); }
.name { min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.children { padding-left: var(--space-md); }
</style>
