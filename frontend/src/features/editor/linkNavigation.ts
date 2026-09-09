/** 可编辑锚点需要显式导航；简单的点击即可继续编辑链接。 */
export function installLinkNavigation(root: HTMLElement): () => void {
  const navigate = (event: MouseEvent) => {
    if (event.button !== 0 || !(event.ctrlKey || event.metaKey) || event.altKey) return
    const target = event.target instanceof Element ? event.target : (event.target as Node | null)?.parentElement
    const link = target?.closest<HTMLAnchorElement>('.ProseMirror a[href]')
    if (!link || !root.contains(link)) return
    const href = link.getAttribute('href')?.trim()
    if (!href) return
    // 在 Milkdown 的链接编辑器或本机导航之前消耗修改的点击。
    event.preventDefault()
    event.stopPropagation()
    let url: URL
    try { url = new URL(href, document.baseURI) } catch { return }
    if (!['http:', 'https:', 'mailto:', 'tel:'].includes(url.protocol)) return
    window.open(url.href, '_blank', 'noopener,noreferrer')
  }
  root.addEventListener('click', navigate, true)
  return () => root.removeEventListener('click', navigate, true)
}
