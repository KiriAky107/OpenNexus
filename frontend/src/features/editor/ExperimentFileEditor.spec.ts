// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ExperimentFileEditor from './ExperimentFileEditor.vue'
import { useEditorStore } from '@/stores/editor'

let wrapper: VueWrapper | undefined

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.restoreAllMocks()
})

describe('experiment file editor', () => {
  it('previews CSV as bounded table and returns to the unchanged editable source', async () => {
    setActivePinia(createPinia())
    const editor = useEditorStore()
    editor.currentFilePath = '/experiments/input.csv'
    editor.content = 'name,value\n"A, B",3\n'
    editor.saveStatus = 'saved'
    const autosave = vi.spyOn(editor, 'scheduleAutoSave').mockImplementation(() => {})
    wrapper = mount(ExperimentFileEditor)

    await wrapper.get('button[aria-pressed="false"]').trigger('click')
    expect(wrapper.get('[role="region"]').text()).toContain('A, B')
    expect(wrapper.find('textarea').exists()).toBe(false)

    await wrapper.get('button[aria-pressed="true"]').trigger('click')
    await wrapper.get('textarea').setValue('name,value\n"A, B",4\n')
    expect(editor.content).toBe('name,value\n"A, B",4\n')
    expect(autosave).toHaveBeenCalledOnce()
  })
})
