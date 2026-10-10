// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { installLinkNavigation } from './linkNavigation'

afterEach(() => { document.body.innerHTML = ''; vi.restoreAllMocks() })

it('leaves plain clicks for source editing and navigates with Ctrl or Command', () => {
  const root = document.createElement('div')
  root.innerHTML = '<div class="ProseMirror"><a href="https://example.com/docs"><strong>Docs</strong></a></div>'
  document.body.append(root)
  const open = vi.fn()
  const dispose = installLinkNavigation(root, open, vi.fn())
  const target = root.querySelector('strong')!
  const click = (options: MouseEventInit) => {
    const event = new MouseEvent('click', { bubbles: true, cancelable: true, ...options })
    target.dispatchEvent(event)
    return event
  }
  expect(click({ button: 2 }).defaultPrevented).toBe(false)
  expect(open).not.toHaveBeenCalled()
  expect(click({}).defaultPrevented).toBe(true)
  expect(open).not.toHaveBeenCalled()
  click({ ctrlKey: true })
  expect(open).toHaveBeenLastCalledWith('https://example.com/docs')
  click({ metaKey: true })
  expect(open).toHaveBeenCalledTimes(2)
  root.querySelector('a')!.href = 'javascript:alert(1)'
  expect(click({ ctrlKey: true }).defaultPrevented).toBe(true)
  expect(open).toHaveBeenCalledTimes(3)
  dispose()
  root.querySelector('a')!.href = 'https://example.com'
  expect(click({}).defaultPrevented).toBe(false)
  expect(open).toHaveBeenCalledTimes(3)
})

it('reports synchronous and asynchronous open failures without an unhandled rejection', async () => {
  const root = document.createElement('div')
  root.innerHTML = '<div class="ProseMirror"><a href="missing.md">Missing</a></div>'
  const reason = new Error('File is temporarily locked')
  const open = vi.fn().mockImplementationOnce(() => { throw reason }).mockRejectedValueOnce(reason)
  const report = vi.fn()
  const dispose = installLinkNavigation(root, open, report)
  const click = () => root.querySelector('a')!.dispatchEvent(new MouseEvent('click', { bubbles: true, ctrlKey: true }))
  click(); await Promise.resolve()
  expect(report).toHaveBeenCalledExactlyOnceWith(reason)
  click(); await Promise.resolve()
  expect(report).toHaveBeenCalledTimes(2)
  dispose()
})

it.each(['newer link', 'disposed editor'])('ignores an older failure after a %s', async action => {
  const root = document.createElement('div')
  root.innerHTML = '<div class="ProseMirror"><a href="first.md">First</a></div>'
  let reject!: (reason: Error) => void
  const open = vi.fn().mockImplementationOnce(() => new Promise((_resolve, no) => { reject = no })).mockResolvedValueOnce(true)
  const report = vi.fn()
  const dispose = installLinkNavigation(root, open, report)
  const click = () => root.querySelector('a')!.dispatchEvent(new MouseEvent('click', { bubbles: true, ctrlKey: true }))
  click()
  if (action === 'disposed editor') dispose()
  else { root.querySelector('a')!.setAttribute('href', 'second.md'); click() }
  reject(new Error('Outdated open failure')); await Promise.resolve()
  expect(report).not.toHaveBeenCalled()
  dispose()
})
