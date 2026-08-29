import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Skill } from '@/contracts'
import { mockSkills } from '@/services/skillService'

export const useSkillStore = defineStore('skill', () => {
  const skills = ref<Skill[]>(mockSkills)
  const selectedSkillId = ref<string | null>(null)
  const isLoading = ref(false)

  const selectedSkill = computed(() =>
    skills.value.find((s) => s.skill_id === selectedSkillId.value) || null
  )

  const enabledSkills = computed(() => skills.value.filter((s) => s.enabled))
  const installedSkills = computed(() => skills.value.filter((s) => s.status !== 'error'))
  const readySkills = computed(() => skills.value.filter((s) => s.status === 'ready'))

  async function loadSkills() {
    isLoading.value = true
    try {
      const { listSkills } = await import('@/services/skillService')
      skills.value = await listSkills()
    } finally {
      isLoading.value = false
    }
  }

  function selectSkill(skillId: string | null) {
    selectedSkillId.value = skillId
  }

  async function enableSkill(skillId: string) {
    const skill = skills.value.find((s) => s.skill_id === skillId)
    if (skill) {
      skill.enabled = true
      skill.status = 'ready'
    }
  }

  async function disableSkill(skillId: string) {
    const skill = skills.value.find((s) => s.skill_id === skillId)
    if (skill) {
      skill.enabled = false
      skill.status = 'disabled'
    }
  }

  async function uninstallSkill(skillId: string) {
    const idx = skills.value.findIndex((s) => s.skill_id === skillId)
    if (idx > -1) skills.value.splice(idx, 1)
    if (selectedSkillId.value === skillId) selectedSkillId.value = null
  }

  return {
    skills,
    selectedSkillId,
    selectedSkill,
    enabledSkills,
    installedSkills,
    readySkills,
    isLoading,
    loadSkills,
    selectSkill,
    enableSkill,
    disableSkill,
    uninstallSkill,
  }
})
