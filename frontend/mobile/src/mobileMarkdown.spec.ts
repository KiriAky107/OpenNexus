// @vitest-environment jsdom
import { expect, it } from 'vitest'
import { mobileVaultPath, renderMobileMarkdown } from './mobileMarkdown'

it('renders Markdown as inert HTML and exposes remote images/links only as explicit markers', () => {
  const root = document.createElement('div')
  root.innerHTML = renderMobileMarkdown(['# 中文','<script>bad()</script>','<img src="https://remote/tracker" onerror="bad()">','![remote](https://remote/pixel)','[x](javascript:bad())','[local](../笔记.md)'].join('\n\n'))
  expect(root.querySelector('h1')?.textContent).toBe('中文')
  expect(root.querySelectorAll('script,img,iframe,object,style')).toHaveLength(0)
  expect(root.querySelector('[data-mobile-image]')?.getAttribute('data-mobile-image')).toBe(encodeURIComponent('https://remote/pixel'))
  expect(root.querySelectorAll('a[href="#"]')).toHaveLength(2)
  expect(root.querySelectorAll('[onerror]')).toHaveLength(0)
})

it('resolves original relative paths with the shared desktop boundaries', () => {
  expect(mobileVaultPath('中文/a.md','../attachments/ab/hash.png')).toBe('attachments/ab/hash.png')
  expect(mobileVaultPath('中文/a.md','../%E7%AC%94%E8%AE%B0.md#heading')).toBe('笔记.md')
  for (const path of ['../../escape','../.ainote/host.sqlite3','../opennexus-records/x','file:///private','javascript:bad()','https://remote/pixel','//remote/pixel','..%2f..%2fsecret','%zz']) expect(mobileVaultPath('中文/a.md',path)).toBeNull()
})
