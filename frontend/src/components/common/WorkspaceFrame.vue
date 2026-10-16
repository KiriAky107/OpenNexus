<script setup lang="ts">
const props = withDefaults(defineProps<{ compact?: boolean; primaryOpen?: boolean; secondaryOpen?: boolean; closePanelsLabel?: string }>(), {
  compact: false, primaryOpen: false, secondaryOpen: false, closePanelsLabel: 'Close sidebar',
})
const emit = defineEmits<{ closePanels: [] }>()
function escape(event: KeyboardEvent) {
  if (!props.compact || (!props.primaryOpen && !props.secondaryOpen)) return
  event.preventDefault(); event.stopPropagation(); emit('closePanels')
}
</script>

<template>
  <div class="app-shell" :class="{ compact, 'primary-open': primaryOpen, 'secondary-open': secondaryOpen }" @keydown.esc="escape">
    <slot name="titlebar" /><slot name="menu" />
    <div class="app-body">
      <button v-if="compact && (primaryOpen || secondaryOpen)" class="panel-backdrop" type="button" :aria-label="closePanelsLabel" @click="emit('closePanels')" />
      <slot name="primary" /><slot name="secondary" />
      <main class="main-content" :inert="compact && (primaryOpen || secondaryOpen) ? true : undefined"><slot /></main>
    </div>
    <slot name="status" /><slot name="overlays" />
  </div>
</template>

<style scoped>

.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
  background: var(--color-background-secondary);
  color: var(--color-text-primary);
}

.app-body {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

.main-content {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  background: var(--color-background-primary);
  isolation: isolate;
}

.compact { height: 100dvh; box-sizing: border-box; padding-top: env(safe-area-inset-top); padding-bottom: env(safe-area-inset-bottom); }
.compact .app-body { position: relative; }
.compact :deep(.primary-sidebar), .compact :deep(.secondary-sidebar) { position: absolute; inset: 0 auto 0 0; width: min(86vw, 340px) !important; z-index: 31; box-shadow: var(--shadow-xl); }
.compact:not(.primary-open) :deep(.primary-sidebar), .compact:not(.secondary-open) :deep(.secondary-sidebar) { display: none; }
.panel-backdrop { position: absolute; inset: 0; left: min(86vw, 340px); z-index: 30; min-height: 0; padding: 0; border: 0; border-radius: 0; background: var(--color-background-overlay); }
.compact :deep(.tree-node) { min-height: 44px; }
.compact :deep(.primary-sidebar .nav-item) { min-height: 44px; }
</style>
