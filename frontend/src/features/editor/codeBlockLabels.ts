/** Mirror changed language labels without rescanning every code block on each DOM mutation. */
export function installCodeBlockLabels(root: HTMLElement): () => void {
  const sync = (block: HTMLElement) => {
    const label = block.querySelector('.language-button')?.textContent?.trim() || 'Plain text'
    if (block.dataset.languageLabel !== label) block.dataset.languageLabel = label
  }
  const discover = (node: Node, blocks: Set<HTMLElement>) => {
    if (!(node instanceof HTMLElement)) return
    if (node.matches('.milkdown-code-block')) blocks.add(node)
    node.querySelectorAll<HTMLElement>('.milkdown-code-block').forEach(block => blocks.add(block))
  }
  const observer = new MutationObserver(records => {
    const changed = new Set<HTMLElement>()
    for (const record of records) {
      const element = record.target instanceof Element ? record.target : record.target.parentElement
      // CodeMirror viewport/text changes do not change the footer's language.
      const label = element?.closest('.language-button')
      const block = label?.closest<HTMLElement>('.milkdown-code-block')
      if (block) changed.add(block)
      if ([...record.removedNodes].some(node => node instanceof Element &&
        (node.matches('.language-button') || node.querySelector('.language-button')))) {
        const owner = element?.closest<HTMLElement>('.milkdown-code-block')
        if (owner) changed.add(owner)
      }
      for (const node of record.addedNodes) {
        discover(node, changed)
        if (node instanceof Element && (node.matches('.language-button') || node.querySelector('.language-button'))) {
          const owner = node.closest<HTMLElement>('.milkdown-code-block')
          if (owner) changed.add(owner)
        }
      }
    }
    changed.forEach(block => { if (root.contains(block)) sync(block) })
  })
  root.querySelectorAll<HTMLElement>('.milkdown-code-block').forEach(sync)
  observer.observe(root, { subtree: true, childList: true, characterData: true })
  return () => observer.disconnect()
}
