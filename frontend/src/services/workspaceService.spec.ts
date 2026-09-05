// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiErrorClass } from './apiClient'
import * as workspaceService from './workspaceService'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const workspaceSnapshot = {
  workspace: {
    vault_id: 'default',
    name: 'vault',
    path: 'C:\\data\\vault',
    file_count: 1,
    indexed_note_count: 1,
    requires_refresh: false,
  },
  items: [
    {
      entry_id: 'folder-course',
      name: '课程',
      path: '/课程',
      type: 'folder',
      note_id: null,
      children: [
        {
          entry_id: 'note-os',
          note_id: 'note-os',
          name: '操作系统.md',
          path: '/课程/操作系统.md',
          type: 'file',
          children: [],
        },
      ],
    },
  ],
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('workspaceService backend adapter', () => {
  it.each([
    ['tags:\n- python\n- rust', { tags: ['python', 'rust'] }],
    ['tags: []', { tags: [] }],
    ['tags:', { tags: [] }],
    ['tags: ["a,b", rust]', { tags: ['a,b', 'rust'] }],
    ['title: Demo', {}],
    ['tags: [broken', {}],
  ])('saves explicit metadata tags with the same Markdown snapshot: %s', async (yaml, tagPayload) => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async (input) => String(input) === '/api/workspace/open'
      ? jsonResponse(workspaceSnapshot) : jsonResponse({}))
    await workspaceService.openVault('C:\\data\\vault')
    const markdown = `---\n${yaml}\n---\n# Body\n`
    await workspaceService.saveFileContent('/课程/操作系统.md', markdown)
    const patchCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')
    expect(String(patchCall?.[0])).toBe('/api/notes/note-os')
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({ markdown, ...tagPayload })
  })

  it('opens the configured Vault and reads/saves Markdown through Note API', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/workspace/open') return jsonResponse(workspaceSnapshot)
      if (url === '/api/notes/note-os' && init?.method === 'GET') {
        return jsonResponse({
          note_id: 'note-os', title: '操作系统', file_path: '课程/操作系统.md', tags: [],
          created_at: '2026-08-31T00:00:00Z', updated_at: '2026-08-31T00:00:00Z',
          markdown: '# 操作系统\n', blocks: [],
        })
      }
      if (url === '/api/notes/note-os' && init?.method === 'PATCH') {
        return jsonResponse({})
      }
      throw new Error(`Unexpected request: ${init?.method} ${url}`)
    })

    const vault = await workspaceService.openVault('C:\\data\\vault')
    const tree = await workspaceService.getFileTree()
    const markdown = await workspaceService.readFileContent('/课程/操作系统.md')
    await workspaceService.saveFileContent('/课程/操作系统.md', '# 已更新\n')

    expect(vault).toEqual({ vault_id: 'default', path: 'C:\\data\\vault', name: 'vault' })
    expect(tree[0].children?.[0]).toMatchObject({
      id: 'note-os', note_id: 'note-os', path: '/课程/操作系统.md', type: 'file',
    })
    expect(markdown).toBe('# 操作系统\n')
    const patchCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({ markdown: '# 已更新\n' })
  })

  it('creates notes and folders with Vault-relative paths', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/workspace/open') return jsonResponse(workspaceSnapshot)
      if (url === '/api/notes' && init?.method === 'POST') {
        return jsonResponse({
          note_id: 'note-new', title: '新笔记', file_path: '课程/新笔记.md', tags: [],
          created_at: '2026-08-31T00:00:00Z', updated_at: '2026-08-31T00:00:00Z',
          markdown: '# 新笔记\n', blocks: [],
        })
      }
      if (url === '/api/workspace/folders' && init?.method === 'POST') {
        return jsonResponse({
          entry_id: 'folder-child', name: '子目录', path: '/课程/子目录', type: 'folder',
          note_id: null, children: [],
        })
      }
      throw new Error(`Unexpected request: ${init?.method} ${url}`)
    })
    await workspaceService.openVault('C:\\data\\vault')

    const note = await workspaceService.createFile('/课程', '新笔记.md', '# 新笔记\n')
    const folder = await workspaceService.createFolder('/课程', '子目录')

    expect(note).toMatchObject({ id: 'note-new', path: '/课程/新笔记.md' })
    expect(folder).toMatchObject({ id: 'folder-child', path: '/课程/子目录' })
    const bodies = fetchMock.mock.calls
      .filter(([, init]) => init?.method === 'POST')
      .map(([, init]) => JSON.parse(String(init?.body)))
    expect(bodies).toContainEqual({ title: '新笔记', folder: '课程', markdown: '# 新笔记\n' })
    expect(bodies).toContainEqual({ parent: '课程', name: '子目录' })
  })

  it('reports backend connectivity errors instead of falling back to Mock data', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('offline'))

    await expect(workspaceService.getWorkspaceInfo()).rejects.toEqual(
      expect.objectContaining<Partial<ApiErrorClass>>({ code: 'NETWORK_ERROR' }),
    )
  })
})
