/** 将菜单提升到顶层；只有打开的菜单才需要滚动测量。 */
export function installLanguagePickerPopover(root: HTMLElement): () => void {
  const menus = new Set<HTMLElement>()
  const openMenus = new Set<HTMLElement>()
  let frame = 0
  function sync(menu: HTMLElement) {
    if (!root.contains(menu)) { menus.delete(menu); openMenus.delete(menu); return }
    const trigger = menu.parentElement?.querySelector<HTMLElement>('.language-button')
    if (!trigger || typeof menu.showPopover !== 'function') return
    menus.add(menu)
    menu.setAttribute('popover', 'manual')
    const search = menu.querySelector<HTMLInputElement>('.search-input')
    if (search) { search.autocomplete = 'off'; search.spellcheck = false }
    if (trigger.dataset.expanded !== 'true' || !menu.firstElementChild) {
      openMenus.delete(menu)
      if (menu.matches(':popover-open')) menu.hidePopover()
      return
    }
    openMenus.add(menu)
    if (!menu.matches(':popover-open')) menu.showPopover()
    const anchor = trigger.getBoundingClientRect()
    const below = window.innerHeight - anchor.bottom - 16, above = anchor.top - 16
    const placeAbove = below < 240 && above > below
    const available = Math.max(80, placeAbove ? above : below)
    menu.style.setProperty('--picker-list-height', `${Math.min(280, Math.max(32, available - 64))}px`)
    const bounds = menu.getBoundingClientRect()
    menu.style.setProperty('--picker-left', `${Math.max(12, Math.min(anchor.left, window.innerWidth - bounds.width - 12))}px`)
    menu.style.setProperty('--picker-top', `${Math.max(12, placeAbove ? anchor.top - bounds.height - 8 : anchor.bottom + 8)}px`)
  }
  function discover(node: Node, changed: Set<HTMLElement>) {
    if (!(node instanceof HTMLElement)) return
    if (node.matches('.language-picker')) changed.add(node)
    node.querySelectorAll<HTMLElement>('.language-picker').forEach(menu => changed.add(menu))
  }
  const observer = new MutationObserver(records => {
    const changed = new Set<HTMLElement>()
    let removed = false
    for (const record of records) {
      const target = record.target instanceof Element ? record.target : record.target.parentElement
      const menu = target?.closest<HTMLElement>('.language-picker')
      if (menu) changed.add(menu)
      if (record.type === 'attributes') {
        const sibling = target?.parentElement?.querySelector<HTMLElement>('.language-picker')
        if (sibling) changed.add(sibling)
      }
      record.addedNodes.forEach(node => discover(node, changed))
      removed ||= record.removedNodes.length > 0
    }
    if (removed) for (const menu of menus) if (!root.contains(menu)) { menus.delete(menu); openMenus.delete(menu) }
    changed.forEach(sync)
  })
  const positionOpenMenus = () => {
    if (!openMenus.size || frame) return
    frame = requestAnimationFrame(() => { frame = 0; openMenus.forEach(sync) })
  }
  root.querySelectorAll<HTMLElement>('.language-picker').forEach(sync)
  observer.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-expanded'] })
  // 外部编辑器视口是根的祖先，因此在文档上侦听捕获。
  document.addEventListener('scroll', positionOpenMenus, { capture: true, passive: true })
  window.addEventListener('resize', positionOpenMenus)
  return () => {
    observer.disconnect(); cancelAnimationFrame(frame)
    document.removeEventListener('scroll', positionOpenMenus, true)
    window.removeEventListener('resize', positionOpenMenus)
    for (const menu of openMenus) if (menu.matches(':popover-open')) menu.hidePopover()
    menus.clear(); openMenus.clear()
  }
}
