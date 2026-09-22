<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/common/AppShell.vue'
import TitleBar from '@/components/common/TitleBar.vue'
import TitleBarMenu from '@/components/common/TitleBarMenu.vue'
import { isDesktop } from '@/services/platform/desktop'

const route = useRoute()
const isVaultEntry = computed(() => route.path === '/')
const desktop = isDesktop()
</script>

<template>
  <div v-if="isVaultEntry" class="entry-shell">
    <TitleBar v-if="desktop" />
    <TitleBarMenu v-if="desktop" />
    <main class="entry-content">
      <router-view />
    </main>
  </div>
  <AppShell v-else>
    <router-view />
  </AppShell>
</template>

<style scoped>
.entry-shell {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100dvh;
  min-height: 0;
  overflow: hidden;
}

.entry-content {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}
</style>
