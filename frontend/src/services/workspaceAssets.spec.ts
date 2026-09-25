import { describe, expect, it } from 'vitest'
import { resolveWorkspaceAssetPath, workspaceAssetReference } from './workspaceService'

describe('workspace asset paths', () => {
  it('resolves ordinary vault images without requiring managed hash filenames', () => {
    expect(resolveWorkspaceAssetPath('/02-笔记表达/01.md', '../附件/opennexus-logo.png')).toBe('附件/opennexus-logo.png')
    expect(resolveWorkspaceAssetPath('/02-笔记表达/01.md', '/附件/logo.JPEG')).toBe('附件/logo.JPEG')
    expect(resolveWorkspaceAssetPath('/a.md', '.ainote/private.png')).toBeNull()
    expect(resolveWorkspaceAssetPath('/a.md', 'opennexus-records/private.png')).toBeNull()
    expect(resolveWorkspaceAssetPath('/a.md', '附件/script.svg')).toBeNull()
  })
  it('creates portable references relative to the note', () => {
    expect(workspaceAssetReference('/课程/系统/调度.md', 'attachments/ab/hash.png')).toBe('../../attachments/ab/hash.png')
    expect(resolveWorkspaceAssetPath('/课程/系统/调度.md', '../../attachments/ab/hash.png')).toBe('attachments/ab/hash.png')
  })

  it('does not resolve remote URLs or paths escaping the vault', () => {
    expect(resolveWorkspaceAssetPath('/a.md', 'https://example.com/image.png')).toBeNull()
    expect(resolveWorkspaceAssetPath('/a.md', '../attachments/ab/hash.png')).toBeNull()
  })
})
