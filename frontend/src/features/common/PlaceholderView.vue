<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const title = computed(() => String(route.meta.title ?? '功能开发中'))
</script>

<template>
  <section class="placeholder-view">
    <div>
      <h1>{{ title }}</h1>
      <p>基础路由已经就绪，具体页面将在后续功能开发中实现。</p>
    </div>
  </section>
</template>

<style scoped>
.placeholder-view {
  display: grid;
  height: 100%;
  place-items: center;
  color: var(--color-text-secondary);
}

.placeholder-view div {
  text-align: center;
}

.placeholder-view h1 {
  margin: 0 0 var(--space-sm);
  color: var(--color-text-primary);
}
</style>
