import { afterAll, beforeAll } from 'vitest'

/** DOM test engines omit this browser API; real dialog behavior is checked in the browser/APK. */
export function installTestDialogApi() {
  const prototype = HTMLDialogElement.prototype
  const originalShow = Object.getOwnPropertyDescriptor(prototype, 'showModal')
  const originalClose = Object.getOwnPropertyDescriptor(prototype, 'close')
  beforeAll(() => {
    Object.defineProperties(prototype, {
      showModal: { configurable: true, value(this: HTMLDialogElement) { this.setAttribute('open', '') } },
      close: { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute('open') } },
    })
  })
  afterAll(() => {
    if (originalShow) Object.defineProperty(prototype, 'showModal', originalShow)
    else Reflect.deleteProperty(prototype, 'showModal')
    if (originalClose) Object.defineProperty(prototype, 'close', originalClose)
    else Reflect.deleteProperty(prototype, 'close')
  })
}
