// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ExtensionInstallDialog from './ExtensionInstallDialog.vue'

let wrapper: VueWrapper
afterEach(() => { wrapper?.unmount() })

it.each(['Skill', 'Plugin'] as const)('installs %s from a trimmed directory and prevents duplicate submissions', async kind => {
  let complete!: () => void
  const install = vi.fn(() => new Promise<void>(resolve => { complete = resolve }))
  wrapper = mount(ExtensionInstallDialog, { props: { kind, install } })
  expect(wrapper.text()).toContain(`${kind.toLowerCase()}.yaml`)
  expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  await wrapper.get('input').setValue('  G:\\packages\\example  ')
  await wrapper.get('form').trigger('submit')
  await wrapper.get('form').trigger('submit')
  expect(install).toHaveBeenCalledExactlyOnceWith('G:\\packages\\example')
  await wrapper.get('dialog').trigger('cancel')
  expect(wrapper.emitted('close')).toBeUndefined()
  expect(wrapper.get('input').attributes('disabled')).toBeDefined()
  complete()
  await flushPromises()
  expect(wrapper.emitted('installed')).toHaveLength(1)
})

it('keeps the path and displays validation errors for retry', async () => {
  const install = vi.fn().mockRejectedValueOnce(new Error('Manifest does not exist')).mockResolvedValueOnce(undefined)
  wrapper = mount(ExtensionInstallDialog, { props: { kind: 'Plugin', install } })
  await wrapper.get('input').setValue('G:\\packages\\example')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toBe('Manifest does not exist')
  expect((wrapper.get('input').element as HTMLInputElement).value).toBe('G:\\packages\\example')
  expect(wrapper.emitted('installed')).toBeUndefined()
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.emitted('installed')).toHaveLength(1)
})
