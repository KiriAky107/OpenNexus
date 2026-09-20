/** 统一接管编辑器链接，让桌面端内部文件、媒体定位和外部 URL 使用各自安全的导航通道。 */
export function installLinkNavigation(root: HTMLElement, open: (href: string) => unknown | Promise<unknown>): () => void {
  const navigate = (event: MouseEvent) => {
    if (event.button !== 0 || event.altKey) return
    const target = event.target instanceof Element ? event.target : (event.target as Node | null)?.parentElement
    const link = target?.closest<HTMLAnchorElement>('.ProseMirror a[href]')
    if (!link || !root.contains(link)) return
    const href = link.getAttribute('href')?.trim()
    if (!href) return
    // 在 Milkdown 的链接编辑器或 WebView 默认导航之前消费点击。
    event.preventDefault()
    event.stopPropagation()
    void open(href)
  }
  root.addEventListener('click', navigate, true)
  return () => root.removeEventListener('click', navigate, true)
}
