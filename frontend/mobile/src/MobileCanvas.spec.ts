// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import MobileCanvas from './MobileCanvas.vue'
import { nextTick } from 'vue'

const original = '{\r\n"nodes":[{"id":"a","type":"text","x":20,"y":30,"width":300,"height":180,"text":"原文 <script>bad()</script> 😀","custom":{"keep":true}}],"edges":[],"extension":[1,2]}\r\n'
const button = (wrapper: ReturnType<typeof mount>, label: string) => { const found = wrapper.findAll('button').find(item => item.text() === label); if (!found) throw Error(`Missing button ${label}`); return found }
const setup = () => mount(MobileCanvas, { props: { modelValue: original, language: 'en' } })
async function pointer(wrapper: ReturnType<typeof mount>, selector: string, type: string, values: Record<string, number>) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  for (const [key, value] of Object.entries(values)) Object.defineProperty(event, key, { value })
  wrapper.get(selector).element.dispatchEvent(event); await nextTick()
}
async function select(wrapper: ReturnType<typeof mount>) {
  await pointer(wrapper, '[data-node="a"]', 'pointerdown', { pointerId: 1, button: 0, clientX: 25, clientY: 35 })
  await pointer(wrapper, '.canvas-viewport', 'pointerup', { pointerId: 1 })
}
it('keeps exact source and plain node text while language changes without document edits', async () => {
  const wrapper = setup()
  expect(wrapper.text()).toContain('<script>bad()</script>'); expect(wrapper.find('script').exists()).toBe(false)
  await wrapper.setProps({ language: 'zh-CN' })
  expect(wrapper.get('.canvas-toolbar').text()).toContain('文字')
  expect(wrapper.emitted('update:modelValue')).toBeUndefined(); expect(wrapper.vm.source()).toBe(original)
  wrapper.unmount()
})
it('previews a drag, then emits one document update only when the finger finishes', async () => {
  const wrapper = setup()
  await pointer(wrapper, '[data-node="a"]', 'pointerdown', { pointerId: 1, button: 0, clientX: 25, clientY: 35 })
  await pointer(wrapper, '.canvas-viewport', 'pointermove', { pointerId: 1, clientX: 75, clientY: 95 })
  expect(wrapper.emitted('update:modelValue')).toBeUndefined(); expect(wrapper.get('[data-node="a"]').attributes('style')).toContain('left: 70px')
  await pointer(wrapper, '.canvas-viewport', 'pointerup', { pointerId: 1 })
  const result = wrapper.emitted('update:modelValue')!
  expect(result).toHaveLength(1); expect(JSON.parse(result[0]![0] as string).nodes[0]).toMatchObject({ x: 70, y: 90, custom: { keep: true } })
  wrapper.vm.undo(); expect(wrapper.emitted('update:modelValue')!.at(-1)![0]).toBe(original)
  wrapper.unmount()
})
it('cancels a drag when read-only ownership changes and cannot commit a late pointer-up', async () => {
  const wrapper = setup()
  await pointer(wrapper, '[data-node="a"]', 'pointerdown', { pointerId: 1, button: 0, clientX: 25, clientY: 35 })
  await pointer(wrapper, '.canvas-viewport', 'pointermove', { pointerId: 1, clientX: 100, clientY: 95 })
  await wrapper.setProps({ readOnly: true })
  await pointer(wrapper, '.canvas-viewport', 'pointerup', { pointerId: 1 })
  expect(wrapper.emitted('update:modelValue')).toBeUndefined(); expect(wrapper.vm.source()).toBe(original)
  wrapper.unmount()
})
it('a second finger on another node owns zoom and cancels the pending node mutation', async () => {
  const wrapper = setup()
  await pointer(wrapper, '[data-node="a"]', 'pointerdown', { pointerId: 1, button: 0, clientX: 25, clientY: 35 })
  await pointer(wrapper, '.canvas-viewport', 'pointermove', { pointerId: 1, clientX: 80, clientY: 95 })
  await pointer(wrapper, '[data-node="a"]', 'pointerdown', { pointerId: 2, button: 0, clientX: 160, clientY: 95 })
  await pointer(wrapper, '.canvas-viewport', 'pointermove', { pointerId: 2, clientX: 220, clientY: 95 })
  await pointer(wrapper, '.canvas-viewport', 'pointerup', { pointerId: 2 })
  await pointer(wrapper, '.canvas-viewport', 'pointerup', { pointerId: 1 })
  expect(wrapper.emitted('update:modelValue')).toBeUndefined(); expect(wrapper.vm.source()).toBe(original)
  wrapper.unmount()
})
it('keeps composition input local until completion and coalesces later field input into one undo', async () => {
  const wrapper = setup(); await select(wrapper); await button(wrapper, 'Properties').trigger('click')
  const textarea = wrapper.get<HTMLTextAreaElement>('.canvas-properties textarea')
  await textarea.trigger('compositionstart'); textarea.element.value = '中文组合 😀'; await textarea.trigger('input')
  expect(wrapper.emitted('update:modelValue')).toBeUndefined(); expect(wrapper.emitted('compositionStart')).toHaveLength(1)
  await textarea.trigger('compositionend'); await flushPromises()
  expect(JSON.parse(wrapper.emitted('update:modelValue')!.at(-1)![0] as string).nodes[0].text).toBe('中文组合 😀')
  await textarea.setValue('中文组合 😀 more'); wrapper.vm.undo()
  expect(wrapper.emitted('update:modelValue')!.at(-1)![0]).toBe(original)
  wrapper.unmount()
})
it('adds a real editable node, keeps exporter fields, and preserves its content when the language switches', async () => {
  const wrapper = setup(); await button(wrapper, 'Text').trigger('click')
  expect(wrapper.emitted('update:modelValue')).toHaveLength(1)
  const after = JSON.parse(wrapper.emitted('update:modelValue')![0]![0] as string)
  expect(after.nodes).toHaveLength(2); expect(after.nodes[1].type).toBe('text'); expect(after.extension).toEqual([1, 2])
  const source = wrapper.vm.source(); await wrapper.setProps({ language: 'zh-CN' }); expect(wrapper.vm.source()).toBe(source)
  wrapper.unmount()
})
it('rejects an unsafe file reference without changing the current document or closing its review', async () => {
  const wrapper = setup(); await button(wrapper, 'File or image').trigger('click')
  await wrapper.get('.canvas-dialog input').setValue('../private')
  await wrapper.get('.canvas-dialog form').trigger('submit')
  expect(wrapper.find('.canvas-dialog').exists()).toBe(true); expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  expect(wrapper.vm.source()).toBe(original)
  await wrapper.setProps({ language: 'zh-CN' }); expect(wrapper.get('.canvas-dialog [role=alert]').text()).toContain('不符合画布格式')
  wrapper.unmount()
})
it('does not replace a current document with a foreign acknowledgement', async () => {
  const wrapper = setup(); await wrapper.setProps({ modelValue: '{"nodes":[],"edges":[]}' })
  expect(wrapper.emitted('error')![0]).toEqual(['CANVAS_EDITOR_CONTENT_CHANGED']); expect(wrapper.vm.source()).toBe(original)
  expect(wrapper.emitted('update:modelValue')).toBeUndefined(); wrapper.unmount()
})

it('accepts only the frozen original image result and keeps one undo back to exact original JSON', async () => {
  const wrapper = setup(), document = JSON.parse(original)
  document.nodes.push({ id: 'original-image-node', type: 'file', x: 0, y: 0, width: 320, height: 240, file: 'attachments/original.png' })
  const after = JSON.stringify(document, null, 2) + '\n'
  expect(wrapper.vm.acceptImage('foreign', after)).toBe(false)
  expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  expect(wrapper.vm.acceptImage(original, after)).toBe(true)
  expect(wrapper.emitted('update:modelValue')!.at(-1)![0]).toBe(after)
  wrapper.vm.undo(); expect(wrapper.emitted('update:modelValue')!.at(-1)![0]).toBe(original)
  wrapper.unmount()
})

it('keeps a partially typed URL visible, blocks completion and cannot clear its warning by editing another field', async () => {
  const wrapper = setup(); await button(wrapper, 'Web link').trigger('click')
  await wrapper.get('.canvas-dialog input').setValue('https://example.org/'); await wrapper.get('.canvas-dialog form').trigger('submit')
  const inputs = wrapper.findAll<HTMLInputElement>('.canvas-properties input'), url = inputs[0]!, label = inputs[1]!
  await url.setValue('h')
  expect(url.element.value).toBe('h')
  await label.setValue('kept label')
  expect(wrapper.find('[role=alert]').exists()).toBe(true)
  expect(wrapper.emitted('validation')!.at(-1)).toEqual([true])
  await url.setValue('https://new.example/path')
  expect(wrapper.emitted('validation')!.at(-1)).toEqual([false])
  expect(JSON.parse(wrapper.vm.source()).nodes.at(-1).url).toBe('https://new.example/path')
  wrapper.unmount()
})

it('keeps an unapplied URL when keyboard selection targets another node', async () => {
  const wrapper = setup(); await button(wrapper, 'Web link').trigger('click')
  await wrapper.get('.canvas-dialog input').setValue('https://example.org/'); await wrapper.get('.canvas-dialog form').trigger('submit')
  const url = wrapper.get<HTMLInputElement>('.canvas-properties input'); await url.setValue('h')
  await wrapper.get('[data-node="a"]').trigger('keydown.enter')
  expect(wrapper.get<HTMLInputElement>('.canvas-properties input').element.value).toBe('h')
  expect(wrapper.emitted('validation')!.at(-1)).toEqual([true])
  expect(wrapper.get('.canvas-properties').text()).toContain('Web address')
  wrapper.unmount()
})

it('shows the committed drag position after a numeric property was edited', async () => {
  const wrapper = setup(); await select(wrapper); await button(wrapper, 'Properties').trigger('click')
  await wrapper.get<HTMLInputElement>('.numeric-fields input').setValue('80')
  await pointer(wrapper, '[data-node="a"]', 'pointerdown', { pointerId: 1, button: 0, clientX: 25, clientY: 35 })
  await pointer(wrapper, '.canvas-viewport', 'pointermove', { pointerId: 1, clientX: 75, clientY: 35 })
  await pointer(wrapper, '.canvas-viewport', 'pointerup', { pointerId: 1 })
  expect(JSON.parse(wrapper.vm.source()).nodes[0].x).toBe(130)
  expect(wrapper.get<HTMLInputElement>('.numeric-fields input').element.value).toBe('130')
  wrapper.unmount()
})

it('reviews a Canvas file using its original Vault-relative path', async () => {
  const wrapper = setup(); await button(wrapper, 'File or image').trigger('click')
  await wrapper.get('.canvas-dialog input').setValue('Notes/original.md'); await wrapper.get('.canvas-dialog form').trigger('submit')
  await button(wrapper, 'Review file or link').trigger('click')
  expect(wrapper.emitted('link')!.at(-1)).toEqual(['Notes/original.md'])
  wrapper.unmount()
})

it('cancels a pending touch on Android Back and ignores its later pointer-up', async () => {
  const wrapper = setup()
  await pointer(wrapper, '[data-node="a"]', 'pointerdown', { pointerId: 1, button: 0, clientX: 25, clientY: 35 })
  await pointer(wrapper, '.canvas-viewport', 'pointermove', { pointerId: 1, clientX: 100, clientY: 95 })
  expect(wrapper.vm.handleBack()).toBe(true)
  await pointer(wrapper, '.canvas-viewport', 'pointerup', { pointerId: 1 })
  expect(wrapper.emitted('update:modelValue')).toBeUndefined(); expect(wrapper.vm.source()).toBe(original)
  wrapper.unmount()
})

it('sends invalid JSON repair input to the recoverable draft boundary before leaving its panel', async () => {
  const wrapper = mount(MobileCanvas, { props: { modelValue: '{broken original', language: 'en' } })
  await button(wrapper, 'Review original JSON').trigger('click')
  await wrapper.get('.repair textarea').setValue('{still invalid 中文')
  expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['{still invalid 中文'])
  expect(wrapper.vm.handleBack()).toBe(true)
  await flushPromises(); expect(wrapper.find('.repair').exists()).toBe(false)
  wrapper.unmount()
})
