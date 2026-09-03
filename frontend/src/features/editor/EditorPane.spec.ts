// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import EditorPane from './EditorPane.vue'
import { useEditorStore } from '@/stores/editor'
import * as workspaceService from '@/services/workspaceService'

let wrapper: VueWrapper | null = null

async function waitForText(text: string) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (wrapper?.text().includes(text)) return
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw new Error(`Editor did not render ${text}`)
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  vi.spyOn(workspaceService, 'readFileContent').mockImplementation(async (filePath) => {
    if (filePath === '/欢迎使用 NotesAgent.md') {
      return '# 欢迎使用 NotesAgent\n\n祝你写作愉快'
    }
    if (filePath === '/数据结构/红黑树.md') return '# 红黑树\n\n新的文件内容'
    throw new Error(`Unexpected file path: ${filePath}`)
  })
  vi.spyOn(workspaceService, 'getNoteId').mockImplementation(async (filePath) =>
    filePath.includes('红黑树') ? 'note-rbt' : 'note-welcome'
  )
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('EditorPane file switching', () => {
  it('recreates the visual editor with the newly loaded file content', async () => {
    const store = useEditorStore()
    await store.loadFile('/欢迎使用 NotesAgent.md')
    wrapper = mount(EditorPane, { attachTo: document.body })
    await waitForText('欢迎使用 NotesAgent')

    await store.loadFile('/数据结构/红黑树.md')
    await nextTick()
    await waitForText('红黑树')

    expect(store.currentFilePath).toBe('/数据结构/红黑树.md')
    expect(wrapper.text()).not.toContain('祝你写作愉快')
  })
})
