import { ref, watch } from 'vue'

export type AppLocale = 'zh-CN' | 'en'

function storedLocale(): AppLocale {
  if (typeof localStorage === 'undefined') return 'zh-CN'
  try {
    const saved = JSON.parse(localStorage.getItem('app-settings') ?? '{}') as { language?: unknown }
    return saved.language === 'en' ? 'en' : 'zh-CN'
  } catch {
    return 'zh-CN'
  }
}

export const appLocale = ref<AppLocale>(storedLocale())

watch(appLocale, (value) => {
  if (typeof document !== 'undefined') document.documentElement.lang = value
}, { immediate: true })

/** Keep the Chinese source beside its English translation while the UI is migrated. */
export function t(zh: string, en: string): string {
  return appLocale.value === 'en' ? en : zh
}

export function localeTag(): string {
  return appLocale.value === 'en' ? 'en' : 'zh-CN'
}
