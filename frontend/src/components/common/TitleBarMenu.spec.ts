// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useEditorStore } from '@/stores/editor'
import TitleBarMenu from './TitleBarMenu.vue'

const execute = vi.hoisted(() => vi.fn())
const capabilities = vi.hoisted(() => vi.fn())
vi.mock('@/services/editorCommandService', () => ({
  executeEditorCommand: execute,
  getEditorCommandCapabilities: capabilities,
  subscribeEditorCommandCapabilities: () => () => undefined,
}))

beforeEach(() => {
  setActivePinia(createPinia())
  execute.mockReset().mockResolvedValue({ ok: true })
  capabilities.mockReturnValue([
    { id: 'editor.paragraph', supported: true, enabled: true },
    { id: 'editor.heading', supported: true, enabled: true },
    { id: 'editor.callout', supported: true, enabled: true },
    { id: 'editor.import-note-properties', supported: true, enabled: true },
  ])
})

describe('桌面顶部段落菜单', () => {
  it('源码笔记通过统一编辑命令执行属性导入', async () => {
    const editor = useEditorStore()
    editor.mode = 'source'
    editor.currentFilePath = '/示例.md'
    editor.saveStatus = 'saved'
    const wrapper = mount(TitleBarMenu)
    expect(wrapper.text()).toContain('文件编辑段落格式视图主题帮助')
    await wrapper.get('[data-menu="paragraph"] .menu-trigger').trigger('click')
    const item = wrapper.get('.import-properties')
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
    capabilities.mockReturnValue([])
    const wrapper = mount(TitleBarMenu)
    await wrapper.get('[data-menu="paragraph"] .menu-trigger').trigger('click')
    expect((wrapper.get('.import-properties').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.text()).toContain('请在无冲突的 Markdown 源码笔记中使用')
    wrapper.unmount()
  })

  it('标题命令传入对应级别并显示快捷键', async () => {
    const wrapper = mount(TitleBarMenu)
    await wrapper.get('[data-menu="paragraph"] .menu-trigger').trigger('click')
    const headings = wrapper.findAll('[data-menu="paragraph"] [role="menuitem"]').filter(item => item.text().includes('级标题'))
    await headings[1].trigger('click')
    expect(execute).toHaveBeenCalledWith('editor.heading', 2)
    await wrapper.get('[data-menu="paragraph"] .menu-trigger').trigger('click')
    expect(wrapper.get('.import-properties').text()).toContain('Ctrl+Alt+P')
    wrapper.unmount()
  })

  it('格式菜单列出十四种警告框和元数据快捷键', async () => {
    const wrapper = mount(TitleBarMenu)
    await wrapper.get('[data-menu="format"] .menu-trigger').trigger('click')
    const calloutTrigger = wrapper.get('.submenu-trigger')
    expect(calloutTrigger.text()).toContain('Ctrl+Alt+C')
    await calloutTrigger.trigger('click')
    expect(wrapper.findAll('.submenu-popover button')).toHaveLength(14)
    expect(wrapper.get('.metadata-command').text()).toContain('Ctrl+Alt+P')
    wrapper.unmount()
  })

  it('支持菜单栏方向键与警告框子菜单键盘访问', async () => {
    capabilities.mockReturnValue([{ id: 'editor.callout', supported: true, enabled: true }])
    const wrapper = mount(TitleBarMenu, { attachTo: document.body })
    const file = wrapper.get('[data-menu="file"] .menu-trigger')
    ;(file.element as HTMLElement).focus()
    await file.trigger('keydown', { key: 'ArrowRight' })
    expect(document.activeElement).toBe(wrapper.get('[data-menu="edit"] .menu-trigger').element)

    await wrapper.get('[data-menu="format"] .menu-trigger').trigger('click')
    const trigger = wrapper.get('.submenu-trigger')
    ;(trigger.element as HTMLElement).focus()
    await trigger.trigger('keydown', { key: 'ArrowRight' })
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(wrapper.findAll('.submenu-popover [role="menuitem"]')).toHaveLength(14)
    expect((document.activeElement as HTMLElement).closest('.submenu-popover')).not.toBeNull()
    wrapper.unmount()
  })
})
