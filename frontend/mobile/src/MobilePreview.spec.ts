// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import MobilePreview from './MobilePreview.vue'
const host = vi.hoisted(() => ({ mobileRequest: vi.fn() }))
vi.mock('./native', () => host)
let wrapper: VueWrapper | undefined
const props = { content: '', path: 'notes/a.md', label: 'Preview', imageUnavailable: 'Unavailable:', imageLimit: 'Limit' }
const createURL = vi.fn(() => 'blob:owned-image'), revokeURL = vi.fn()
beforeEach(() => {
  vi.clearAllMocks(); host.mobileRequest.mockResolvedValue({ content_base64: 'AQIDBA==' })
  vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL: createURL, revokeObjectURL: revokeURL }))
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.unstubAllGlobals() })

it('reads only safe local images, reuses original object URLs and preserves them across language changes', async () => {
  wrapper = mount(MobilePreview, { props: { ...props, content: '# 原文\n\n![one](../attachments/ab/hash.png)\n\n![same](../attachments/ab/hash.png)\n\n![remote](https://remote/pixel)\n\n![private](../.ainote/host.sqlite3)\n\n[link](../中文.md)' }, attachTo: document.body })
  await flushPromises()
  expect(host.mobileRequest.mock.calls.map(([r])=>r)).toEqual([{ kind:'read_image',path:'attachments/ab/hash.png' }])
  expect(wrapper.findAll('img')).toHaveLength(2);expect(createURL).toHaveBeenCalledTimes(1)
  expect(wrapper.get('h1').text()).toBe('原文')
  await wrapper.setProps({ label:'阅读预览',imageUnavailable:'图片未加载：',imageLimit:'上限' });await flushPromises()
  expect(host.mobileRequest).toHaveBeenCalledTimes(1);expect(wrapper.text()).toContain('图片未加载：')
  await wrapper.get('a').trigger('click');expect(wrapper.emitted('link')).toEqual([['../中文.md']])
  wrapper.unmount();wrapper=undefined;expect(revokeURL).toHaveBeenCalledWith('blob:owned-image')
})

it('ignores an old native image reply after the preview switched to another note', async () => {
  let finish!: (value: { content_base64:string }) => void
  host.mobileRequest.mockReturnValue(new Promise(yes=>{ finish=yes }))
  wrapper=mount(MobilePreview,{props:{...props,content:'![old](../attachments/ab/old.png)'},attachTo:document.body})
  await flushPromises();await wrapper.setProps({path:'other/b.md',content:'# Current note'});await flushPromises()
  finish({content_base64:'AQIDBA=='});await flushPromises()
  expect(wrapper.get('h1').text()).toBe('Current note');expect(wrapper.find('img').exists()).toBe(false);expect(createURL).not.toHaveBeenCalled()
})
