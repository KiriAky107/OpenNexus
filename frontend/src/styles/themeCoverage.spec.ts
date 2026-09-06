// @vitest-environment happy-dom
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { expect, it } from 'vitest'
import { getCommunityThemePreviewCss, mockCommunityThemes } from '@/services/themePackageService'
const root = join(process.cwd(), 'src')
const files = Object.fromEntries(readdirSync(root, { recursive: true }).map(String).filter(path => /\.(vue|css|ts|theme)$/.test(path)).map(path => [path, readFileSync(join(root, path), 'utf8')]))
it('resolves semantic style token references throughout the component source tree', () => {
  const defined = new Set<string>()
  const used = new Set<string>()
  for (const [path, source] of Object.entries(files)) {
    if (path.endsWith('.spec.ts')) continue
    for (const match of source.matchAll(/(--[\w-]+)\s*:/g)) defined.add(match[1]!)
    for (const match of source.matchAll(/var\((--(?:color|font|space|radius|shadow|motion|line)-[\w-]+)/g)) used.add(match[1]!)
  }
  expect([...used].filter(name => !defined.has(name))).toEqual([])
})
it.each(mockCommunityThemes)('provides interaction and Markdown colors in $theme_id', theme => {
  const css = getCommunityThemePreviewCss(theme.theme_id)
  for (const token of ['accent-primary-active', 'accent-soft-hover', 'border-focus', 'text-inverse', 'markdown-grid', 'markdown-marker', 'markdown-table-header']) {
    expect(css).toContain(`--color-${token}:`)
  }
  expect(css).toContain(`color-scheme: ${theme.is_dark ? 'dark' : 'light'}`)
})

const calloutThemes = ['light', 'dark', 'sepia', ...mockCommunityThemes.map(theme => theme.theme_id)]
const calloutTones = ['info', 'success', 'warning', 'danger', 'important', 'quote']
it.each(calloutThemes)('keeps callout headings readable against tinted surfaces in %s', themeId => {
  const doc = document.implementation.createHTMLDocument('theme contrast')
  const style = doc.createElement('style')
  style.textContent = files['styles/tokens.css'] ?? files['styles\\tokens.css']!
  style.textContent += getCommunityThemePreviewCss(themeId)
  doc.head.append(style)
  const values = new Map<string, string>()
  for (const rule of Array.from(doc.styleSheets[0]!.cssRules) as CSSStyleRule[]) {
    if (![':root', `[data-theme='${themeId}']`, `[data-theme="${themeId}"]`].includes(rule.selectorText)) continue
    for (const name of ['--color-surface-primary', ...calloutTones.map(tone => `--color-callout-${tone}`)]) {
      const value = rule.style.getPropertyValue(name).trim()
      if (value) values.set(name, value)
    }
  }
  const rgb = (hex: string) => [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255)
  const luminance = (color: number[]) => color.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index]!, 0)
  const surface = rgb(values.get('--color-surface-primary')!)
  for (const tone of calloutTones) {
    const hex = values.get(`--color-callout-${tone}`)!
    expect(hex).toMatch(/^#[\da-f]{6}$/i)
    const ink = rgb(hex)
    const background = surface.map((value, index) => value * .92 + ink[index]! * .08)
    const first = luminance(ink), second = luminance(background)
    expect((Math.max(first, second) + .05) / (Math.min(first, second) + .05), tone).toBeGreaterThanOrEqual(4.5)
  }
})

it('preserves warning and success colors inside the editor selector cascade', () => {
  const style = document.createElement('style')
  style.textContent = readFileSync(join(root, 'styles/callouts.css'), 'utf8')
  document.head.append(style)
  const host = document.createElement('div')
  host.className = 'milkdown'
  host.innerHTML = '<div class="ProseMirror"><blockquote class="markdown-callout" data-callout="warning"></blockquote><blockquote class="markdown-callout" data-callout="success"></blockquote></div>'
  document.body.append(host)
  try {
    const blocks = host.querySelectorAll('blockquote')
    expect(getComputedStyle(blocks[0]!).getPropertyValue('--callout-color')).toContain('--color-callout-warning')
    expect(getComputedStyle(blocks[1]!).getPropertyValue('--callout-color')).toContain('--color-callout-success')
  } finally { host.remove(); style.remove() }
})
