// @vitest-environment happy-dom
import { expect, it } from 'vitest'
import { visibleMatchRanges } from './searchHighlight'
it('finds repeated visible matches across formatted text nodes without expanding or matching reasoning', () => {
  const root = document.createElement('div')
  root.innerHTML = '<p>need<strong>le</strong> and needle</p><details class="thinking"><p>needle</p></details><details class="tool-result">needle</details>'
  const ranges = visibleMatchRanges(root, 'needle')
  expect(ranges.map(r => r.toString())).toEqual(['needle', 'needle'])
  expect(root.querySelector('details')!.open).toBe(false)
})
it('matches deferred visible code once and never selects its hidden copy buffer', () => {
  const root = document.createElement('div')
  root.innerHTML = '<pre><code><span class="markdown-code-chunk" style="content-visibility:auto"><span class="line">needle</span></span></code></pre><pre class="markdown-code-source" hidden>needle</pre>'
  const ranges = visibleMatchRanges(root, 'needle')
  expect(ranges).toHaveLength(1)
  expect(ranges[0]!.startContainer.parentElement!.className).toBe('line')
  expect(root.querySelector('.markdown-code-source')!.textContent).toBe('needle')
})
