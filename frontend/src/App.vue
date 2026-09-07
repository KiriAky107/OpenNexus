<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/common/AppShell.vue'
import TitleBar from '@/components/common/TitleBar.vue'
import { isDesktop } from '@/services/platform/desktop'

const route = useRoute()
const isVaultEntry = computed(() => route.path === '/')
const desktop = isDesktop()
</script>

<template>
  <div v-if="isVaultEntry" class="entry-shell">
    <TitleBar v-if="desktop" />
    <router-view />
  </div>
  <AppShell v-else>
    <router-view />
  </AppShell>
</template>

<style scoped>
.entry-shell {
  display: flex;
  flex-direction: column;
  width: 100vw;
  height: 100vh;
}
</style>
