import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import '@/styles/headings.css'

const key = 'editor-heading-appearance'
export const defaultHeadingSizes = [32, 28, 24, 21, 18, 16]
export function normalizeHeadingAppearance(value: unknown) {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const levels = Array.isArray(raw.levels) ? raw.levels : []
  return {
    custom: raw.custom === true,
    centerTitle: raw.centerTitle !== false,
    markers: raw.markers !== false,
    family: ['inherit', 'serif', 'sans-serif', 'monospace'].includes(String(raw.family)) ? String(raw.family) : 'inherit',
    levels: defaultHeadingSizes.map((size, index) => {
      const item = levels[index] ?? {}
      return {
        size: typeof item.size === 'number' && Number.isFinite(item.size) ? Math.min(72, Math.max(12, item.size)) : size,
        weight: [400, 500, 600, 700, 800].includes(item.weight) ? Number(item.weight) : 700,
      }
    }),
  }
}

export const useHeadingAppearanceStore = defineStore('heading-appearance', () => {
  let saved: unknown
  try { saved = JSON.parse(localStorage.getItem(key) ?? '{}') } catch { saved = {} }
  const preferences = ref(normalizeHeadingAppearance(saved))
  watch(preferences, value => localStorage.setItem(key, JSON.stringify(normalizeHeadingAppearance(value))), { deep: true })
  const cssVariables = computed(() => {
    const normalized = normalizeHeadingAppearance(preferences.value)
    const result: Record<string, string> = {}
    if (!normalized.centerTitle) result['--heading-title-align'] = 'left'
    if (!normalized.markers) result['--heading-marker-display'] = 'none'
    if (!normalized.custom) return result
    result['--heading-family'] = normalized.family
    normalized.levels.forEach((item, index) => {
      result[`--heading-${index + 1}-size`] = `${item.size}px`
      result[`--heading-${index + 1}-weight`] = String(item.weight)
    })
    return result
  })
  function reset() { preferences.value = normalizeHeadingAppearance({}) }
  return { preferences, cssVariables, reset }
})
