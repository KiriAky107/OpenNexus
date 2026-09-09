// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import TitleBarMenu from './TitleBarMenu.vue'

const execute = vi.hoisted(() => vi.fn())
const capabilities = vi.hoisted(() => vi.fn())
const routerPush = vi.hoisted(() => vi.fn())
vi.mock('@/services/editorCommandService', () => ({
  executeEditorCommand: execute,
  getEditorCommandCapabilities: capabilities,
  subscribeEditorCommandCapabilities: () => () => undefined,
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ name: 'workspace' }),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  execute.mockReset().mockResolvedValue({ ok: true })
  routerPush.mockReset()
  capabilities.mockReturnValue([
    { id: 'editor.paragraph', supported: true, enabled: true },
    { id: 'editor.heading', supported: true, enabled: true },
    { id: 'editor.callout', supported: true, enabled: true },
    { id: 'editor.import-note-properties', supported: true, enabled: true },
  ])
})

describe('桌面顶部段落菜单', () => {
  it('文件菜单承载实际工作区命令和笔记导出', async () => {
    const editor = useEditorStore()
    editor.currentFilePath = '/示例.md'
    editor.content = '# 示例'
    editor.saveStatus = 'saved'
    const wrapper = mount(TitleBarMenu, { global: { stubs: { ExportDialog: { template: '<div data-testid="export-dialog" />' } } } })
    await wrapper.get('[data-menu="file"] .menu-trigger').trigger('click')
    expect(wrapper.text()).toContain('新建笔记…')
    expect(wrapper.text()).toContain('打开其他知识库…')
    expect(wrapper.text()).toContain('刷新文件树')
    expect(wrapper.text()).toContain('下载 Markdown 副本')
    await wrapper.get('.export-command').trigger('click')
    expect(wrapper.find('[data-testid="export-dialog"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('视图和帮助菜单只连接项目已有页面', async () => {
    useWorkspaceStore().hasVault = true
    const wrapper = mount(TitleBarMenu)
    await wrapper.get('[data-menu="view"] .menu-trigger').trigger('click')
    expect(wrapper.text()).toContain('工作区搜索AI 对话智能体任务音视频')
    expect(wrapper.text()).toContain('Skill 管理Plugin 管理MCP 服务器')
    await wrapper.get('[data-menu="help"] .menu-trigger').trigger('click')
    expect(wrapper.text()).toContain('运行日志Benchmark 评测社区目录Function Plot 教程设置与诊断…')
    await wrapper.get('[data-help-function-plot]').trigger('click')
    expect(routerPush).toHaveBeenCalledWith('/help/function-plot')
    wrapper.unmount()
  })

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
