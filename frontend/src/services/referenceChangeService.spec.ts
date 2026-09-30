import { beforeEach, expect, it, vi } from 'vitest'
vi.mock('./workspaceService',()=>({ readFileContent:vi.fn(),saveFileContent:vi.fn(),refreshTree:vi.fn() }))
import * as workspace from './workspaceService'
import { executeReferenceChange, type ReferenceChangePlan } from './referenceChangeService'
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
