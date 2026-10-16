<script setup lang="ts">
import appLogoUrl from '@/assets/opennexus-logo.svg'
import darkAppLogoUrl from '@/assets/opennexus-logo-dark.svg'
withDefaults(defineProps<{ desktop?: boolean; compact?: boolean; vaultName?: string; documentTitle: string; dirty?: boolean; dark?: boolean }>(), {
  desktop: false, compact: false, vaultName: '', dirty: false, dark: false,
})
</script>

<template>
  <header class="titlebar" :class="{ desktop, compact }" :data-tauri-drag-region="desktop ? '' : undefined">
    <slot name="leading" />
    <div class="titlebar-left" :data-tauri-drag-region="desktop ? '' : undefined">
      <span v-if="vaultName" class="vault-name" :data-tauri-drag-region="desktop ? '' : undefined">{{ vaultName }}</span><span v-if="vaultName" class="title-separator" :data-tauri-drag-region="desktop ? '' : undefined">/</span>
      <span class="file-name" :data-tauri-drag-region="desktop ? '' : undefined" :class="{ dirty }">{{ documentTitle }}<span v-if="dirty" class="dirty-dot" /></span>
    </div>
    <div class="titlebar-center" :data-tauri-drag-region="desktop ? '' : undefined"><span class="app-name" :data-tauri-drag-region="desktop ? '' : undefined"><img :src="dark ? darkAppLogoUrl : appLogoUrl" alt="" />OpenNexus</span></div>
    <div class="titlebar-right"><slot /></div>
  </header>
</template>

<style scoped>

.titlebar {
  height: var(--titlebar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-lg);
  background: var(--color-surface-secondary);
  border-bottom: 1px solid var(--color-border-subtle);
  font-size: var(--font-size-sm);
  flex-shrink: 0;
  z-index: var(--z-titlebar);
  user-select: none;
  -webkit-app-region: drag;
}

.titlebar-left {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  min-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.vault-name {
  color: var(--color-text-secondary);
  font-weight: 500;
}

.title-separator {
  color: var(--color-text-tertiary);
}

.file-name {
  color: var(--color-text-primary);
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  &.dirty {
    color: var(--color-accent-primary);
  }
}

.dirty-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent-primary);
  flex-shrink: 0;
}

.titlebar-center {
  color: var(--color-text-tertiary);
  font-size: var(--font-size-sm);
}

.app-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-full);
  background: var(--color-surface-primary);
  color: var(--color-text-secondary);
  font-weight: 650;
  letter-spacing: .04em;
}

.app-name img {
  width: 18px;
  height: 18px;
}

.titlebar-right {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  min-width: 200px;
  justify-content: flex-end;
}

:deep(.icon-btn) {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  font-size: 14px;
  -webkit-app-region: no-drag;
  transition: background-color var(--motion-fast), color var(--motion-fast);

  &:hover {
    background: var(--color-background-hover);
    color: var(--color-text-primary);
  }
}

.icon {
  font-size: 14px;
}

:deep(.window-controls) {
  display: flex;
  align-items: center;
  gap: 2px;
  padding-left: 8px;
  border-left: 1px solid var(--color-border-subtle);
  -webkit-app-region: no-drag;
}

:deep(.win-btn) {
  width: 38px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  padding: 0;
  border: 0;
  background: transparent;
  transition: background-color var(--motion-fast), color var(--motion-fast);

  &:hover {
    background: var(--color-background-hover);
    color: var(--color-text-primary);
  }

  &.close:hover {
    background: var(--color-error);
    color: var(--color-on-error);
  }
}
:deep(.icon-btn).active { color: var(--color-accent-primary); background: var(--color-accent-soft); }
:deep(.win-btn):focus-visible, :deep(.icon-btn):focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: -2px; }
:deep(.win-btn):active, :deep(.icon-btn):active { background: var(--color-background-active); }
:deep(.win-btn).close:active { background: var(--color-error); color: var(--color-on-error); }

@media (max-width: 760px) {
  .titlebar-left, .titlebar-right { min-width: 0; }
  .titlebar-center { display: none; }
  .file-name { max-width: 42vw; }
}

.compact { min-height: 52px; height: auto; padding: 4px 8px; gap: 4px; -webkit-app-region: no-drag; }
.compact .titlebar-left { flex: 1; min-width: 0; }
.compact .titlebar-right { min-width: 0; gap: 2px; }
.compact :deep(.icon-btn) { min-width: 44px; min-height: 44px; padding: 0; }
.compact .file-name { max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
</style>
