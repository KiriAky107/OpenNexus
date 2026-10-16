<script setup lang="ts">
import { t } from '@/i18n'
import type { SyncRemoteVault } from './syncVaults'
defineProps<{ items: SyncRemoteVault[]; disabled?: boolean }>()
const selected = defineModel<string>({ required: true })
</script>

<template>
  <label class="sync-vault-selector">{{ t('远端库', 'Remote vault') }}
    <select v-model="selected" :disabled="disabled">
      <option value="">{{ t('请选择', 'Choose a vault') }}</option>
      <option v-for="vault in items" :key="vault.id" :value="vault.id">{{ vault.name }} · {{ vault.sequence }}</option>
    </select>
  </label>
</template>

<style scoped>
.sync-vault-selector { display:grid; gap:var(--space-sm); min-width:0; }
select { width:100%; min-width:0; max-width:100%; min-height:44px; box-sizing:border-box; padding:8px 12px; border:1px solid var(--color-border-default); border-radius:var(--radius-md); background:var(--color-surface-primary); color:var(--color-text-primary); font:inherit; }
option { background:var(--color-surface-primary); color:var(--color-text-primary); }
select:disabled { opacity:.55; }
</style>
