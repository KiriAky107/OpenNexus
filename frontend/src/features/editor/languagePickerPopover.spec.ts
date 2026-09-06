// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest'
import { installLanguagePickerPopover } from './languagePickerPopover'

it('measures only open menus on ancestor scroll and cleans up scheduled work', async () => {
  const root = document.createElement('div')
  root.innerHTML = '<div><button class="language-button" data-expanded="false">JS</button><div class="language-picker"><input class="search-input"></div></div>'
  document.body.append(root)
  const menu = root.querySelector<HTMLElement>('.language-picker')!
  const trigger = root.querySelector<HTMLElement>('button')!
  let open = false
  menu.showPopover = vi.fn(() => { open = true })
  menu.hidePopover = vi.fn(() => { open = false })
  const matches = menu.matches.bind(menu)
  vi.spyOn(menu, 'matches').mockImplementation(selector => selector === ':popover-open' ? open : matches(selector))
  const measure = vi.spyOn(trigger, 'getBoundingClientRect')
  let callback: FrameRequestCallback | undefined
  const raf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(fn => { callback = fn; return 42 })
  const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})
  const dispose = installLanguagePickerPopover(root)
  try {
    document.dispatchEvent(new Event('scroll'))
    expect(raf).not.toHaveBeenCalled()
    expect(measure).not.toHaveBeenCalled()
    trigger.dataset.expanded = 'true'
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(menu.showPopover).toHaveBeenCalledOnce()
    measure.mockClear()
    document.dispatchEvent(new Event('scroll'))
    document.dispatchEvent(new Event('scroll'))
    expect(raf).toHaveBeenCalledOnce()
    callback!(0)
    expect(measure).toHaveBeenCalledOnce()
    document.dispatchEvent(new Event('scroll'))
    dispose()
    expect(cancel).toHaveBeenCalledWith(42)
    expect(menu.hidePopover).toHaveBeenCalledOnce()
    raf.mockClear()
    document.dispatchEvent(new Event('scroll'))
    expect(raf).not.toHaveBeenCalled()
  } finally { dispose(); root.remove(); vi.restoreAllMocks() }
})
