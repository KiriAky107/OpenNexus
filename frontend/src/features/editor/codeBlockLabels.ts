/** Mirror the live picker label for theme decorations without changing Markdown. */
export function installCodeBlockLabels(root: HTMLElement): () => void {
  const sync = () => root.querySelectorAll<HTMLElement>('.milkdown-code-block').forEach(block => {
    const label = block.querySelector('.language-button')?.textContent?.trim() || 'Plain text'
    if (block.dataset.languageLabel !== label) block.dataset.languageLabel = label
  })
  const observer = new MutationObserver(sync)
  observer.observe(root, { subtree: true, childList: true, characterData: true })
  sync()
  return () => observer.disconnect()
}
