// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import AppDialog from './AppDialog.vue'
const mounted: VueWrapper[] = []
afterEach(() => { mounted.splice(0).reverse().forEach(w => w.unmount()); document.body.innerHTML = ''; document.body.style.cssText = ''; document.documentElement.style.cssText = '' })
it('locks all scroll ancestors and restores focus and inline styles', async () => {
  const opener = document.createElement('button'); document.body.append(opener); opener.focus()
  const host = document.createElement('div'); host.style.setProperty('overflow', 'auto', 'important'); document.body.append(host)
  const w = mount(AppDialog, { props:{label:'测试'}, slots:{default:'<section class="modal"><input autofocus /></section>'}, attachTo:host }); mounted.push(w)
  expect(w.get('dialog').element.open).toBe(true)
  expect(host.style.overflow).toBe('hidden')
  expect(document.body.style.overflow).toBe('hidden')
  await w.get('dialog').trigger('keydown', {key:'Escape'})
  expect(w.emitted('close')).toHaveLength(1)
  w.unmount(); mounted.pop()
  expect(host.style.overflow).toBe('auto')
  expect(host.style.getPropertyPriority('overflow')).toBe('important')
  expect(document.body.style.overflow).toBe('')
  expect(document.activeElement).toBe(opener)
})
it('retains scroll locks until the last nested dialog closes', () => {
  const first = mount(AppDialog, {props:{label:'父弹窗'}, attachTo:document.body}); mounted.push(first)
  const second = mount(AppDialog, {props:{label:'子弹窗'}, attachTo:document.body}); mounted.push(second)
  first.unmount(); mounted.splice(0,1)
  expect(document.body.style.overflow).toBe('hidden')
  second.unmount(); mounted.pop()
  expect(document.body.style.overflow).toBe('')
})
it('does not dismiss permission or busy dialogs through Escape or backdrop', async () => {
  const w = mount(AppDialog, {props:{label:'权限确认',dismissible:false},attachTo:document.body}); mounted.push(w)
  await w.get('dialog').trigger('keydown',{key:'Escape'})
  await w.get('dialog').trigger('cancel')
  await w.get('dialog').trigger('click')
  expect(w.emitted('close')).toBeUndefined()
})
