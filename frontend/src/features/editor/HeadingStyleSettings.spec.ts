// @vitest-environment happy-dom
import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import HeadingStyleSettings from './HeadingStyleSettings.vue'
it('previews individual heading settings and restores theme inheritance', async () => {
  localStorage.clear()
  const wrapper = mount(HeadingStyleSettings, { global: { plugins: [createPinia()] } })
  try {
    expect(wrapper.get('input[aria-label="H1 字号"]').attributes('disabled')).toBeDefined()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('input[aria-label="H1 字号"]').setValue(42)
    await wrapper.get('select[aria-label="H1 粗细"]').setValue('400')
    expect(wrapper.get('.heading-style-preview').attributes('style')).toContain('--heading-1-size: 42px')
    expect(wrapper.get('.heading-style-preview').attributes('style')).toContain('--heading-1-weight: 400')
    await wrapper.get('button').trigger('click')
    expect(wrapper.get('.heading-style-preview').attributes('data-heading-style')).toBeUndefined()
    expect(wrapper.get('.heading-style-preview').attributes('style') ?? '').not.toContain('--heading-1-size')
  } finally { wrapper.unmount() }
})
