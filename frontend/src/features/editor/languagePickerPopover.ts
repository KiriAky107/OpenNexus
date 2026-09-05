/** Promote Milkdown's menu to the top layer without moving its Vue-owned DOM. */
export function installLanguagePickerPopover(root: HTMLElement): () => void {
  const menus = new Set<HTMLElement>()
  function sync() {
    root.querySelectorAll<HTMLElement>('.language-picker').forEach(menu => {
      const trigger = menu.parentElement?.querySelector<HTMLElement>('.language-button')
      if (!trigger || typeof menu.showPopover !== 'function') return
      menus.add(menu)
      menu.setAttribute('popover', 'manual')
      const search = menu.querySelector<HTMLInputElement>('.search-input')
      if (search) {
        search.autocomplete = 'off'
        search.spellcheck = false
      }
      if (trigger.dataset.expanded !== 'true' || !menu.firstElementChild) {
        if (menu.matches(':popover-open')) menu.hidePopover()
        return
      }
      if (!menu.matches(':popover-open')) menu.showPopover()
      const anchor = trigger.getBoundingClientRect()
      const below = window.innerHeight - anchor.bottom - 16
      const above = anchor.top - 16
      const placeAbove = below < 240 && above > below
      const available = Math.max(80, placeAbove ? above : below)
      menu.style.setProperty('--picker-list-height', `${Math.min(280, Math.max(32, available - 64))}px`)
      const bounds = menu.getBoundingClientRect()
      menu.style.setProperty('--picker-left', `${Math.max(12, Math.min(anchor.left, window.innerWidth - bounds.width - 12))}px`)
      menu.style.setProperty('--picker-top', `${Math.max(12, placeAbove ? anchor.top - bounds.height - 8 : anchor.bottom + 8)}px`)
    })
    for (const menu of menus) if (!root.contains(menu)) menus.delete(menu)
  }
  const observer = new MutationObserver(sync)
  observer.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-expanded'] })
  root.addEventListener('scroll', sync, true)
  window.addEventListener('resize', sync)
  sync()
  return () => {
    observer.disconnect()
    root.removeEventListener('scroll', sync, true)
    window.removeEventListener('resize', sync)
    for (const menu of menus) if (menu.matches(':popover-open')) menu.hidePopover()
  }
}
