// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { strToU8, zipSync } from 'fflate'
import { decodeThemePackage, fetchThemePackage, inspectThemePackage, installTheme, MAX_THEME_BYTES, THEME_APP_VERSION } from './themePackageService'
import paper from '@/assets/themes/paper-moments.theme?raw'

afterEach(() => vi.unstubAllGlobals())
it('uses the desktop release version for compatibility checks', () => {
  expect(THEME_APP_VERSION).toBe('0.5.4-alpha')
})
it.each(['999.0.0', 'bad', '0.2'])('rejects unsupported minimum app version %s at inspection and install', async version => {
  const source = paper.replace(/min_app_version:.*\r?\n/, `min_app_version: ${version}\n`)
  expect((await inspectThemePackage(source)).compatible).toBe(false)
  const { manifest, css } = await inspectThemePackage(paper)
  await expect(installTheme({ ...manifest, min_app_version: version }, css)).rejects.toThrow()
})
it('accepts the current version and preserves real YAML list metadata', async () => {
  const result = await inspectThemePackage(paper.replace(/min_app_version:.*\r?\n/, `min_app_version: ${THEME_APP_VERSION}\ntags: [paper, "a,b"]\n`))
  expect(result.compatible).toBe(true)
  expect(Array.isArray(result.manifest.tags)).toBe(true)
})
it('reads ZIP manifests under repository folders and validates the bundled CSS', async () => {
  const [yaml, css] = paper.split(/\r?\n---\r?\n/)
  const zip = zipSync({ 'repo-main/theme.yaml': strToU8(yaml!), 'repo-main/theme.css': strToU8(css!) })
  const result = await inspectThemePackage(await decodeThemePackage(zip))
  expect(result.compatible).toBe(true)
  expect(result.css).toBe(css!.replace(/\r\n/g, '\n').trim())
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
