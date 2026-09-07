// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useEditorStore } from '@/stores/editor'
import TitleBarMenu from './TitleBarMenu.vue'

const execute = vi.hoisted(() => vi.fn())
vi.mock('@/services/editorCommandService', () => ({ executeEditorCommand: execute }))

beforeEach(() => {
  setActivePinia(createPinia())
  execute.mockReset().mockResolvedValue({ ok: true })
})

describe('桌面顶部段落菜单', () => {
  it('源码笔记通过统一编辑命令执行属性导入', async () => {
    const editor = useEditorStore()
    editor.mode = 'source'
    editor.currentFilePath = '/示例.md'
    editor.saveStatus = 'saved'
    const wrapper = mount(TitleBarMenu)
    await wrapper.get('.menu-trigger').trigger('click')
    const item = wrapper.get('[role="menuitem"]')
    expect((item.element as HTMLButtonElement).disabled).toBe(false)
    await item.trigger('click')
    expect(execute).toHaveBeenCalledWith('editor.import-note-properties')
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('写作模式显示入口但禁止绕过编辑器能力', async () => {
    const editor = useEditorStore()
    editor.mode = 'wysiwyg'
    editor.currentFilePath = '/示例.md'
    const wrapper = mount(TitleBarMenu)
    await wrapper.get('.menu-trigger').trigger('click')
    expect((wrapper.get('[role="menuitem"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.text()).toContain('请在无冲突的 Markdown 源码笔记中使用')
    wrapper.unmount()
  })
})
