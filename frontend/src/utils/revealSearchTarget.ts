/** Keep a search result visible while skipped code chunks acquire real heights. */
export function revealSearchTarget(target: HTMLElement, viewport: HTMLElement): () => void {
  let frame = 0, frames = 0, stable = 0, stopped = false
  let timeout: ReturnType<typeof setTimeout> | undefined
  const scrollingKeys = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '])
  const stop = () => {
    if (stopped) return
    stopped = true
    cancelAnimationFrame(frame)
    clearTimeout(timeout)
    viewport.removeEventListener('wheel', stop)
    viewport.removeEventListener('touchstart', stop)
    viewport.removeEventListener('pointerdown', stop)
    viewport.removeEventListener('keydown', onKey)
  }
  const onKey = (event: KeyboardEvent) => { if (scrollingKeys.has(event.key)) stop() }
  if (!target.isConnected || !viewport.contains(target) || !viewport.clientHeight) return stop
  viewport.addEventListener('wheel', stop, { passive: true })
  viewport.addEventListener('touchstart', stop, { passive: true })
  viewport.addEventListener('pointerdown', stop, { passive: true })
  viewport.addEventListener('keydown', onKey)
  timeout = setTimeout(stop, 2000)
  target.scrollIntoView?.({ block: 'center', inline: 'nearest', behavior: 'instant' })
  const started = performance.now()
  const place = () => {
    if (stopped || !target.isConnected || !viewport.contains(target)) { stop(); return }
    const box = target.getBoundingClientRect(), visible = viewport.getBoundingClientRect()
    const middle = visible.top + viewport.clientTop + viewport.clientHeight / 2
    const offset = box.top + Math.min(box.height, viewport.clientHeight) / 2 - middle
    const desired = Math.max(0, Math.min(viewport.scrollHeight - viewport.clientHeight, viewport.scrollTop + offset))
    if (Math.abs(desired - viewport.scrollTop) > 2) {
      viewport.scrollTop = desired
      stable = 0
    } else stable++
    // Bound work even if a live response keeps changing. A new result, manual
    // scroll, navigation or component disposal cancels this placement earlier.
    const elapsed = performance.now() - started
    // A deferred chunk can resize the timeline after several quiet frames.
    // Keep observing through the first layout turn, then release stable results.
    if (++frames >= 120 || (stable >= 5 && elapsed >= 300) || elapsed >= 2000) { stop(); return }
    frame = requestAnimationFrame(place)
  }
  place()
  return stop
}
