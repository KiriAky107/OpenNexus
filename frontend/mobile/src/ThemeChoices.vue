<script setup lang="ts">
import { computed } from 'vue'
import ThemeCard from '../../src/features/themes/ThemeCard.vue'
import { t } from '../../src/i18n'
import type { BuiltinTheme, ThemeChoice } from './theme'
defineProps<{ modelValue: ThemeChoice; resolved: BuiltinTheme; label: string }>()
defineEmits<{ 'update:modelValue': [choice: ThemeChoice] }>()
const choices = computed(() => [
  { id: 'system' as const, name: t('跟随系统', 'System'), description: t('跟随设备的浅色或深色外观', 'Follow the device light or dark appearance') },
  { id: 'light' as const, name: t('浅色', 'Light'), description: t('默认浅色主题', 'Default light theme') },
  { id: 'dark' as const, name: t('深色', 'Dark'), description: t('默认深色主题', 'Default dark theme') },
  { id: 'sepia' as const, name: t('护眼', 'Sepia'), description: t('暖色低眩光主题', 'Warm low-glare theme') },
])
</script>

<template><div class="theme-choices" role="group" :aria-label="label"><ThemeCard v-for="theme in choices" :key="theme.id" :name="theme.name" :description="theme.description" :preview="theme.id === 'system' ? resolved : theme.id" :selected="modelValue === theme.id" @select="$emit('update:modelValue', theme.id)" /></div></template>

<style scoped>
.theme-choices { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-md); margin-block: var(--space-md); }
.theme-choices :deep(.item-card) { padding: var(--space-md); }
@media (max-width: 380px) { .theme-choices { grid-template-columns: 1fr; } }
</style>
