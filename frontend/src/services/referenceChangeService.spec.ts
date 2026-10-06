// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
vi.mock('./workspaceService',()=>({ readFileContent:vi.fn(),saveFileContent:vi.fn(),refreshTree:vi.fn() }))
import * as workspace from './workspaceService'
import { executeReferenceChange, prepareReferenceChange, type ReferenceChangePlan } from './referenceChangeService'
import { resolveNoteLink } from './markdownLinkService'
import * as desktop from './platform/desktop'
import linkFixture from './fixtures/experiment-links-v1.json'
const plan: ReferenceChangePlan = { oldPath:'/b.md',newPath:'/c.md',expectedHash:'h',tree:[],updates:[{path:'/a.md',destination:'/a.md',before:'[b](b.md)',after:'[b](c.md)',count:1}],pending:[],affected:[] }
beforeEach(()=>vi.resetAllMocks())
afterEach(()=>vi.restoreAllMocks())
it('keeps the shared experiment links correct through file, case, folder and referring-note moves', async () => {
  vi.spyOn(desktop, 'isDesktop').mockReturnValue(true)
  const contents = new Map(linkFixture.files.map(file => [`/${file.path}`, file.content]))
  vi.mocked(workspace.refreshTree).mockImplementation(async () => desktop.nativeTree(await Promise.all([...contents].map(async ([path, content]) => ({
    file_id: path, path: path.slice(1), hash: await desktop.contentHash(content), revision: 1, deleted: false,
  })))))
  vi.mocked(workspace.readFileContent).mockImplementation(async path => {
    if (!contents.has(path)) throw new Error('FILE_NOT_FOUND')
    return contents.get(path)!
  })
  vi.mocked(workspace.saveFileContent).mockImplementation(async (path, after, before) => {
    expect(contents.get(path)).toBe(before)
    contents.set(path, after)
  })
  for (const step of linkFixture.steps) {
    const oldPath = `/${step.old_path}`, newPath = `/${step.new_path}`
    const review = await prepareReferenceChange(oldPath, newPath)
    expect(review.pending).toEqual([])
    if (step.kind === 'folder') expect(Object.keys(review.expectedEntries!)).toHaveLength(3)
    const result = await executeReferenceChange(review, true, async expected => {
      expect(expected).toBe(review.expectedHash)
      for (const [path, content] of [...contents]) if (path === oldPath || path.startsWith(oldPath + '/')) {
        contents.delete(path); contents.set(newPath + path.slice(oldPath.length), content)
      }
    }, () => {})
    expect(result.pending).toEqual([]); expect(result.failures).toEqual([])
    const note = contents.get(`/${step.note_path}`)!
    expect(note).toBe(step.note_content)
    const links = [...note.matchAll(/^\[[^\]]+\]\(([^)]+)\)/gm)]
    expect(links).toHaveLength(4)
    links.forEach((link, index) => expect(resolveNoteLink(link[1]!, `/${step.note_path}`)).toEqual({ path: `/${step.linked_paths[index]}`, fragment: '' }))
  }
})
it.each(['py', 'json', 'csv', 'md'])('reviews literal hash and percent characters in a %s filename and keeps the rewritten link navigable', async extension => {
  vi.spyOn(desktop, 'isDesktop').mockReturnValue(true)
  const oldPath = `/experiments/课程/旧 #%.${extension}`
  const newPath = `/experiments/课程/新 #%2F.${extension}`
  const before = `[实验文件](experiments/${encodeURIComponent('课程')}/${encodeURIComponent(`旧 #%.${extension}`)})\r\n`
  const file = (path: string) => ({ id: path, name: path.split('/').at(-1)!, path, type: 'file' as const, content_hash: 'revision-' + path })
  vi.mocked(workspace.refreshTree).mockResolvedValue([file(oldPath), file('/索引.md')])
  vi.mocked(workspace.readFileContent).mockImplementation(async path => path === '/索引.md' ? before : 'source\r\n')
  const review = await prepareReferenceChange(oldPath, newPath)
  const update = review.updates.find(item => item.path === '/索引.md')!
  expect(review.pending).toEqual([])
  expect(update.count).toBe(1)
  expect(update.after.endsWith('\r\n')).toBe(true)
  const href = update.after.match(/\]\(([^)]+)\)/)![1]!
  expect(resolveNoteLink(href, '/索引.md')).toEqual({ path: newPath, fragment: '' })
  const mutate = vi.fn().mockResolvedValue(undefined)
  const result = await executeReferenceChange(review, true, mutate, () => {})
  expect(result.changed).toContain('/索引.md')
  expect(result.failures).toEqual([])
  expect(workspace.saveFileContent).toHaveBeenCalledWith('/索引.md', update.after, before)
})
it.each(['/../outside.md', '/experiments/.private.py', '/experiments/CON.py', '/experiments/a?b.py', '/experiments/a\\b.py', 'experiments/a.py'])('rejects an unsafe raw destination %s', async destination => {
  await expect(prepareReferenceChange('/experiments/a.py', destination)).rejects.toThrow('INVALID_DESTINATION_PATH')
  expect(workspace.refreshTree).not.toHaveBeenCalled()
})
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
