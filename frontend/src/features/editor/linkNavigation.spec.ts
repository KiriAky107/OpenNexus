// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { installLinkNavigation } from './linkNavigation'

afterEach(() => { document.body.innerHTML = ''; vi.restoreAllMocks() })

it('opens nested link content with Ctrl/Command click but leaves ordinary editing alone', () => {
  const root = document.createElement('div')
  root.innerHTML = '<div class="ProseMirror"><a href="https://example.com/docs"><strong>Docs</strong></a></div>'
  document.body.append(root)
  const dispose = installLinkNavigation(root)
  const open = vi.spyOn(window, 'open').mockReturnValue(null)
  const target = root.querySelector('strong')!
  const click = (options: MouseEventInit) => {
    const event = new MouseEvent('click', { bubbles: true, cancelable: true, ...options })
    target.dispatchEvent(event)
    return event
  }
  expect(click({}).defaultPrevented).toBe(false)
  click({ ctrlKey: true, button: 2 })
  expect(open).not.toHaveBeenCalled()
  expect(click({ ctrlKey: true }).defaultPrevented).toBe(true)
  expect(open).toHaveBeenLastCalledWith('https://example.com/docs', '_blank', 'noopener,noreferrer')
  click({ metaKey: true })
  expect(open).toHaveBeenCalledTimes(2)
  root.querySelector('a')!.href = 'javascript:alert(1)'
  expect(click({ ctrlKey: true }).defaultPrevented).toBe(true)
  expect(open).toHaveBeenCalledTimes(2)
  dispose()
  root.querySelector('a')!.href = 'https://example.com'
  expect(click({ ctrlKey: true }).defaultPrevented).toBe(false)
  expect(open).toHaveBeenCalledTimes(2)
})
