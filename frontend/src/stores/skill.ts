import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Skill, UserSkill, UserSkillWriteRequest } from '@/contracts'
import * as skillService from '@/services/skillService'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

export const useSkillStore = defineStore('skill', () => {
  const workspace = useWorkspaceStore()
  const skills = ref<Skill[]>([])
  const selectedSkillId = ref<string | null>(null)
  const isLoading = ref(false)
  const error = ref<string | null>(null)
  const userSkills = ref<UserSkill[]>([])
  const userSkillError = ref<string | null>(null)

  const selectedSkill = computed(() =>
    skills.value.find((s) => s.skill_id === selectedSkillId.value) || null
  )

  const enabledSkills = computed(() => skills.value.filter((s) => s.enabled))
  const installedSkills = computed(() => skills.value.filter((s) => s.status !== 'error'))
  const readySkills = computed(() => skills.value.filter((s) => s.status === 'ready'))
  const readyUserSkills = computed(() => userSkills.value.filter((skill) => skill.status === 'ready'))

  async function loadSkills() {
    isLoading.value = true
    const vault = workspace.vaultId
    try {
      const [installed, user] = await Promise.allSettled([
        skillService.listSkills(), vault ? skillService.listUserSkills() : Promise.resolve([]),
      ])
      if (installed.status === 'fulfilled') { skills.value = installed.value; error.value = null }
      else error.value = installed.reason instanceof Error ? installed.reason.message : t('Skill 加载失败', 'Failed to load Skills')
      if (workspace.vaultId !== vault) return
      if (user.status === 'fulfilled') { userSkills.value = user.value; userSkillError.value = null }
      else { userSkills.value = []; userSkillError.value = user.reason instanceof Error ? user.reason.message : t('用户 Skill 加载失败', 'Failed to load user Skills') }
    } finally {
      isLoading.value = false
    }
  }

  function assertVault(vaultId: string) {
    if (!vaultId || workspace.vaultId !== vaultId) throw new Error('WORKSPACE_CHANGED')
  }

  async function createUserSkill(request: UserSkillWriteRequest, vaultId: string, operationId?: string) {
    assertVault(vaultId)
    const created = await skillService.createUserSkill(request, operationId)
    assertVault(vaultId)
    userSkills.value.unshift(created); userSkillError.value = null
    return created
  }

  async function updateUserSkill(skillId: string, request: UserSkillWriteRequest, vaultId: string, operationId?: string) {
    assertVault(vaultId)
    const updated = await skillService.updateUserSkill(skillId, request, operationId)
    assertVault(vaultId)
    const index = userSkills.value.findIndex(skill => skill.skill_id === skillId)
    if (index >= 0) userSkills.value[index] = updated
    userSkillError.value = null
    return updated
  }

  async function deleteUserSkill(skillId: string, revision: string, vaultId: string, operationId?: string) {
    assertVault(vaultId)
    await skillService.deleteUserSkill(skillId, revision, operationId)
    assertVault(vaultId)
    userSkills.value = userSkills.value.filter(skill => skill.skill_id !== skillId)
    userSkillError.value = null
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
    userSkills,
    readyUserSkills,
    userSkillError,
    isLoading,
    error,
    loadSkills,
    selectSkill,
    installSkill,
    enableSkill,
    disableSkill,
    uninstallSkill,
    createUserSkill,
    updateUserSkill,
    deleteUserSkill,
  }
})
