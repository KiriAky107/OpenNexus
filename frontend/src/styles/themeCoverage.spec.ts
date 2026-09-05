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
