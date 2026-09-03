<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ logoId?: string }>()
const assets = import.meta.glob<string>('../../assets/providers/*.svg', { eager: true, query: '?url', import: 'default' })
const source = computed(() => {
  const id = props.logoId === 'openai-responses' ? 'openai' : props.logoId
  return assets[`../../assets/providers/${id}.svg`]
})
</script>

<template>
  <span class="provider-logo" :class="{ 'dark-logo': logoId === 'kimi' }" aria-hidden="true">
    <img v-if="source" :src="source" alt="" width="22" height="22" />
    <span v-else class="custom-logo">+</span>
  </span>
</template>

<style scoped>
.provider-logo { display: inline-flex; flex: 0 0 28px; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 7px; background: #fff; color: #252b36; }
img { display: block; object-fit: contain; }
.dark-logo { background: #111; }
.custom-logo { font-size: 23px; line-height: 1; }
</style>
