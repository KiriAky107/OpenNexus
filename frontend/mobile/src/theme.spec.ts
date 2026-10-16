// @vitest-environment happy-dom
/// <reference types="node" />
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { initializeMobileTheme, themeKey, useMobileTheme } from './theme'

const native = vi.hoisted(() => ({ mobileAppearance: vi.fn() }))
vi.mock('./native', () => native)
let listener: ((event: MediaQueryListEvent) => void) | undefined
let query: { matches: boolean; addEventListener: ReturnType<typeof vi.fn>; removeEventListener: ReturnType<typeof vi.fn> }
let style: HTMLStyleElement
const wrappers: ReturnType<typeof mount>[] = []
function controller(enabled = ref(false)) {
  let theme!: ReturnType<typeof useMobileTheme>
  wrappers.push(mount(defineComponent({ setup() { theme = useMobileTheme(enabled); return () => h('div') } })))
  return theme
}
function systemDark(matches: boolean) { query.matches = matches; listener?.({ matches } as MediaQueryListEvent) }
beforeEach(() => {
  localStorage.clear(); document.documentElement.removeAttribute('data-theme')
  vi.clearAllMocks(); native.mobileAppearance.mockResolvedValue({ state: 'applied' })
  listener = undefined
  query = { matches: false, addEventListener: vi.fn((_, callback) => { listener = callback }), removeEventListener: vi.fn() }
  vi.stubGlobal('matchMedia', vi.fn(() => query))
  style = document.createElement('style')
  style.textContent = readFileSync(join(process.cwd(), 'src/styles/theme-tokens.css'), 'utf8')
  document.head.append(style)
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); style.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('applies the system theme before first mount and follows changes without replacing an explicit choice', () => {
  query.matches = true; initializeMobileTheme()
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.content).toBe('#0d1117')
  const theme = controller()
  systemDark(false); expect(theme.resolved.value).toBe('light')
  theme.choice.value = 'sepia'; systemDark(true)
  expect(theme.resolved.value).toBe('sepia'); expect(document.documentElement.dataset.theme).toBe('sepia')
  expect(localStorage.getItem(themeKey)).toBe('sepia')
  expect(native.mobileAppearance).not.toHaveBeenCalled()
})
it('restores only known built-in choices and never persists a system color as a fixed preference', () => {
  localStorage.setItem(themeKey, 'untrusted-custom-css'); query.matches = true
  const theme = controller(); expect(theme.choice.value).toBe('system'); expect(theme.resolved.value).toBe('dark')
  systemDark(false); expect(localStorage.getItem(themeKey)).toBe('untrusted-custom-css')
  theme.choice.value = 'dark'; wrappers.pop()!.unmount()
  const reopened = controller(); expect(reopened.choice.value).toBe('dark')
  expect(document.documentElement.dataset.codeTheme).toBe('github-dark')
})
it('keeps switching usable when preference storage is unavailable and removes the system listener', () => {
  const theme = controller()
  vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw Error('storage denied') })
  expect(() => { theme.choice.value = 'dark' }).not.toThrow()
  expect(document.documentElement.dataset.theme).toBe('dark')
  wrappers.pop()!.unmount(); expect(query.removeEventListener).toHaveBeenCalledWith('change', listener)
})
it('serializes native appearance updates and finishes on the newest selected theme', async () => {
  let finish!: () => void
  native.mobileAppearance.mockImplementationOnce(() => new Promise(resolve => { finish = () => resolve({ state: 'applied' }) }))
  const theme = controller(ref(true))
  theme.choice.value = 'dark'; theme.choice.value = 'sepia'
  expect(native.mobileAppearance).toHaveBeenCalledTimes(1)
  finish(); await flushPromises()
  expect(native.mobileAppearance.mock.calls).toEqual([['light', '#ffffff'], ['sepia', '#fbf3df']])
  expect(theme.nativeError.value).toBe(false)
})
it('retains the page theme after native rejection and supports an explicit retry', async () => {
  native.mobileAppearance.mockRejectedValueOnce(Error('MOBILE_APPEARANCE_FAILED'))
  const theme = controller(ref(true)); await flushPromises()
  expect(theme.nativeError.value).toBe(true); expect(document.documentElement.dataset.theme).toBe('light')
  theme.retryNative(); await flushPromises(); expect(theme.nativeError.value).toBe(false)
})
it('ignores a late native rejection after unmount', async () => {
  let reject!: (error: Error) => void
  native.mobileAppearance.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
  const theme = controller(ref(true)); wrappers.pop()!.unmount()
  reject(Error('late')); await flushPromises(); expect(theme.nativeError.value).toBe(false)
})

it('applies semantic surface, text and border colors to the collapsed selector and its options in every built-in theme', () => {
  const forms = document.createElement('style')
  forms.textContent = readFileSync(join(process.cwd(), 'mobile/src/style.css'), 'utf8').replace(/^@import[^;]+;/, '')
  document.head.append(forms)
  const label = document.createElement('label'); label.className = 'language'
  const select = document.createElement('select')
  select.innerHTML = '<option value="system">System</option><option value="dark">Dark</option>'
  label.append(select); document.body.append(label)
  const theme = controller()
  const rgb = (value: string) => value.startsWith('#') ? [1, 3, 5].map(offset => parseInt(value.slice(offset, offset + 2), 16)) : value.match(/\d+/g)!.map(Number)
  try {
    for (const choice of ['light', 'dark', 'sepia'] as const) {
      theme.choice.value = choice
      const tokens = getComputedStyle(document.documentElement), collapsed = getComputedStyle(select)
      expect(rgb(collapsed.backgroundColor)).toEqual(rgb(tokens.getPropertyValue('--color-surface-primary').trim()))
      expect(rgb(collapsed.color)).toEqual(rgb(tokens.getPropertyValue('--color-text-primary').trim()))
      expect(rgb(collapsed.borderTopColor)).toEqual(rgb(tokens.getPropertyValue('--color-border-default').trim()))
      expect(rgb(getComputedStyle(select.options[1]!).backgroundColor)).toEqual(rgb(tokens.getPropertyValue('--color-surface-elevated').trim()))
    }
  } finally { label.remove(); forms.remove() }
})
