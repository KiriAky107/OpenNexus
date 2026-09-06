<script setup lang="ts">
import { ref } from 'vue'
import AppDialog from './AppDialog.vue'
import type { ActionDialogRequest } from '@/composables/useActionDialog'
import { t } from '@/i18n'
const props = defineProps<ActionDialogRequest>()
const emit = defineEmits<{ resolve: [value: string | null] }>()
const value = ref(props.initialValue)
</script>

<template>
  <AppDialog :label="mode === 'confirm' ? t('确认操作', 'Confirm action') : message" @close="emit('resolve', null)">
    <form class="modal action-dialog" @submit.prevent="emit('resolve', mode === 'prompt' ? value : '')">
      <span class="badge info">{{ mode === 'confirm' ? t('操作确认', 'Confirmation') : t('填写信息', 'Enter information') }}</span>
      <h2>{{ mode === 'confirm' ? t('确认操作', 'Confirm action') : t('请输入', 'Enter a value') }}</h2>
      <label v-if="mode === 'prompt'" class="action-field"><span>{{ message }}</span><input v-model="value" class="input" autofocus /></label>
      <p v-else class="action-message">{{ message }}</p>
      <footer>
        <button type="button" class="button-secondary" :autofocus="mode === 'confirm'" @click="emit('resolve', null)">{{ t('取消', 'Cancel') }}</button>
        <button type="submit" class="button-primary">{{ t('确定', 'Confirm') }}</button>
      </footer>
    </form>
  </AppDialog>
</template>

<style scoped>
.action-dialog { width: min(520px, 100%); }
h2 { margin: var(--space-sm) 0 var(--space-lg); }
.action-field { display: grid; gap: var(--space-md); }
.action-message, .action-field span { white-space: pre-wrap; overflow-wrap: anywhere; line-height: var(--line-height-relaxed); }
footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: var(--space-sm); margin-top: var(--space-xl); }
</style>
