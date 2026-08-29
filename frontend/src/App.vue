<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/common/AppShell.vue'

const route = useRoute()
const isVaultEntry = computed(() => route.path === '/')
</script>

<template>
  <AppShell v-if="!isVaultEntry">
    <router-view />
  </AppShell>
  <router-view v-else />
</template>

<style scoped>
</style>
