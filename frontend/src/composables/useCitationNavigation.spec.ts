import { describe, expect, it, vi } from 'vitest'
import { navigateToCitation } from './useCitationNavigation'
import type { CitationNavigationDeps } from './useCitationNavigation'

function deps(overrides: Partial<CitationNavigationDeps> = {}) {
  const calls: string[] = []
  const base: CitationNavigationDeps = {
    loadFile: vi.fn(async () => { calls.push('loadFile') }),
    openFile: vi.fn(() => { calls.push('openFile') }),
    highlightBlock: vi.fn(() => { calls.push('highlightBlock') }),
    navigate: vi.fn(async () => { calls.push('navigate') }),
  }
  return { deps: { ...base, ...overrides }, calls }
}

describe('navigateToCitation', () => {
  it('先加载文件再高亮，最后跳转到工作区', async () => {
    // 顺序不能改：editor store 的 loadFile 末尾会把 highlightBlockId 清空
    // （stores/editor.ts），先 highlightBlock 会被自己冲掉。
    const { deps: d, calls } = deps()

    await navigateToCitation({ file_path: 'notes/a.md', block_id: 'blk-1' }, d)

    expect(calls).toEqual(['loadFile', 'openFile', 'highlightBlock', 'navigate'])
    expect(d.loadFile).toHaveBeenCalledWith('notes/a.md')
    expect(d.highlightBlock).toHaveBeenCalledWith('blk-1')
    expect(d.navigate).toHaveBeenCalledWith('/workspace')
  })

  it('等 loadFile 的 promise resolve 之后才高亮', async () => {
    let loaded = false
    const highlightBlock = vi.fn(() => {
      // loadFile 还没完成就高亮，说明少了 await
      expect(loaded).toBe(true)
    })
    const { deps: d } = deps({
      loadFile: vi.fn(async () => {
        await Promise.resolve()
        loaded = true
      }),
      highlightBlock,
    })

    await navigateToCitation({ file_path: 'notes/a.md', block_id: 'blk-1' }, d)

    expect(highlightBlock).toHaveBeenCalledTimes(1)
  })

  it('没有 block_id 时只打开文件，不调用高亮', async () => {
    const { deps: d, calls } = deps()

    await navigateToCitation({ file_path: 'notes/a.md' }, d)

    expect(calls).toEqual(['loadFile', 'openFile', 'navigate'])
    expect(d.highlightBlock).not.toHaveBeenCalled()
  })

  it('缺少 file_path 时抛出可展示的错误，且不做任何跳转', async () => {
    const { deps: d } = deps()

    await expect(navigateToCitation({ block_id: 'blk-1' }, d)).rejects.toThrow('该引用缺少文件路径，无法定位到笔记。')
    expect(d.loadFile).not.toHaveBeenCalled()
    expect(d.navigate).not.toHaveBeenCalled()
  })

  it('file_path 是空串或非字符串时同样拒绝', async () => {
    const { deps: d } = deps()

    await expect(navigateToCitation({ file_path: '   ' }, d)).rejects.toThrow(/缺少文件路径/)
    await expect(navigateToCitation({ file_path: 42 }, d)).rejects.toThrow(/缺少文件路径/)
    expect(d.loadFile).not.toHaveBeenCalled()
  })

  it('loadFile 失败时不跳转，避免把用户从未保存的编辑器里弹走', async () => {
    const { deps: d } = deps({
      loadFile: vi.fn(async () => { throw new Error('SAVE_CONFLICT: 当前文件有未解决的冲突') }),
    })

    await expect(navigateToCitation({ file_path: 'notes/a.md', block_id: 'b' }, d)).rejects.toThrow(/SAVE_CONFLICT/)
    expect(d.openFile).not.toHaveBeenCalled()
    expect(d.highlightBlock).not.toHaveBeenCalled()
    expect(d.navigate).not.toHaveBeenCalled()
  })
})
