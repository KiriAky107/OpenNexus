import { beforeEach, expect, it, vi } from 'vitest'
vi.mock('./workspaceService',()=>({ readFileContent:vi.fn(),saveFileContent:vi.fn(),refreshTree:vi.fn() }))
import * as workspace from './workspaceService'
import { executeReferenceChange, prepareReferenceChange, type ReferenceChangePlan } from './referenceChangeService'
const plan: ReferenceChangePlan = { oldPath:'/b.md',newPath:'/c.md',expectedHash:'h',tree:[],updates:[{path:'/a.md',destination:'/a.md',before:'[b](b.md)',after:'[b](c.md)',count:1}],pending:[],affected:[] }
beforeEach(()=>vi.resetAllMocks())
it('rejects a changed reference source before moving the target',async()=>{
  vi.mocked(workspace.readFileContent).mockResolvedValue('human edit')
  const mutate=vi.fn()
  await expect(executeReferenceChange(plan,true,mutate,()=>{})).rejects.toThrow('引用来源已改变')
  expect(mutate).not.toHaveBeenCalled();expect(workspace.saveFileContent).not.toHaveBeenCalled()
})
it('reports write conflicts after a move without overwriting the concurrent edit',async()=>{
  vi.mocked(workspace.readFileContent).mockResolvedValue(plan.updates[0]!.before)
  vi.mocked(workspace.saveFileContent).mockRejectedValue(new Error('REVISION_CONFLICT'))
  const mutate=vi.fn().mockResolvedValue(undefined)
  const result=await executeReferenceChange(plan,true,mutate,()=>{})
  expect(mutate).toHaveBeenCalledWith('h')
  expect(workspace.saveFileContent).toHaveBeenCalledWith('/a.md','[b](c.md)','[b](b.md)')
  expect(result.changed).toEqual([]);expect(result.failures[0]?.error).toContain('REVISION_CONFLICT')
})
it('checks scope before writing and can keep reference text unchanged',async()=>{
  const mutate=vi.fn().mockResolvedValue(undefined)
  await expect(executeReferenceChange(plan,true,mutate,()=>{throw new Error('vault changed')})).rejects.toThrow('vault changed')
  await executeReferenceChange(plan,false,mutate,()=>{})
  expect(workspace.saveFileContent).not.toHaveBeenCalled()
})
it('reviews and applies a multi-file folder move while preserving unrelated Markdown bytes', async () => {
  const first = '# 中文\r\n\r\n[外部](../outside.md) [同目录](b.md)\r\n``[示例](b.md)``\r\n'
  const outside = '[来源](course/a.md)\r\n[第二篇](course/b.md)\r\n'
  const contents = new Map([['/course/a.md', first], ['/course/b.md', '# B'], ['/outside.md', outside]])
  const file = (path: string) => ({ id: path, name: path.split('/').at(-1)!, path, type: 'file' as const, content_hash: 'revision-' + path })
  vi.mocked(workspace.refreshTree).mockResolvedValue([{ id: 'folder', name: 'course', path: '/course', type: 'folder', children: [file('/course/a.md'), file('/course/b.md')] }, file('/outside.md')])
  vi.mocked(workspace.readFileContent).mockImplementation(async path => contents.get(path)!)
  const reviewed = await prepareReferenceChange('/course', '/nested/new')
  expect(reviewed.updates).toHaveLength(2)
  expect(reviewed.pending).toEqual([])
  expect(reviewed.updates[0]).toMatchObject({ destination: '/nested/new/a.md', after: first.replace('../outside.md', '../../outside.md') })
  expect(reviewed.updates[1]).toMatchObject({ destination: '/outside.md', after: outside.replaceAll('course/', 'nested/new/') })
  expect(reviewed.updates.every(update => update.edits?.length)).toBe(true)
  const moved = vi.fn().mockResolvedValue(undefined)
  const result = await executeReferenceChange(reviewed, true, moved, () => {})
  expect(result.changed).toEqual(['/nested/new/a.md', '/outside.md'])
  expect(result.failures).toEqual([])
  expect(workspace.saveFileContent).toHaveBeenCalledWith('/nested/new/a.md', first.replace('../outside.md', '../../outside.md'), first)
})
