// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderMermaid } from '@/services/mermaidService'
import { createMermaidPreview } from './mermaidPreview'

vi.mock('@/services/mermaidService', () => ({ renderMermaid: vi.fn() }))

it('renders SVG with the requested theme and keeps async revisions isolated', async () => {
  let finish!: (value: any) => void
  vi.mocked(renderMermaid).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  vi.mocked(renderMermaid).mockResolvedValueOnce({ svg: '<svg><text>new</text></svg>', warnings: [], width: 10, height: 10 })
  const oldPublish = vi.fn()
  const latestPublish = vi.fn()
  const old = createMermaidPreview('graph TD; A-->B', false, oldPublish)
  const latest = createMermaidPreview('graph TD; A-->C', true, latestPublish)
  document.body.append(latest.cloneNode(true))
  await flushPromises()
  finish({ svg: '<svg><text>old</text></svg>', warnings: [] })
  await flushPromises()
  expect(latest.querySelector('svg')?.textContent).toBe('new')
  expect(old.querySelector('svg')?.textContent).toBe('old')
  expect(oldPublish).not.toHaveBeenCalled()
  expect(latestPublish).toHaveBeenCalledWith(latest)
  expect(latestPublish.mock.calls[0]![0]).not.toBe(latest)
  document.getElementById(latest.id)?.remove()
  expect(renderMermaid).toHaveBeenLastCalledWith('graph TD; A-->C', { theme: 'dark' })
})

it('shows syntax errors as text without executing markup', async () => {
  vi.mocked(renderMermaid).mockResolvedValueOnce({ svg: '', warnings: ['<img src=x onerror=alert(1)>'], width: 0, height: 0 })
  const preview = createMermaidPreview('invalid', false, vi.fn())
  await flushPromises()
  expect(preview.classList.contains('has-error')).toBe(true)
  expect(preview.querySelector('img')).toBeNull()
  expect(preview.textContent).toContain('点击编辑')
})
