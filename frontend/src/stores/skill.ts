import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Skill } from '@/contracts'
import * as skillService from '@/services/skillService'
import { t } from '@/i18n'

export const useSkillStore = defineStore('skill', () => {
  const skills = ref<Skill[]>([])
  const selectedSkillId = ref<string | null>(null)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  const selectedSkill = computed(() =>
    skills.value.find((s) => s.skill_id === selectedSkillId.value) || null
  )

  const enabledSkills = computed(() => skills.value.filter((s) => s.enabled))
  const installedSkills = computed(() => skills.value.filter((s) => s.status !== 'error'))
  const readySkills = computed(() => skills.value.filter((s) => s.status === 'ready'))

  async function loadSkills() {
    isLoading.value = true
    try {
      skills.value = await skillService.listSkills()
      error.value = null
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : t('Skill 加载失败', 'Failed to load Skills')
    } finally {
      isLoading.value = false
    }
  }

  function selectSkill(skillId: string | null) {
    selectedSkillId.value = skillId
  }

  async function installSkill(packagePath: string | File) {
    const installed = await skillService.installSkill(packagePath)
    const index = skills.value.findIndex((skill) => skill.skill_id === installed.skill_id)
    if (index >= 0) skills.value[index] = installed
    else skills.value.unshift(installed)
    selectedSkillId.value = installed.skill_id
  }

  async function enableSkill(skillId: string) {
    const updated = await skillService.enableSkill(skillId)
    const index = skills.value.findIndex((skill) => skill.skill_id === skillId)
    if (index >= 0) skills.value[index] = updated
  }

  async function disableSkill(skillId: string) {
    const updated = await skillService.disableSkill(skillId)
    const index = skills.value.findIndex((skill) => skill.skill_id === skillId)
    if (index >= 0) skills.value[index] = updated
  }

  async function uninstallSkill(skillId: string) {
    await skillService.uninstallSkill(skillId)
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
    error,
    loadSkills,
    selectSkill,
    installSkill,
    enableSkill,
    disableSkill,
    uninstallSkill,
  }
})
