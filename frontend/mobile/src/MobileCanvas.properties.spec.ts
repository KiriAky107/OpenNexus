// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import MobileCanvas from './MobileCanvas.vue'

const document = {
  nodes: [
    { id: 'group', type: 'group', x: 500, y: 0, width: 240, height: 160, label: 'Properties group before', background: 'assets/before.png', vendor: { opaque: ['组', false] } },
    { id: 'text', type: 'text', x: 0, y: 0, width: 160, height: 120, text: 'Properties node before', label: 'Original node label', color: '1', vendor: { opaque: ['正文', true, null] } },
    { id: 'file', type: 'file', x: 220, y: 0, width: 180, height: 120, file: 'reference-before.md', subpath: '#before', vendor: { keep: 17 } },
    { id: 'link', type: 'link', x: 0, y: 180, width: 160, height: 120, url: 'https://example.invalid/before', vendor: { keep: '<script>literal</script>' } },
  ],
  edges: [{ id: 'edge', fromNode: 'text', toNode: 'file', label: 'Properties edge original', fromSide: 'right', toSide: 'left', fromEnd: 'none', toEnd: 'arrow', color: '2', vendor: { opaque: ['连接', 19] } }],
  'vendor-root': { original: ['原始扩展', true, null] },
}
const original = JSON.stringify(document, null, 2).replace(/\n/g, '\r\n') + '\r\n'
type Field = { name: string; key: string; value: string; select?: boolean }
const cases: { target: string; edge?: boolean; fields: Field[] }[] = [
  { target: 'text', fields: [
    { name: 'Label', key: 'label', value: 'Node label updated 😀' },
    { name: 'Node color', key: 'color', value: '4', select: true },
  ] },
  { target: 'group', fields: [
    { name: 'Node content', key: 'label', value: 'Group label updated 中文' },
    { name: 'Background image path', key: 'background', value: 'assets/background-updated.png' },
  ] },
  { target: 'link', fields: [
    { name: 'Node content', key: 'url', value: 'https://example.invalid/updated?q=%25_%E4%B8%AD' },
  ] },
  { target: 'file', fields: [
    { name: 'Node content', key: 'file', value: 'references/updated-中文.md' },
    { name: 'Heading or block subpath', key: 'subpath', value: '#Heading' },
  ] },
  { target: 'edge', edge: true, fields: [
    { name: 'Source node', key: 'fromNode', value: 'group', select: true },
    { name: 'Destination node', key: 'toNode', value: 'link', select: true },
    { name: 'From side', key: 'fromSide', value: 'bottom', select: true },
    { name: 'To side', key: 'toSide', value: 'top', select: true },
    { name: 'From arrow', key: 'fromEnd', value: 'arrow', select: true },
    { name: 'To arrow', key: 'toEnd', value: 'none', select: true },
    { name: 'Color', key: 'color', value: '6', select: true },
  ] },
]

it.each(cases)('edits only the reviewed $target properties through the shared form and undoes the whole form', async ({ target, edge, fields }) => {
  const wrapper = mount(MobileCanvas, { props: { modelValue: original, language: 'en' } })
  const button = (label: string) => {
    const found = wrapper.findAll('button').find(item => item.text() === label)
    if (!found) throw Error(`Missing button ${label}`)
    return found
  }
  try {
    await wrapper.get('[data-node="text"]').trigger('keydown.enter')
    if (edge) await button('Properties edge original').trigger('click')
    else await wrapper.get(`[data-list-node-id="${target}"]`).trigger('click')
    expect(wrapper.vm.source()).toBe(original)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()

    const expected = JSON.parse(original)
    const edited = (edge ? expected.edges : expected.nodes).find((item: { id: string }) => item.id === target)
    for (const [index, field] of fields.entries()) {
      const control = wrapper.get<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`.canvas-property-fields [aria-label="${field.name}"]`)
      expect(control.element.tagName).toBe(field.select ? 'SELECT' : field.name === 'Node content' ? 'TEXTAREA' : 'INPUT')
      await control.setValue(field.value)
      edited[field.key] = field.value
      expect(JSON.parse(wrapper.vm.source())).toEqual(expected)
      expect(wrapper.emitted('update:modelValue')).toHaveLength(index + 1)
      expect(wrapper.find('script').exists()).toBe(false)
      expect(wrapper.vm.source().replace(/\r\n/g, '')).not.toContain('\n')
    }

    const after = wrapper.vm.source(), changes = wrapper.emitted('update:modelValue')!.length
    await wrapper.setProps({ language: 'zh-CN' })
    expect(wrapper.vm.source()).toBe(after)
    expect(wrapper.emitted('update:modelValue')).toHaveLength(changes)
    await wrapper.setProps({ language: 'en' })
    await button('Canvas tools').trigger('click')
    await button('Undo').trigger('click')
    expect(wrapper.vm.source()).toBe(original)
    await button('Canvas tools').trigger('click')
    await button('Redo').trigger('click')
    expect(wrapper.vm.source()).toBe(after)
  } finally { wrapper.unmount() }
})
