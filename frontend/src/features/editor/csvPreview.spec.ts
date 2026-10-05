import { describe, expect, it } from 'vitest'
import { parseCsvPreview } from './csvPreview'

describe('bounded CSV table preview', () => {
  it('handles quoted commas, escaped quotes, CRLF and embedded newlines without changing source', () => {
    const source = 'name,value,note\r\n"A, B",3,"line 1\r\nline 2"\r\n"say ""hi""",4,end\r\n'
    expect(parseCsvPreview(source)).toEqual({
      rows: [['name', 'value', 'note'], ['A, B', '3', 'line 1\r\nline 2'], ['say "hi"', '4', 'end']],
      truncated: false,
      invalid: false,
    })
    expect(source).toContain('"A, B"')
  })

  it('rejects malformed quoting and bounds preview size', () => {
    expect(parseCsvPreview('a"b,c\n').invalid).toBe(true)
    expect(parseCsvPreview('"unterminated').invalid).toBe(true)
    expect(parseCsvPreview(Array.from({ length: 205 }, (_, i) => String(i)).join('\n')).rows).toHaveLength(200)
    expect(parseCsvPreview(Array.from({ length: 205 }, (_, i) => String(i)).join('\n')).truncated).toBe(true)
    const wide = parseCsvPreview(Array.from({ length: 45 }, (_, i) => String(i)).join(','))
    expect(wide.rows[0]).toHaveLength(40)
    expect(wide.truncated).toBe(true)
    expect(parseCsvPreview('x'.repeat(300_000)).truncated).toBe(true)
  })
})
