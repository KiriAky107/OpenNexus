import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { mobileAppearance } from './native'

export type BuiltinTheme = 'light' | 'dark' | 'sepia'
export type ThemeChoice = 'system' | BuiltinTheme
export const themeKey = 'opennexus.mobile.theme'
const valid = (value: unknown): value is ThemeChoice => ['system', 'light', 'dark', 'sepia'].includes(String(value))

function savedChoice(): ThemeChoice {
  try { const value = localStorage.getItem(themeKey); if (valid(value)) return value } catch { /* Keep the session choice usable. */ }
  return 'system'
}
function systemQuery() {
  try { return window.matchMedia?.('(prefers-color-scheme: dark)') } catch { return undefined }
}
function apply(theme: BuiltinTheme) {
  const root = document.documentElement
  root.dataset.theme = theme
  root.dataset.codeTheme = theme === 'dark' ? 'github-dark' : 'github-light'
  let meta = document.head.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
  if (!meta) { meta = document.createElement('meta'); meta.name = 'theme-color'; document.head.append(meta) }
  meta.content = getComputedStyle(root).getPropertyValue('--color-background-primary').trim()
}

/** Apply before mounting the first screen; use the same shared CSS values as desktop. */
export function initializeMobileTheme() {
  const choice = savedChoice()
  apply(choice === 'system' ? systemQuery()?.matches ? 'dark' : 'light' : choice)
}

export function useMobileTheme(nativeEnabled: Ref<boolean> = ref(false)) {
  const choice = ref<ThemeChoice>(savedChoice()), query = systemQuery()
  const darkSystem = ref(Boolean(query?.matches)), nativeError = ref(false)
  const resolved = computed<BuiltinTheme>(() => choice.value === 'system' ? darkSystem.value ? 'dark' : 'light' : choice.value)
  let disposed = false, running = false, revision = 0
  const changed = (event: MediaQueryListEvent) => { darkSystem.value = event.matches }
  query?.addEventListener?.('change', changed)
  watch(resolved, apply, { immediate: true, flush: 'sync' })
  watch(choice, value => { try { localStorage.setItem(themeKey, value) } catch { /* Session choice still applies. */ } }, { flush: 'sync' })

  // Serialize native changes so an earlier slow reply cannot restore an obsolete system-bar theme.
  async function syncNative() {
    if (!nativeEnabled.value || disposed) return
    revision++
    if (running) return
    running = true
    try {
      let completed = 0
      while (!disposed && nativeEnabled.value && completed !== revision) {
        const current = revision, theme = resolved.value
        const background = getComputedStyle(document.documentElement).getPropertyValue('--color-background-primary').trim()
        try { await mobileAppearance(theme, background); if (!disposed && current === revision) nativeError.value = false }
        catch { if (!disposed && current === revision) nativeError.value = true }
        completed = current
      }
    } finally { running = false }
  }
  watch([resolved, nativeEnabled], () => { void syncNative() }, { immediate: true, flush: 'sync' })
  onBeforeUnmount(() => { disposed = true; query?.removeEventListener?.('change', changed) })
  return { choice, resolved, nativeError, retryNative: () => { void syncNative() } }
}
