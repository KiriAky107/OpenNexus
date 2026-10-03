import { expect, it } from 'vitest'
import { computeHighlight } from './codeHighlightProcessor'

const input = { language: 'typescript', theme: 'github-light' as const }
function stylesAt(result: Awaited<ReturnType<typeof computeHighlight>>, from: number, to: number) {
  const styles = new Set<string>()
  for (let index = 0; index < result!.spans.length; index += 3) {
    if (result!.spans[index]! < to && result!.spans[index + 1]! > from) styles.add(result!.styles[result!.spans[index + 2]!]!)
  }
  return styles
}

it('preserves cross-line comment and template-string grammar for off-screen prefixes', async () => {
  const code = 'const answer = 42'
  const prefix = '/*\n' + 'comment line\n'.repeat(200)
  const commented = await computeHighlight({ ...input, source: prefix + code + '\n*/' })
  expect(stylesAt(commented, prefix.length, prefix.length + code.length)).toEqual(stylesAt(commented, 0, 2))
  const closed = prefix + '*/\n'
  const uncommented = await computeHighlight({ ...input, source: closed + code })
  expect(stylesAt(uncommented, closed.length, closed.length + code.length).size).toBeGreaterThan(1)
  const templatePrefix = 'const text = `\n' + 'string line\n'.repeat(200)
  const template = await computeHighlight({ ...input, source: templatePrefix + code + '\n`' })
  expect(stylesAt(template, templatePrefix.length, templatePrefix.length + code.length).size).toBe(1)
})

it.each(['\n', '\r\n'])('uses UTF-16 offsets for Unicode and %j line endings', async newline => {
  const source = ['const 城市 = "😀"', '', '/* comment */', 'console.log(城市)'].join(newline)
  const result = await computeHighlight({ ...input, source })
  let previous = 0
  for (let index = 0; index < result!.spans.length; index += 3) {
    const from = result!.spans[index]!, to = result!.spans[index + 1]!
    expect(from).toBeGreaterThanOrEqual(previous)
    expect(to).toBeLessThanOrEqual(source.length)
    expect(source.slice(previous, from).replace(/\r?\n/g, '')).toBe('')
    expect(source.slice(from, to)).not.toContain('\n')
    previous = to
  }
  expect(previous).toBe(source.length)
})

it('keeps both themes and unsupported-language fallback and declines oversized documents', async () => {
  const source = 'const answer = 42'
  const [light, dark, plain, oversized] = await Promise.all([
    computeHighlight({ ...input, source }), computeHighlight({ ...input, source, theme: 'github-dark' }),
    computeHighlight({ ...input, source, language: 'unknown-synthetic-language' }),
    computeHighlight({ ...input, source: 'x'.repeat(1_000_001) }),
  ])
  expect(light!.styles).not.toEqual(dark!.styles)
  expect(plain!.spans).toEqual(new Uint32Array([0, source.length, 0]))
  expect(oversized).toBeUndefined()
})
