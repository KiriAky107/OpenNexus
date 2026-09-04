// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from 'vitest'
import {
  inspectThemePackage,
  installTheme,
  listInstalledThemes,
  uninstallTheme,
} from './themePackageService'
import type { ThemeManifest } from '@/contracts'

const validYaml = `
theme_id: ocean-blue
name: 海洋蓝
version: 1.2.0
author: 测试作者
min_app_version: 0.1.0
css_entry: theme.css
is_dark: false
`

function manifest(overrides: Partial<ThemeManifest> = {}): ThemeManifest {
  return {
    theme_id: 'test-theme',
    name: '测试主题',
    version: '1.0.0',
    author: '作者',
    min_app_version: '0.1.0',
    is_dark: false,
    css_entry: 'theme.css',
    ...overrides,
  }
}

beforeEach(() => {
  localStorage.clear()
  document.head.querySelectorAll('style[id^="theme-style-"]').forEach((el) => el.remove())
})

describe('inspectThemePackage', () => {
  it('解析合法的 manifest 并标记为兼容', async () => {
    const result = await inspectThemePackage(validYaml)

    expect(result.compatible).toBe(true)
    expect(result.manifest.theme_id).toBe('ocean-blue')
    expect(result.manifest.name).toBe('海洋蓝')
    expect(result.manifest.version).toBe('1.2.0')
    expect(result.error_code).toBeUndefined()
  })

  it('缺少必填字段时返回 THEME_MANIFEST_INVALID 而不是抛错', async () => {
    const result = await inspectThemePackage('theme_id: no-name\nversion: 1.0.0\n')

    expect(result.compatible).toBe(false)
    expect(result.error_code).toBe('THEME_MANIFEST_INVALID')
  })

  it('theme_id 含非法字符时判定不兼容', async () => {
    const result = await inspectThemePackage(validYaml.replace('ocean-blue', 'Ocean Blue!'))

    expect(result.compatible).toBe(false)
    expect(result.error_code).toBe('THEME_MANIFEST_INVALID')
  })

  it('css_entry 指向远程地址时判定为安全违规', async () => {
    const result = await inspectThemePackage(
      validYaml.replace('css_entry: theme.css', 'css_entry: https://evil.example.com/theme.css'),
    )

    expect(result.compatible).toBe(false)
    expect(result.error_code).toBe('THEME_SECURITY_VIOLATION')
  })
})

describe('installTheme 的 CSS 安全校验', () => {
  it('拒绝含 @import 的 CSS', async () => {
    await expect(
      installTheme(manifest(), '@import url("https://evil.example.com/x.css");'),
    ).rejects.toThrow(/THEME_SECURITY_VIOLATION/)
  })

  it('拒绝含 javascript: 的 CSS', async () => {
    await expect(
      installTheme(manifest(), '[data-theme="test-theme"] { background: url(javascript:alert(1)); }'),
    ).rejects.toThrow(/THEME_SECURITY_VIOLATION/)
  })

  it('拒绝含 expression() 的 CSS', async () => {
    await expect(
      installTheme(manifest(), '[data-theme="test-theme"] { width: expression(alert(1)); }'),
    ).rejects.toThrow(/THEME_SECURITY_VIOLATION/)
  })

  it('不安全的 CSS 不会被注入页面', async () => {
    await installTheme(manifest(), '@import "x.css";').catch(() => {})

    expect(document.getElementById('theme-style-test-theme')).toBeNull()
  })
})

describe('主题安装生命周期', () => {
  const safeCss = '[data-theme="test-theme"] { --color-accent-primary: #2f6feb; }'

  it('安装后可列出，且默认不启用', async () => {
    const installed = await installTheme(manifest(), safeCss)

    expect(installed.builtin).toBe(false)
    expect(installed.enabled).toBe(false)

    const themes = await listInstalledThemes()
    expect(themes.map((t) => t.theme_id)).toContain('test-theme')
  })

  it('安装会把 CSS 注入独立的 style 节点', async () => {
    await installTheme(manifest(), safeCss)

    const styleEl = document.getElementById('theme-style-test-theme')
    expect(styleEl?.textContent).toContain('--color-accent-primary')
  })

  it('重复安装同一 theme_id 只保留一份', async () => {
    await installTheme(manifest(), safeCss)
    await installTheme(manifest({ version: '2.0.0' }), safeCss)

    const themes = await listInstalledThemes()
    expect(themes.filter((t) => t.theme_id === 'test-theme')).toHaveLength(1)
    expect(themes.find((t) => t.theme_id === 'test-theme')?.version).toBe('2.0.0')
  })

  it('卸载会同时移除记录与注入的样式', async () => {
    await installTheme(manifest(), safeCss)
    await uninstallTheme('test-theme')

    const themes = await listInstalledThemes()
    expect(themes.map((t) => t.theme_id)).not.toContain('test-theme')
    expect(document.getElementById('theme-style-test-theme')).toBeNull()
  })
})
