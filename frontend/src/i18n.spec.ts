// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import PrimarySidebar from '@/components/common/PrimarySidebar.vue'
import { appLocale, t } from '@/i18n'
import { useSettingsStore } from '@/stores/settings'

beforeEach(() => {
  localStorage.clear()
  appLocale.value = 'zh-CN'
  setActivePinia(createPinia())
})

afterEach(() => {
  appLocale.value = 'zh-CN'
  localStorage.clear()
})

describe('interface locale', () => {
  it('changes shared labels and the document language immediately', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'workspace', component: { template: '<div />' } }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(PrimarySidebar, { global: { plugins: [router] } })
    const settings = useSettingsStore()

    expect(wrapper.text()).toContain('工作区')
    settings.language = 'en'
    await nextTick()

    expect(t('工作区', 'Workspace')).toBe('Workspace')
    expect(wrapper.text()).toContain('Workspace')
    expect(document.documentElement.lang).toBe('en')
  })
})
