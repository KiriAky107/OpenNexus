<script setup lang="ts">
import type { Component } from 'vue'

withDefaults(defineProps<{ icon: Component; size?: number }>(), { size: 18 })
</script>

<template>
  <component :is="icon" class="app-icon" :style="{ width: `${size}px`, height: `${size}px` }" aria-hidden="true" />
</template>

<style scoped>
.app-icon { display: inline-block; flex: 0 0 auto; color: currentColor; }
</style>
