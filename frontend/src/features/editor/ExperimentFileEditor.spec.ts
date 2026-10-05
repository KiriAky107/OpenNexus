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
  vi.useRealTimers()
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

  it('retains Windows line endings while editing and previewing CSV', async () => {
    setActivePinia(createPinia())
    const editor = useEditorStore()
    editor.currentFilePath = '/experiments/input.csv'
    editor.content = 'name,value\r\n甲,3\r\n'
    editor.saveStatus = 'saved'
    vi.spyOn(editor, 'scheduleAutoSave').mockImplementation(() => {})
    wrapper = mount(ExperimentFileEditor)
    await wrapper.get('textarea').setValue('name,value\n甲,4\n乙,5\n')
    expect(editor.content).toBe('name,value\r\n甲,4\r\n乙,5\r\n')
    await wrapper.get('button[aria-pressed="false"]').trigger('click')
    expect(wrapper.get('table').text()).toContain('甲4乙5')
    await wrapper.get('button[aria-pressed="true"]').trigger('click')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('name,value\n甲,4\n乙,5\n')
    expect(editor.content).toBe('name,value\r\n甲,4\r\n乙,5\r\n')
  })

  it('cancels a pending save and waits until Chinese composition finishes', async () => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    const editor = useEditorStore()
    editor.currentFilePath = '/experiments/中文.py'
    editor.content = 'print("")\r\n'
    editor.saveStatus = 'saved'
    const autosave = vi.spyOn(editor, 'scheduleAutoSave')
    wrapper = mount(ExperimentFileEditor)
    const textarea = wrapper.get('textarea')
    await textarea.setValue('print("z")\n')
    expect(vi.getTimerCount()).toBe(1)
    await textarea.trigger('compositionstart')
    expect(vi.getTimerCount()).toBe(0)
    await textarea.setValue('print("中文")\n')
    expect(editor.content).toBe('print("中文")\r\n')
    expect(autosave).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
    await textarea.trigger('compositionend')
    expect(autosave).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(1)
    editor.cancelPendingAutoSave()
  })

  it('does not apply delayed composition events to the newly opened file', async () => {
    setActivePinia(createPinia())
    const editor = useEditorStore()
    editor.currentFilePath = '/experiments/old.py'
    editor.content = 'old'
    wrapper = mount(ExperimentFileEditor)
    const textarea = wrapper.get('textarea')
    await textarea.trigger('compositionstart')
    editor.currentFilePath = '/experiments/new.py'; editor.content = 'keep new source'
    ;(textarea.element as HTMLTextAreaElement).value = 'late old input'
    await textarea.trigger('compositionend')
    expect(editor.content).toBe('keep new source')
  })
})
