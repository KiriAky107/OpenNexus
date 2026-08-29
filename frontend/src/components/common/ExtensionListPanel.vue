<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { usePluginStore } from '@/stores/plugin'
import { useSkillStore } from '@/stores/skill'

const route = useRoute()
const skillStore = useSkillStore()
const pluginStore = usePluginStore()
const isPlugin = computed(() => route.name === 'plugins')

onMounted(() => { if (isPlugin.value) void pluginStore.loadPlugins(); else void skillStore.loadSkills() })
</script>

<template>
  <div class="sidebar-panel">
    <div v-if="isPlugin" class="sidebar-list">
      <button v-for="plugin in pluginStore.plugins" :key="plugin.plugin_id" class="sidebar-list-item extension-item"
        :class="{ active: pluginStore.selectedPluginId === plugin.plugin_id }" @click="pluginStore.selectPlugin(plugin.plugin_id)">
        <span>{{ plugin.icon || '🧩' }}</span><span><strong>{{ plugin.name }}</strong><small>{{ plugin.status }}</small></span>
      </button>
    </div>
    <div v-else class="sidebar-list">
      <button v-for="skill in skillStore.skills" :key="skill.skill_id" class="sidebar-list-item extension-item"
        :class="{ active: skillStore.selectedSkillId === skill.skill_id }" @click="skillStore.selectSkill(skill.skill_id)">
        <span>{{ skill.icon || '⚡' }}</span><span><strong>{{ skill.name }}</strong><small>{{ skill.status }}</small></span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.extension-item { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: var(--space-sm); width: 100%; text-align: left; }
.extension-item strong, .extension-item small { display: block; }
.extension-item small { color: var(--color-text-tertiary); }
</style>
