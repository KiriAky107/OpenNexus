import { afterEach, expect, it, vi } from 'vitest'
const native = vi.hoisted(() => ({ invoke: vi.fn() }))
vi.mock('@tauri-apps/api/core', () => ({ invoke: native.invoke }))
vi.mock('./platform/desktop', () => ({ isDesktop: () => true }))
import { installRelease } from './communityService'
import vector from './fixtures/community-python-vector.json'
import type { CommunityRelease, CommunitySource } from '@/contracts/community'
afterEach(() => { vi.unstubAllGlobals(); native.invoke.mockReset() })
it('desktop stages through Host without renderer download or legacy installation', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  native.invoke.mockResolvedValue({ state: 'staged' })
  const source: CommunitySource = { id: 'fixture', url: 'https://catalog.example/', keys: [], enabled: true }
  const release = vector.release as CommunityRelease
  expect(await installRelease(source, release)).toContain('尚未安装或启用')
  expect(fetch).not.toHaveBeenCalled()
  const [command, args] = native.invoke.mock.calls[0]!
  expect(command).toBe('extension_stage')
  expect(args.request.release.signature).toBe(release.signature)
  expect(args.request.release).not.toHaveProperty('withdrawn')
  expect(args.request.release).not.toHaveProperty('download_path')
  expect(args.request.release).not.toHaveProperty('release_id')
  expect(vector.release).toHaveProperty('release_id')
})
