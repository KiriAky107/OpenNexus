<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ProviderPreset } from '@/contracts'
import ProviderLogo from './ProviderLogo.vue'
import { t } from '@/i18n'

const props = defineProps<{ presets: ProviderPreset[]; modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const search = ref('')
const filtered = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return props.presets.filter(preset => [preset.name, preset.preset_id, preset.description, preset.base_url]
    .some(value => value?.toLocaleLowerCase().includes(query)))
})
</script>

<template>
  <div class="preset-selector">
    <label class="field" for="provider-search"><span>{{ t('提供商预设', 'Provider presets') }}</span><input id="provider-search" v-model="search" class="input" type="search" :placeholder="t('搜索提供商，例如 通义千问 / DeepSeek', 'Search providers, such as Qwen / DeepSeek')" /></label>
    <div class="preset-grid" role="group" :aria-label="t('提供商预设', 'Provider presets')">
      <button type="button" class="preset-chip" :class="{ selected: !modelValue }" :aria-pressed="!modelValue" @click="emit('update:modelValue', '')"><ProviderLogo /><span>{{ t('自定义', 'Custom') }}</span></button>
      <button v-for="preset in filtered" :key="preset.preset_id" type="button" class="preset-chip" :class="{ selected: modelValue === preset.preset_id }" :aria-pressed="modelValue === preset.preset_id" :title="preset.description || preset.name" :data-preset="preset.preset_id" @click="emit('update:modelValue', preset.preset_id)">
        <ProviderLogo :logo-id="preset.logo_id || preset.preset_id" /><span>{{ preset.name }}</span>
      </button>
    </div>
    <p v-if="search && !filtered.length" class="subtle" role="status">{{ t('没有匹配的预设，可以使用自定义服务。', 'No matching preset. You can use a custom service.') }}</p>
  </div>
</template>

<style scoped>
.preset-selector { display: grid; gap: var(--space-sm); }
.preset-grid { display: flex; flex-wrap: wrap; gap: 8px; max-height: 220px; overflow-y: auto; padding: 3px; }
.preset-chip { display: inline-flex; align-items: center; gap: 7px; padding: 6px 10px; border: 1px solid var(--color-border-default); border-radius: 11px; background: var(--color-surface-primary); color: var(--color-text-primary); cursor: pointer; font: inherit; font-size: 13px; }
.preset-chip:hover { background: var(--color-background-hover); }
.preset-chip.selected { border-color: #377cf6; background: color-mix(in srgb, #377cf6 12%, var(--color-surface-primary)); color: #377cf6; box-shadow: 0 0 0 1px #377cf6; }
.preset-chip:focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: 2px; }
</style>
