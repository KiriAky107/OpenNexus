import { afterEach, expect, it, vi } from 'vitest'
import { createHash, webcrypto } from 'node:crypto'
const native = vi.hoisted(() => ({ invoke: vi.fn() }))
vi.mock('@tauri-apps/api/core', () => ({ invoke: native.invoke }))
vi.mock('./platform/desktop', () => ({ isDesktop: () => true }))
import { installRelease, stageDesktopRelease } from './communityService'
import vector from './fixtures/community-python-vector.json'
import type { CommunityRelease, CommunitySource } from '@/contracts/community'
afterEach(() => { vi.unstubAllGlobals(); native.invoke.mockReset() })
it('desktop stages through Host without renderer download or legacy installation', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  vi.stubGlobal('crypto', webcrypto)
  native.invoke.mockImplementation(async (command,args) => command === 'extension_stage_prepare' ? 'request-id' : { operation_id: args?.request?.operation_id, state: 'staged', version: vector.release.version, archive_sha256: vector.release.sha256, package_key: createHash('sha256').update(JSON.stringify(['https://catalog.example/',vector.release.namespace,vector.release.package_id,vector.release.version])).digest('hex') })
  const source: CommunitySource = { id: 'fixture', url: 'https://catalog.example/', keys: [], enabled: true }
  const release = vector.release as CommunityRelease
  expect(await installRelease(source, release)).toContain('尚未安装或启用')
  expect(fetch).not.toHaveBeenCalled()
  const [command, args] = native.invoke.mock.calls.find(call => call[0] === 'extension_stage')!
  expect(command).toBe('extension_stage')
  expect(args.request.release.signature).toBe(release.signature)
  expect(args.request.release).not.toHaveProperty('withdrawn')
  expect(args.request.release).not.toHaveProperty('download_path')
  expect(args.request.release).not.toHaveProperty('release_id')
  expect(vector.release).toHaveProperty('release_id')
})

it('cancels a prepared request when abort wins before dispatch', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const controller = new AbortController()
  native.invoke.mockImplementation(async command => {
    if (command === 'extension_stage_prepare') { controller.abort(); return 'request-id' }
    return null
  })
  await expect(installRelease({ id: 'fixture', url: 'https://catalog.example/', keys: [], enabled: true }, vector.release as CommunityRelease, controller.signal)).rejects.toThrow()
  expect(native.invoke.mock.calls.some(call => call[0] === 'extension_stage')).toBe(false)
  expect(native.invoke).toHaveBeenCalledWith('extension_stage_cancel', { requestId: 'request-id' })
})

it('rejects a staged receipt for a different package even when version and archive are identical', async () => {
  vi.stubGlobal('crypto', webcrypto)
  native.invoke.mockImplementation(async (command,args) => command === 'extension_stage_prepare' ? 'request-id' : {operation_id:args?.request?.operation_id,state:'staged',version:vector.release.version,archive_sha256:vector.release.sha256,package_key:'a'.repeat(64)})
  await expect(stageDesktopRelease({id:'fixture',url:'https://catalog.example/',keys:[],enabled:true},vector.release as CommunityRelease)).rejects.toThrow('回执')
  expect(native.invoke.mock.calls.some(call=>call[0]==='extension_install_confirm'||call[0]==='extension_enable')).toBe(false)
})

it('reports a verified staged receipt after cancellation races its durable commit',async()=>{
  vi.stubGlobal('crypto',webcrypto)
  const controller=new AbortController();let operationId:string|undefined
  native.invoke.mockImplementation(async(command,args)=>{
    if(command==='extension_stage_prepare')return 'request-id'
    if(command==='extension_stage'){operationId=args.request.operation_id;controller.abort();throw new Error('REQUEST_CANCELLED')}
    if(command==='extension_stage_status')return {operation_id:operationId,package_key:createHash('sha256').update(JSON.stringify(['https://catalog.example/',vector.release.namespace,vector.release.package_id,vector.release.version])).digest('hex'),archive_sha256:vector.release.sha256,version:vector.release.version,state:'staged'}
    return null
  })
  expect(await installRelease({id:'fixture',url:'https://catalog.example/',keys:[],enabled:true},vector.release as CommunityRelease,controller.signal)).toContain('取消前已完成暂存')
  expect(native.invoke.mock.calls.some(call=>call[0]==='extension_install_confirm'||call[0]==='extension_enable')).toBe(false)
})
