// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import mermaid from 'mermaid'
import { mermaidThemeVariables, renderMermaid } from './mermaidService'

vi.mock('mermaid', () => ({ default: { initialize: vi.fn(), render: vi.fn().mockResolvedValue({ svg: '<svg viewBox="0 0 10 10"></svg>' }) } }))
afterEach(() => { document.documentElement.removeAttribute('style'); vi.clearAllMocks() })
it('uses the current theme tokens for nodes, actors, text and lines', () => {
  document.documentElement.style.setProperty('--color-accent-soft', '#f3e1d8')
  document.documentElement.style.setProperty('--color-text-primary', '#493f35')
  const theme = mermaidThemeVariables(false)
  expect(theme.primaryColor).toBe('#f3e1d8')
  expect(theme.actorBkg).toBe('#f3e1d8')
  expect(theme.primaryTextColor).toBe('#493f35')
  expect(theme.actorTextColor).toBe('#493f35')
})
it('keeps explicit diagram styling and initializes base palette on each render', async () => {
  const source = 'graph TD; A-->B; style A fill:#f9f'
  await renderMermaid(source)
  expect(mermaid.initialize).toHaveBeenCalledWith(expect.objectContaining({ theme: 'base', securityLevel: 'strict', themeVariables: expect.any(Object) }))
  expect(mermaid.render).toHaveBeenCalledWith(expect.any(String), source)
})
