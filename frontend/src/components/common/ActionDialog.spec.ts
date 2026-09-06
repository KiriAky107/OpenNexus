// @vitest-environment happy-dom
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import ActionDialog from './ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'

let wrapper: ReturnType<typeof mount>
afterEach(() => wrapper?.unmount())
function setup() {
  let api!: ReturnType<typeof useActionDialog>
  wrapper = mount(defineComponent({
    components: { ActionDialog },
    setup() { api = useActionDialog(); return api },
    template: '<ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />',
  }), { attachTo: document.body })
  return api
}
it('requires explicit confirmation and treats Escape as cancellation', async () => {
  const api = setup()
  const action = vi.fn()
  const result = api.askConfirm('删除所有配置？').then(ok => { if (ok) action() })
  await flushPromises()
  expect(document.activeElement?.textContent).toBe('取消')
  await wrapper.get('dialog').trigger('cancel')
  await result
  expect(action).not.toHaveBeenCalled()
  const confirmed = api.askConfirm('继续？')
  await flushPromises()
  await wrapper.get('form').trigger('submit')
  expect(await confirmed).toBe(true)
})
it('preserves the default input and distinguishes empty submission from cancel', async () => {
  const api = setup()
  const input = api.askPrompt('新名称', '旧名称')
  await flushPromises()
  expect((wrapper.get('input').element as HTMLInputElement).value).toBe('旧名称')
  await wrapper.get('input').setValue('')
  await wrapper.get('form').trigger('submit')
  expect(await input).toBe('')
  const cancelled = api.askPrompt('名称')
  await flushPromises()
  await wrapper.get('button[type="button"]').trigger('click')
  expect(await cancelled).toBeNull()
})
it('cancels duplicate requests and pending operations when their view unmounts', async () => {
  const api = setup()
  const first = api.askConfirm('继续？')
  expect(await api.askConfirm('重复')).toBe(false)
  wrapper.unmount()
  expect(await first).toBe(false)
  expect(await api.askPrompt('已离开')).toBeNull()
})
