import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

export interface MarkdownPreferences {
  heading: 'atx' | 'setext'; bullet: '-' | '*' | '+'; incrementList: boolean
  fence: '`' | '~'; math: boolean; callouts: boolean; diagrams: boolean; autoLinks: boolean
  lineNumbers: boolean; wrapCode: boolean; indent: number; defaultLanguage: string
}
export const defaultMarkdownPreferences: MarkdownPreferences = {
  heading: 'atx', bullet: '-', incrementList: true, fence: '`', math: true, callouts: true,
  diagrams: true, autoLinks: true, lineNumbers: true, wrapCode: false, indent: 4, defaultLanguage: '',
}
export function normalizeMarkdownPreferences(value: unknown): MarkdownPreferences {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const result = { ...defaultMarkdownPreferences }
  for (const key of ['incrementList','math','callouts','diagrams','autoLinks','lineNumbers','wrapCode'] as const) if (typeof raw[key] === 'boolean') result[key] = raw[key]
  result.heading = raw.heading === 'setext' ? 'setext' : 'atx'
  result.bullet = raw.bullet === '*' || raw.bullet === '+' ? raw.bullet : '-'
  result.fence = raw.fence === '~' ? '~' : '`'
  result.indent = [2,4,8].includes(Number(raw.indent)) ? Number(raw.indent) : 4
  result.defaultLanguage = typeof raw.defaultLanguage === 'string' && /^[\w+-]{0,40}$/.test(raw.defaultLanguage) ? raw.defaultLanguage : ''
  return result
}
export const markdownPresets = {
  extended: defaultMarkdownPreferences,
  github: { ...defaultMarkdownPreferences, math: false },
  plain: { ...defaultMarkdownPreferences, math: false, callouts: false, diagrams: false, autoLinks: false },
}
const key = 'markdown-preferences'
export const useMarkdownPreferencesStore = defineStore('markdown-preferences', () => {
  let saved: Record<string, unknown> = {}
  try { saved = JSON.parse(localStorage.getItem(key) ?? '{}') ?? {} } catch { /* 默认值 */ }
  const preferences = ref(normalizeMarkdownPreferences(saved.preferences))
  const customPresets = ref<{ name: string; preferences: MarkdownPreferences }[]>(Array.isArray(saved.presets)
    ? saved.presets.filter(item => item && typeof item.name === 'string').slice(0, 20).map(item => ({ name: item.name.slice(0, 40), preferences: normalizeMarkdownPreferences(item.preferences) })) : [])
  const normalized = computed(() => normalizeMarkdownPreferences(preferences.value))
  watch([preferences, customPresets], () => localStorage.setItem(key, JSON.stringify({ preferences: normalized.value, presets: customPresets.value })), { deep: true })
  function apply(value: unknown) { preferences.value = normalizeMarkdownPreferences(value) }
  function savePreset(name: string) {
    name = name.trim().slice(0, 40)
    if (!name) return false
    const existing = customPresets.value.find(item => item.name === name)
    if (existing) existing.preferences = { ...normalized.value }
    else if (customPresets.value.length < 20) customPresets.value.push({ name, preferences: { ...normalized.value } })
    else return false
    return true
  }
  return { preferences, normalized, customPresets, apply, savePreset }
})
