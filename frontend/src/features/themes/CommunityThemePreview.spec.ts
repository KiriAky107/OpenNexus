// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
// Vitest disables CSS by default, including CSS raw imports. Load the real files here.
vi.mock('@/styles/features.css?raw', async () => ({ default: (await import('node:fs')).readFileSync(process.cwd() + '/src/styles/features.css', 'utf8') }))
vi.mock('@/styles/tokens.css?raw', async () => ({ default: (await import('node:fs')).readFileSync(process.cwd() + '/src/styles/tokens.css', 'utf8') }))
vi.mock('@/styles/callouts.css?raw', async () => ({ default: (await import('node:fs')).readFileSync(process.cwd() + '/src/styles/callouts.css', 'utf8') }))
import CommunityThemePreview from './CommunityThemePreview.vue'
vi.mock('@/styles/markdown-behavior.css?raw', async () => ({ default: (await import('node:fs')).readFileSync(process.cwd() + '/src/styles/markdown-behavior.css', 'utf8') }))
import { mockCommunityThemes } from '@/services/themePackageService'
const themes = [...['light','dark','sepia'].map(theme_id => ({theme_id,name:theme_id,builtin:true})), ...mockCommunityThemes.map(t => ({...t,builtin:false}))]
it.each(themes)('previews shared component states safely for $theme_id', theme => {
  const w = mount(CommunityThemePreview, {props:{themeId:theme.theme_id,name:theme.name,...(theme.builtin ? {css:''} : {})},attachTo:document.body})
  try {
    const iframe = w.get('iframe')
    expect(iframe.attributes('sandbox')).toBe('')
    const doc = new DOMParser().parseFromString(iframe.attributes('srcdoc')!, 'text/html')
    expect(doc.documentElement.dataset.theme).toBe(theme.theme_id)
    expect(doc.querySelector('script')).toBeNull()
    for (const selector of ['.editor-scroll-buttons button', '.markdown-content h6', '.task-list-item input:checked', '.markdown-content[data-code-wrap="true"][data-line-numbers="true"] .line', '.heading-fold-toggle']) expect(doc.querySelector(selector), selector).not.toBeNull()
    expect(doc.querySelector('style')!.textContent).toContain('.editor-scroll-buttons button:focus-visible')
    expect(doc.querySelector('style')!.textContent).toContain(".markdown-content[data-code-wrap='true'] .shiki code")
    expect(doc.querySelectorAll('.specimen-callouts > aside.markdown-callout')).toHaveLength(14)
    expect(doc.querySelector('.specimen-callouts > details[open]')).not.toBeNull()
    expect(doc.querySelector('.specimen-callouts > details:not([open])')).not.toBeNull()
    expect(doc.querySelector('style')!.textContent).toContain('.callout-title:focus-visible')
    expect(doc.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content')).toContain("default-src 'none'")
    for (const selector of ['input.input','input:disabled','textarea.textarea','select.select','.ui-disclosure[open]','.ui-disclosure:not([open])','.button-primary:disabled','.badge.success','.error-banner','.specimen-markdown code','.specimen-markdown table','.specimen-chart','.specimen-long']) expect(doc.querySelector(selector), selector).not.toBeNull()
    expect(doc.querySelector('style')!.textContent).toContain('.button-primary:hover')
    expect(doc.querySelector('style')!.textContent).not.toContain('color:white')
    const rules = Array.from(doc.styleSheets[0]!.cssRules) as CSSStyleRule[]
    // The sandbox cannot inherit MarkdownContent's component stylesheet.
    const codeRule = rules.find(rule => rule.selectorText === '.markdown-content .shiki code')!
    const lineRule = rules.find(rule => rule.selectorText === '.markdown-content .shiki .line')!
    expect(codeRule.style.getPropertyValue('display')).toBe('block')
    expect(lineRule.style.getPropertyValue('display')).toBe('block')
    expect(lineRule.style.getPropertyValue('min-height')).toBe('1lh')
    const rootRule = rules.filter(rule => rule.selectorText === 'html').pop()!
    const bodyRule = rules.filter(rule => rule.selectorText === 'body').pop()!
    // The embedded document must override the app-shell overflow lock.
    expect(rootRule.style.getPropertyValue('overflow-y')).toBe('auto')
    expect(rootRule.style.getPropertyPriority('overflow-y')).toBe('important')
    expect(bodyRule.style.getPropertyValue('height')).toBe('auto')
    expect(bodyRule.style.getPropertyValue('overflow')).toBe('visible')
  } finally { w.unmount() }
})
