import { beforeEach, describe, expect, it, vi } from 'vitest'
const native = vi.hoisted(() => ({ enabled: false, invoke: vi.fn() }))
vi.mock('@tauri-apps/api/core', () => ({ isTauri: () => native.enabled, invoke: native.invoke }))
import { contentHash, DesktopError, hostInvoke, nativeTree } from './desktop'
import * as workspace from '../workspaceService'

beforeEach(() => { native.enabled = false; native.invoke.mockReset() })

describe('原生 Workspace 适配', () => {
  it('Web 不伪造原生能力', async () => {
    await expect(hostInvoke('workspace_tree')).rejects.toThrow('DESKTOP_UNAVAILABLE')
    expect(native.invoke).not.toHaveBeenCalled()
  })
  it('分层目录保留稳定文件身份', () => {
    const tree = nativeTree([{ file_id: 'stable', path: '中文/笔记.md', hash: 'h', revision: 2, deleted: false }])
    expect(tree[0]?.children?.[0]).toMatchObject({ id: 'stable', note_id: 'stable', path: '/中文/笔记.md' })
  })
  it('保存使用原始内存基线摘要且不调用 HTTP', async () => {
    native.enabled = true
    native.invoke.mockResolvedValue({})
    await workspace.saveFileContent('/中文.md', 'new', 'old')
    expect(native.invoke).toHaveBeenCalledWith('workspace_write', { path: '中文.md', content: 'new', expected: await contentHash('old') })
    await expect(workspace.saveFileContent('/中文.md', 'new')).rejects.toThrow('EXPECTED_REVISION_REQUIRED')
  })
  it('冲突保留结构化错误，不变成保存成功', async () => {
    native.enabled = true; native.invoke.mockRejectedValue('REVISION_CONFLICT')
    await expect(hostInvoke('workspace_write')).rejects.toBeInstanceOf(DesktopError)
  })
  it('取消原生目录选择不进入空 Vault', async () => {
    native.enabled = true; native.invoke.mockResolvedValue(null)
    await expect(workspace.openVault('ignored')).rejects.toThrow()
  })
})
