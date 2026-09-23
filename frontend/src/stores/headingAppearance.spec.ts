// @vitest-environment happy-dom
import { beforeEach, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { normalizeHeadingAppearance, useHeadingAppearanceStore } from './headingAppearance'
beforeEach(() => { localStorage.clear(); setActivePinia(createPinia()) })
it('persists heading preferences and restores theme defaults without residual overrides', async () => {
  const store = useHeadingAppearanceStore()
  expect(store.cssVariables).toEqual({})
  store.preferences.custom = true
  store.preferences.levels[1]!.size = 35
  store.preferences.levels[1]!.weight = 400
  await nextTick()
  setActivePinia(createPinia())
  const restored = useHeadingAppearanceStore()
  expect(restored.cssVariables['--heading-2-size']).toBe('35px')
  expect(restored.cssVariables['--heading-2-weight']).toBe('400')
  restored.reset()
  expect(restored.cssVariables).toEqual({})
})
it('rejects invalid storage and limits values before applying CSS', () => {
  localStorage.setItem('editor-heading-appearance', 'invalid')
  expect(useHeadingAppearanceStore().preferences.custom).toBe(false)
  const result = normalizeHeadingAppearance({ custom: true, family: 'url(unsafe)', levels: [{ size: 9999, weight: 2 }, { size: NaN }] })
  expect(result.family).toBe('inherit')
  expect(result.levels[0]).toEqual({ size: 72, weight: 700 })
  expect(result.levels[1]!.size).toBe(28)
})

it('defaults to centered H1 and markers without replacing theme typography', async () => {
  const store = useHeadingAppearanceStore()
  expect(store.preferences.centerTitle).toBe(true)
  expect(store.preferences.markers).toBe(true)
  store.preferences.centerTitle = false
  store.preferences.markers = false
  await nextTick()
  expect(store.cssVariables).toEqual({ '--heading-title-align': 'left', '--heading-marker-display': 'none' })
  setActivePinia(createPinia())
  expect(useHeadingAppearanceStore().preferences.markers).toBe(false)
  store.reset()
  expect(store.cssVariables).toEqual({})
})
