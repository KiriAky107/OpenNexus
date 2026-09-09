// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import UserSkillEditor from './UserSkillEditor.vue'
import { useWorkspaceStore } from '@/stores/workspace'
import { useSkillStore } from '@/stores/skill'
import * as service from '@/services/skillService'
import type { UserSkill } from '@/contracts'

vi.mock('@/services/skillService', () => ({
  listSkills: vi.fn(), listUserSkills: vi.fn(), createUserSkill: vi.fn(), updateUserSkill: vi.fn(), deleteUserSkill: vi.fn(),
}))

const saved: UserSkill = {
  skill_id: 'user_skill_' + '1'.repeat(32), revision: 'a'.repeat(64), status: 'ready',
  missing_dependencies: [], undeclared_permissions: [],
  data: { version: 1, name: 'Review', description: '', prompt: 'Review carefully', tools: ['notes.read'], permissions: ['notes.read'], retrieval: { top_k: 10, rerank: true, citation: true }, required_capabilities: ['chat'], created_at_ms: 1, updated_at_ms: 1 },
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  useWorkspaceStore().vaultId = 'vault-one'
  vi.mocked(service.listSkills).mockResolvedValue([])
  vi.mocked(service.listUserSkills).mockResolvedValue([])
  vi.mocked(service.createUserSkill).mockResolvedValue(saved)
  vi.mocked(service.updateUserSkill).mockResolvedValue({ ...saved, revision: 'b'.repeat(64), data: { ...saved.data, version: 2 } })
  vi.mocked(service.deleteUserSkill).mockResolvedValue({ status: 'completed' })
})

it('creates a complete declarative record and labels declarations as non-grants', async () => {
  const wrapper = mount(UserSkillEditor)
  await wrapper.findAll('input.input')[0]!.setValue('Review')
  await wrapper.findAll('input.input')[1]!.setValue('notes.read')
  await wrapper.get('textarea').setValue('Review carefully')
  await wrapper.get('input[value="notes.read"]').setValue(true)
  await wrapper.get('input[value="chat"]').setValue(true)
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(service.createUserSkill).toHaveBeenCalledWith(expect.objectContaining({
    revision: '', name: 'Review', prompt: 'Review carefully', tools: ['notes.read'],
    permissions: ['notes.read'], required_capabilities: ['chat'],
    retrieval: { top_k: 10, rerank: true, citation: true },
  }), expect.stringMatching(/^[0-9a-f-]{36}$/))
  expect(wrapper.text()).toContain('不是设备授权')
  expect(useSkillStore().userSkills).toHaveLength(1)
  wrapper.unmount()
})

it('does not publish a late save response into a different Vault', async () => {
  let finish!: (value: UserSkill) => void
  vi.mocked(service.createUserSkill).mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const wrapper = mount(UserSkillEditor)
  await wrapper.findAll('input.input')[0]!.setValue('Review')
  await wrapper.get('form').trigger('submit')
  useWorkspaceStore().vaultId = 'vault-two'
  await wrapper.vm.$nextTick()
  finish(saved)
  await flushPromises()
  expect(useSkillStore().userSkills).toEqual([])
  expect(wrapper.text()).toContain('WORKSPACE_CHANGED')
  wrapper.unmount()
})

it('does not publish a late list response into a different Vault', async () => {
  let finish!: (value: UserSkill[]) => void
  vi.mocked(service.listUserSkills).mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const store = useSkillStore()
  const loading = store.loadSkills()
  useWorkspaceStore().vaultId = 'vault-two'
  finish([saved])
  await loading
  expect(store.userSkills).toEqual([])
})

it('reuses the operation UUID after an ambiguous save failure and changes it with the payload', async () => {
  vi.mocked(service.createUserSkill)
    .mockRejectedValueOnce(new Error('HOST_TIMEOUT'))
    .mockResolvedValueOnce(saved)
    .mockRejectedValueOnce(new Error('HOST_TIMEOUT'))
  const wrapper = mount(UserSkillEditor)
  const name = wrapper.findAll('input.input')[0]!
  await name.setValue('Review')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  const firstOperation = vi.mocked(service.createUserSkill).mock.calls[0]![1]
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(vi.mocked(service.createUserSkill).mock.calls[1]![1]).toBe(firstOperation)
  await wrapper.findAll('button').find(button => button.text().includes('清空表单'))!.trigger('click')
  await name.setValue('Changed')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(vi.mocked(service.createUserSkill).mock.calls[2]![1]).not.toBe(firstOperation)
  wrapper.unmount()
})
