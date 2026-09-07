import { renderFunctionPlot } from '@/services/functionPlotService'
import { nextTick } from 'vue'
import { renderMermaid } from '@/services/mermaidService'
import { t } from '@/i18n'
import { appendDiagramControls } from '@/utils/diagramControls'

let previewId = 0
export function createMermaidPreview(source: string, dark: boolean, applyPreview: (value: HTMLElement) => void, kind = 'mermaid', themeId = 'light'): HTMLElement {
  // Each revision owns its element, so a slow render cannot replace newer content.
  // Milkdown sanitizes Element input to its inner HTML; retain the revision
  // marker and controls inside an otherwise disposable envelope.
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
    // Milkdown sanitizes and copies this element. Publish only if its revision
    // still exists; edits, language changes and unmounts remove the old marker.
    const visible = document.getElementById(container.id)
    if (visible) {
      // PreviewPanel copies HTML instead of retaining the supplied element.
      // Update the current copy through Milkdown's reactive callback.
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
    // Mermaid runs in strict mode; Milkdown sanitizes the preview before insertion.
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
