// 当对话框嵌套时，引用计数会锁定底层页面。
const locks = new WeakMap<HTMLElement, { count: number; value: string; priority: string }>()
export function lockDialogScroll(dialog: HTMLElement): () => void {
  const elements: HTMLElement[] = []
  for (let element = dialog.parentElement; element; element = element.parentElement) {
    const lock = locks.get(element)
    if (lock) lock.count++
    else {
      locks.set(element, { count: 1, value: element.style.getPropertyValue('overflow'), priority: element.style.getPropertyPriority('overflow') })
      element.style.setProperty('overflow', 'hidden', 'important')
    }
    elements.push(element)
  }
  let released = false
  return () => {
    if (released) return
    released = true
    for (const element of elements) {
      const lock = locks.get(element)!
      if (--lock.count) continue
      if (lock.value) element.style.setProperty('overflow', lock.value, lock.priority)
      else element.style.removeProperty('overflow')
      locks.delete(element)
    }
  }
}
