/** Markup survives Milkdown's preview copying; the enclosing Vue component handles clicks. */
export function appendDiagramControls(container: HTMLElement) {
  const controls = document.createElement('div')
  controls.className = 'diagram-controls'
  controls.setAttribute('contenteditable', 'false')
  for (const [action, label] of [['out', '缩小图表'], ['in', '放大图表'], ['reset', '重置缩放'], ['view', '大图查看']]) {
    const button = document.createElement('button')
    button.type = 'button'
    button.dataset.diagramAction = action
    const paths: Record<string, string> = {
      out: 'M8 11h6 M15 15l5 5 M17 10a7 7 0 1 1-14 0a7 7 0 0 1 14 0',
      in: 'M8 10h6 M11 7v6 M15 15l5 5 M17 10a7 7 0 1 1-14 0a7 7 0 0 1 14 0',
      reset: 'M3 10a9 9 0 1 1 2 8 M3 3v7h7',
      view: 'M3 9V3h6 M15 3h6v6 M21 15v6h-6 M9 21H3v-6',
    }
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    icon.setAttribute('viewBox', '0 0 24 24')
    icon.setAttribute('width', '16'); icon.setAttribute('height', '16')
    icon.setAttribute('aria-hidden', 'true')
    const path = document.createElementNS(icon.namespaceURI, 'path')
    path.setAttribute('d', paths[action!]!)
    path.setAttribute('fill', 'none'); path.setAttribute('stroke', 'currentColor'); path.setAttribute('stroke-width', '1.8')
    icon.append(path)
    button.append(icon, document.createTextNode(label!))
    controls.append(button)
  }
  container.append(controls)
}
