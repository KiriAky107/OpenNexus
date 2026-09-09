import { renderFunctionPlot } from '@/services/functionPlotService'
import { nextTick } from 'vue'
import { renderMermaid } from '@/services/mermaidService'
import { t } from '@/i18n'
import { appendDiagramControls } from '@/utils/diagramControls'

let previewId = 0
export function createMermaidPreview(source: string, dark: boolean, applyPreview: (value: HTMLElement) => void, kind = 'mermaid', themeId = 'light'): HTMLElement {
  // 每个修订版本都拥有其元素，因此缓慢的渲染无法替换较新的内容。 Milkdown 清理其内部 HTML 的 Element 输入；将修订标记和控件保留在一次性信封内。
  const envelope = document.createElement('div')
  const container = document.createElement('div')
  envelope.append(container)
  container.className = 'editor-mermaid-preview' + (kind === 'function-plot' ? ' function-plot-preview' : '')
  container.id = `editor-mermaid-preview-${++previewId}`
  envelope.dataset.previewId = container.id
  container.setAttribute('aria-live', 'polite')
  container.textContent = t('正在渲染图表…', 'Rendering diagram…')
  const publish = async () => {
    await nextTick()
    // Milkdown 清理并复制该元素。仅当其修订版本仍然存在时才发布；编辑、语言更改和卸载会删除旧标记。
    const visible = document.getElementById(container.id)
    if (visible) {
      // PreviewPanel 复制 HTML，而不是保留提供的元素。通过 Milkdown 的反应式回调更新当前副本。
      applyPreview(envelope.cloneNode(true) as HTMLElement)
    }
  }
  void (kind === 'function-plot' ? new Promise<void>(resolve => setTimeout(resolve, 180)) : Promise.resolve()).then<{ svg: string; warnings: string[] }>(() => {
    if (kind === 'function-plot' && !document.getElementById(container.id)) throw new Error('stale preview')
    return kind === 'function-plot' ? renderFunctionPlot(source, themeId) : renderMermaid(source, { theme: dark ? 'dark' : 'light' })
  }).then(result => {
    if (result.warnings.length && (kind === 'mermaid' || !result.svg)) {
      container.classList.add('has-error')
      container.textContent = `${t('图表语法有误，可点击编辑修改：', 'Diagram syntax error. Choose Edit to fix:')} ${result.warnings.join('\n')}`
      void publish()
      return
    }
    // Mermaid以严格模式运行； Milkdown 在插入之前清理预览。
    container.innerHTML = result.svg
    appendDiagramControls(container)
    if (result.warnings.length) { const warning = document.createElement('p'); warning.textContent = result.warnings.join('\n'); warning.setAttribute('role', 'status'); container.append(warning) }
    void publish()
  }).catch(() => {
    container.textContent = t('图表渲染失败，请点击编辑检查源码。', 'Unable to render diagram. Choose Edit to inspect the source.')
    void publish()
  })
  return envelope
}
