import { beforeEach, expect, it, vi } from 'vitest'
import { apiClient } from './apiClient'
import * as service from './skillService'

vi.mock('./apiClient', () => {
  const client = { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), postBinary: vi.fn() }
  return { apiClient: client, default: client }
})

const request = {
  revision: '', name: 'Review', description: '', prompt: 'Review carefully', tools: ['notes.read'],
  permissions: ['notes.read'], retrieval: { top_k: 10, rerank: true, citation: true }, required_capabilities: ['chat'],
}

beforeEach(() => vi.clearAllMocks())

it('uses dedicated user Skill routes and stable idempotency keys', async () => {
  const createOperation = '00000000-0000-4000-8000-000000000001'
  const updateOperation = '00000000-0000-4000-8000-000000000002'
  const deleteOperation = '00000000-0000-4000-8000-000000000003'
  vi.mocked(apiClient.get).mockResolvedValue({ items: [] })
  vi.mocked(apiClient.post).mockResolvedValue({})
  vi.mocked(apiClient.put).mockResolvedValue({})
  vi.mocked(apiClient.delete).mockResolvedValue({ status: 'completed' })
  await service.listUserSkills()
  await service.createUserSkill(request, createOperation)
  await service.updateUserSkill('user_skill_' + '1'.repeat(32), { ...request, revision: 'a'.repeat(64) }, updateOperation)
  await service.deleteUserSkill('user_skill_' + '1'.repeat(32), 'b'.repeat(64), deleteOperation)
  expect(apiClient.get).toHaveBeenCalledWith('/api/user-skills', { params: { limit: 100, offset: 0 } })
  expect(apiClient.post).toHaveBeenCalledWith('/api/user-skills', request, { headers: { 'Idempotency-Key': createOperation } })
  expect(apiClient.put).toHaveBeenCalledWith('/api/user-skills/user_skill_' + '1'.repeat(32), expect.objectContaining({ revision: 'a'.repeat(64) }), { headers: { 'Idempotency-Key': updateOperation } })
  expect(apiClient.delete).toHaveBeenCalledWith('/api/user-skills/user_skill_' + '1'.repeat(32), { params: { revision: 'b'.repeat(64) }, headers: { 'Idempotency-Key': deleteOperation } })
})

it('loads every bounded Host page instead of silently truncating user Skills', async () => {
  const items = Array.from({ length: 101 }, (_, index) => ({ skill_id: `user_skill_${String(index).padStart(32, '0')}` }))
  vi.mocked(apiClient.get)
    .mockResolvedValueOnce({ items: items.slice(0, 100), page: { total: 101 } })
    .mockResolvedValueOnce({ items: items.slice(100), page: { total: 101 } })
  expect(await service.listUserSkills()).toHaveLength(101)
  expect(apiClient.get).toHaveBeenNthCalledWith(2, '/api/user-skills', { params: { limit: 100, offset: 100 } })
})
