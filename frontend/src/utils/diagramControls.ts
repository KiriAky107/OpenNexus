/** Markup survives Milkdown's preview copying; the enclosing Vue component handles clicks. */
export function appendDiagramControls(container: HTMLElement) {
  const controls = document.createElement('div')
  controls.className = 'diagram-controls'
  controls.setAttribute('contenteditable', 'false')
  for (const [action, label] of [['out', '缩小图表'], ['in', '放大图表'], ['reset', '重置缩放'], ['view', '大图查看']]) {
    const button = document.createElement('button')
    button.type = 'button'
    button.dataset.diagramAction = action
    button.textContent = label
    controls.append(button)
  }
  container.append(controls)
}
