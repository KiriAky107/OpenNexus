/** Editable anchors need explicit navigation; plain clicks keep editing the link. */
export function installLinkNavigation(root: HTMLElement): () => void {
  const navigate = (event: MouseEvent) => {
    if (event.button !== 0 || !(event.ctrlKey || event.metaKey) || event.altKey) return
    const target = event.target instanceof Element ? event.target : (event.target as Node | null)?.parentElement
    const link = target?.closest<HTMLAnchorElement>('.ProseMirror a[href]')
    if (!link || !root.contains(link)) return
    const href = link.getAttribute('href')?.trim()
    if (!href) return
    // Consume modified clicks before Milkdown's link editor or native navigation.
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
