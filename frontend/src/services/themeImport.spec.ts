// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { strToU8, zipSync } from 'fflate'
import { decodeThemePackage, fetchThemePackage, inspectThemePackage, MAX_THEME_BYTES } from './themePackageService'
import paper from '@/assets/themes/paper-moments.theme?raw'

afterEach(() => vi.unstubAllGlobals())
it('reads ZIP manifests under repository folders and validates the bundled CSS', async () => {
  const [yaml, css] = paper.split('\n---\n')
  const zip = zipSync({ 'repo-main/theme.yaml': strToU8(yaml!), 'repo-main/theme.css': strToU8(css!) })
  const result = await inspectThemePackage(await decodeThemePackage(zip))
  expect(result.compatible).toBe(true)
  expect(result.css).toBe(css!.trim())
})
it('accepts a zipped single-file theme', async () => {
  expect(await decodeThemePackage(zipSync({ 'paper.theme': strToU8(paper) }))).toBe(paper)
})
it('rejects unsafe paths, ambiguous manifests and oversized input', async () => {
  await expect(decodeThemePackage(zipSync({ '../paper.theme': strToU8(paper) }))).rejects.toThrow('非法')
  await expect(decodeThemePackage(zipSync({ 'theme.yaml': strToU8(paper), 'manifest.yml': strToU8(paper) }))).rejects.toThrow('多个')
  await expect(decodeThemePackage(new Uint8Array(MAX_THEME_BYTES + 1))).rejects.toThrow('5 MB')
})
it('uses the same ZIP parser for URL downloads without sending credentials', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(zipSync({ 'paper.theme': strToU8(paper) })))
  vi.stubGlobal('fetch', fetcher)
  expect(await fetchThemePackage('https://example.com/theme.zip')).toBe(paper)
  expect(fetcher).toHaveBeenCalledWith('https://example.com/theme.zip', expect.objectContaining({ credentials: 'omit' }))
  await expect(fetchThemePackage('file:///theme.zip')).rejects.toThrow('HTTP(S)')
})
it('reports HTTP and streaming size failures', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('', { status: 404 })).mockResolvedValueOnce(new Response(new Uint8Array(MAX_THEME_BYTES + 1))))
  await expect(fetchThemePackage('https://example.com/theme')).rejects.toThrow('404')
  await expect(fetchThemePackage('https://example.com/theme')).rejects.toThrow('5 MB')
})
