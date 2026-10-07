import { afterEach, expect, it, vi } from 'vitest'
import { computePreviewHighlight } from './previewHighlightProcessor'
import { MAX_PREVIEW_SOURCE, type PreviewHighlightTiming } from './previewHighlightProtocol'

const grammar = vi.hoisted(() => ({ load: vi.fn() }))
vi.mock('./codeHighlighter', () => ({ loadCodeLanguage: grammar.load }))
afterEach(() => { grammar.load.mockReset(); vi.restoreAllMocks() })

it('measures asynchronous grammar waiting separately from synchronous tokenization', async () => {
  let ready!: (value: unknown) => void
  grammar.load.mockReturnValue(new Promise(resolve => { ready = resolve }))
  const clock = vi.spyOn(performance, 'now').mockReturnValueOnce(10).mockReturnValueOnce(40).mockReturnValueOnce(48)
  let timing: PreviewHighlightTiming | undefined
  const result = computePreviewHighlight('const value = 42', 'typescript', value => { timing = value })
  expect(timing).toBeUndefined()
  const codeToHtml = vi.fn().mockReturnValue('<pre>colored</pre>')
  ready({ shiki: { codeToHtml }, language: 'typescript' })
  expect(await result).toBe('<pre>colored</pre>')
  expect(timing).toEqual({ language_load_ms: 30, highlight_ms: 8, worker_ms: 38 })
  expect(clock).toHaveBeenCalledTimes(3)
  expect(codeToHtml).toHaveBeenCalledExactlyOnceWith('const value = 42', expect.objectContaining({ lang: 'typescript', defaultColor: false }))
})

it('does not claim a successful measurement for declined input or a failed grammar', async () => {
  const measured = vi.fn()
  expect(await computePreviewHighlight('x'.repeat(MAX_PREVIEW_SOURCE + 1), 'text', measured)).toBeUndefined()
  expect(grammar.load).not.toHaveBeenCalled()
  grammar.load.mockRejectedValue(Error('grammar unavailable'))
  await expect(computePreviewHighlight('code', 'broken', measured)).rejects.toThrow('grammar unavailable')
  expect(measured).not.toHaveBeenCalled()
})
