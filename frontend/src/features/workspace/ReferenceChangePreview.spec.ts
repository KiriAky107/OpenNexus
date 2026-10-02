// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import ReferenceChangePreview from './ReferenceChangePreview.vue'
import { rewritePathReferences } from '@/services/vaultReferences'

it('lets users page through every replacement and returns to earlier changes', async () => {
  const before = 'padding'.repeat(700) + Array.from({ length: 12 }, (_, index) => `[label-${index}](b.md)\n${'context'.repeat(80)}\n`).join('')
  const result = rewritePathReferences('/a.md', before, '/b.md', '/new.md', new Set(['/a.md', '/b.md']))
  const wrapper = mount(ReferenceChangePreview, { props: { item: { path: '/a.md', destination: '/a.md', before, after: result.content, count: 12, edits: result.edits } } })
  const shown = new Set<string>()
  for (let page = 0; page < 3; page++) {
    for (const excerpt of wrapper.findAll('.after')) {
      for (const match of excerpt.text().matchAll(/label-(\d+)\]\(new.md\)/g)) shown.add(match[1]!)
    }
    if (page < 2) await wrapper.findAll('button')[1]!.trigger('click')
  }
  expect(shown.size).toBe(12)
  expect(wrapper.findAll('button')[1]!.attributes('disabled')).toBeDefined()
  await wrapper.findAll('button')[0]!.trigger('click')
  expect(wrapper.get('[role="status"]').text()).toBe('2 / 3')
  wrapper.unmount()
})
