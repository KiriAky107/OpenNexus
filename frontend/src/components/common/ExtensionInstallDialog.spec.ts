// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ExtensionInstallDialog from './ExtensionInstallDialog.vue'

let wrapper: VueWrapper
afterEach(() => { wrapper?.unmount() })

it.each(['Skill', 'Plugin'] as const)('uploads a selected %s ZIP only on confirmation', async kind => {
  const install = vi.fn().mockResolvedValue(undefined)
  wrapper = mount(ExtensionInstallDialog, { props: { kind, install } })
  const file = new File(['zip fixture'], 'package.zip', {type:'application/zip'})
  Object.defineProperty(wrapper.get('input[type="file"]').element, 'files', {value:[file], configurable:true})
  await wrapper.get('input[type="file"]').trigger('change')
  expect(wrapper.text()).toContain('package.zip')
  expect(install).not.toHaveBeenCalled()
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(install).toHaveBeenCalledExactlyOnceWith(file)
  expect(wrapper.emitted('installed')).toHaveLength(1)
})

it('rejects oversized ZIP files before upload', async () => {
  const install = vi.fn()
  wrapper = mount(ExtensionInstallDialog, {props:{kind:'Skill',install}})
  const file = new File(['zip'], 'large.zip')
  Object.defineProperty(file, 'size', {value:10 * 1024 * 1024 + 1})
  Object.defineProperty(wrapper.get('input[type="file"]').element, 'files', {value:[file]})
  await wrapper.get('input[type="file"]').trigger('change')
  expect(wrapper.get('[role="alert"]').text()).toContain('10 MiB')
  await wrapper.get('form').trigger('submit')
  expect(install).not.toHaveBeenCalled()
})

it.each(['Skill', 'Plugin'] as const)('installs %s from a trimmed directory and prevents duplicate submissions', async kind => {
  let complete!: () => void
  const install = vi.fn(() => new Promise<void>(resolve => { complete = resolve }))
  wrapper = mount(ExtensionInstallDialog, { props: { kind, install } })
  expect(wrapper.text()).toContain(`${kind.toLowerCase()}.yaml`)
  expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  await wrapper.findAll('button').find(button => button.text() === '本地目录')!.trigger('click')
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
  await wrapper.findAll('button').find(button => button.text() === '本地目录')!.trigger('click')
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
