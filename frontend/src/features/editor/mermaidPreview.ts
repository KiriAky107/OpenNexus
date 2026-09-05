import { nextTick } from 'vue'
import { renderMermaid } from '@/services/mermaidService'
import { t } from '@/i18n'

let previewId = 0
export function createMermaidPreview(source: string, dark: boolean, applyPreview: (value: HTMLElement) => void): HTMLElement {
  // Each revision owns its element, so a slow render cannot replace newer content.
  const container = document.createElement('div')
  container.className = 'editor-mermaid-preview'
  container.id = `editor-mermaid-preview-${++previewId}`
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
      applyPreview(container.cloneNode(true) as HTMLElement)
    }
  }
  void renderMermaid(source, { theme: dark ? 'dark' : 'light' }).then(result => {
    if (result.warnings.length) {
      container.classList.add('has-error')
      container.textContent = `${t('图表语法有误，可点击编辑修改：', 'Diagram syntax error. Choose Edit to fix:')} ${result.warnings.join('\n')}`
      void publish()
      return
    }
    // Mermaid runs in strict mode; Milkdown sanitizes the preview before insertion.
    container.innerHTML = result.svg
    void publish()
  }).catch(() => {
    container.textContent = t('图表渲染失败，请点击编辑检查源码。', 'Unable to render diagram. Choose Edit to inspect the source.')
    void publish()
  })
  return container
}
